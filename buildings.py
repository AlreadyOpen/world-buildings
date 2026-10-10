"""Every building in the world that has a height, with every field, as GeoParquet and as map tiles (Helen, 2026-10-10:
"keep all fields and all buildings with heights"; zoomed out, tallest first; all fields in the tiles too).

~204 M Overture buildings + ~1.4 M building parts have a height (8 % of 2.5 billion), ~26 GB of Parquet, so the world is split
into regions (consecutive Overture files, which Overture sorts by location) built in parallel CI jobs. Official sources replace
Overture inside their country and keep their own licence and files. Nothing is filtered out: implausible heights get a `check`
value ('floors' or 'wikidata') and stay in, tiled from zoom 13 only so a wrong 800 m spike does not stand in the skyline.

    python buildings.py plan [--target 6000000]   # out/plan.json: regions sized by Overture's own height counts; wikidata cache
    python buildings.py region <id>               # out/<id>/*.parquet (one per Overture file) + out/<id>.pmtiles
    python buildings.py official <cc>             # out/<cc>/<cc>.parquet + out/<cc>.pmtiles
SKY_DATA moves the data folder (default ./data next to this file)."""
import argparse, concurrent.futures as cf, json, os, subprocess, sys, threading, time
from pathlib import Path

import duckdb

import skylines as sk                                                    # release(), get_json, coverage, wikidata, OFFICIAL

DATA = Path(os.environ.get('SKY_DATA', Path(__file__).resolve().parent / 'data'))
CACHE, OUT = DATA / 'cache', DATA / 'out'
sk.CACHE, sk.OUT = CACHE, OUT
# Tallest first: the zoom from which a building is drawn, by height (m). Every building is in the tiles from zoom 13.
BANDS = [(150, 6), (80, 8), (40, 10), (20, 11), (10, 12)]
MAXZOOM = 13


def db():
    con = duckdb.connect()
    con.execute('LOAD spatial; LOAD httpfs')
    return con


def height_count(url):
    r = db().execute(f"""SELECT sum(num_values) - sum(stats_null_count) FROM parquet_metadata('{url}')
                         WHERE path_in_schema = 'height'""").fetchone()[0]
    return int(r or 0)


def plan(a):
    CACHE.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True)
    rel, files = sk.release()
    if a.files:                                                            # a probe: just these Overture files
        files = [f for f in files if f[0] in a.files.split(',')]
    with cf.ThreadPoolExecutor(16) as ex:
        counts = list(ex.map(lambda f: height_count(f[1]), files))
    regions, cur, n = [], [], 0
    for (fid, url), c in zip(files, counts):
        if fid.startswith('part-'):
            continue
        if cur and n + c > a.target:
            regions.append(cur); cur, n = [], 0
        cur.append([fid, url, c]); n += c
    regions.append(cur)
    p = {'release': rel, 'regions': {f'r{i:03d}': r for i, r in enumerate(regions) if r}}
    parts = [[fid, url, c] for (fid, url), c in zip(files, counts) if fid.startswith('part-')]
    if parts:
        p['regions']['parts'] = parts
    (OUT / 'plan.json').write_text(json.dumps(p))
    sk.wikidata()                                                          # once, shared with every region job
    print(json.dumps({'region': sorted(p['regions'])}))


def check_sql(table):
    """`check` per row: 'floors' (storey over 12 m) or 'wikidata' (300 m+ without a plausible storey), unless a Wikidata
    building or tower within 300 m agrees on the height. NULL = no doubt. Same rules as the skylines layer."""
    storey = '(height - coalesce(min_height, 0)) / nullif(num_floors, 0)'
    whole = "source = 'overture'"
    return f"""
    WITH c AS (SELECT id, height, {storey} > {sk.MAX_STOREY} AS bad_floors, ST_Centroid(geometry) AS p, source FROM {table}
               WHERE coalesce({storey} > {sk.MAX_STOREY}, false)
                  OR ({whole} AND height >= {sk.SUPERTALL} AND NOT coalesce({storey} BETWEEN 2.5 AND 6, false))),
         ok AS (SELECT DISTINCT c.id FROM c JOIN w ON abs(ST_Y(w.p) - ST_Y(c.p)) < 0.003 AND abs(ST_X(w.p) - ST_X(c.p)) < 0.004
                AND ST_Distance_Sphere(w.p, c.p) < 300
                AND CASE WHEN c.source = 'overture_part' THEN c.height <= w.h * 1.15 ELSE abs(c.height - w.h) / w.h <= 0.15 END)
    SELECT id, CASE WHEN bad_floors THEN 'floors' ELSE 'wikidata' END AS "check" FROM c WHERE id NOT IN (SELECT id FROM ok)"""


def minzoom_sql():
    cases = ' '.join(f'WHEN height >= {h} THEN {z}' for h, z in BANDS)
    return f'CASE WHEN "check" IS NOT NULL THEN {MAXZOOM} {cases} ELSE {MAXZOOM} END'


def tile(con, table, layer, out):
    """Stream rows from DuckDB into tippecanoe through a FIFO (no intermediate GeoJSON on disk). All fields go in; tippecanoe
    writes nested ones (names, sources) as JSON text. Zooms 6-13, tallest first by BANDS; if a tile is still too full, the
    smallest are dropped at low zooms and zooms extend past 13 until nothing is dropped (every building stays in)."""
    fifo = out.with_suffix('.fifo')
    fifo.unlink(missing_ok=True); os.mkfifo(fifo)
    tip = subprocess.Popen(['tippecanoe', '-o', str(out), '--force', '-l', layer, '-Z6', f'-z{MAXZOOM}',
                            '--order-descending-by=height', '--drop-smallest-as-needed', '--extend-zooms-if-still-dropping',
                            '-P', '--quiet', str(fifo)])
    cols = [r[0] for r in con.execute(f'DESCRIBE {table}').fetchall() if r[0] != 'geometry']
    props = ', '.join(f"'{c}': \"{c}\"" for c in cols)
    err = []
    def write():
        try:
            con.execute(f"""COPY (SELECT 'Feature' AS type, {{'minzoom': {minzoom_sql()}}} AS tippecanoe,
                                         ST_AsGeoJSON(geometry)::JSON AS geometry, {{{props}}} AS properties
                                  FROM {table}) TO '{fifo}' (FORMAT json)""")
        except Exception as e:
            err.append(e)
    t = threading.Thread(target=write); t.start(); t.join()
    rc = tip.wait(); fifo.unlink(missing_ok=True)
    if err or rc:
        sys.exit(f'tiling {layer} failed: {err or rc}')


def region(a):
    p = json.loads((OUT / 'plan.json').read_text()); files = p['regions'][a.id]
    cover = sk.coverage()
    d = OUT / a.id; d.mkdir(parents=True, exist_ok=True)
    con = db()
    con.execute(f"CREATE TABLE w AS SELECT DISTINCT h, ST_GeomFromText(coord) p FROM read_csv('{sk.wikidata()}')")
    src = 'overture_part' if a.id == 'parts' else 'overture'
    t0 = time.time()
    for fid, url, _ in files:
        out = d / f'{fid}.parquet'
        if out.exists():
            continue
        skip = f"AND NOT ST_Intersects(ST_Centroid(geometry), ST_GeomFromText('{cover}'))" if cover else ''
        con.execute(f"""CREATE OR REPLACE TABLE f AS SELECT *, '{src}' AS source FROM read_parquet('{url}')
                        WHERE height IS NOT NULL {skip}""")
        con.execute(f'CREATE OR REPLACE TABLE k AS {check_sql("f")}')
        tmp = out.with_suffix('.part')
        con.execute(f"""COPY (SELECT f.*, k."check" FROM f LEFT JOIN k USING (id)) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)""")
        tmp.rename(out)
        print(f'{fid} {con.execute("SELECT count(*) FROM f").fetchone()[0]} ({time.time() - t0:.0f} s)', flush=True)
    con.execute(f"CREATE OR REPLACE VIEW r AS SELECT * FROM read_parquet('{d}/*.parquet')")
    tile(con, 'r', 'buildings', OUT / f'{a.id}.pmtiles')
    s = con.execute('SELECT count(*), count("check"), min(bbox.xmin), min(bbox.ymin), max(bbox.xmax), max(bbox.ymax) FROM r').fetchone()
    (OUT / f'{a.id}.json').write_text(json.dumps({'region': a.id, 'rows': s[0], 'checked': s[1], 'bbox': s[2:],
                                                   'files': [f[0] for f in files], 'release': p['release']}))
    print(json.dumps({'region': a.id, 'rows': s[0], 'checked': s[1], 'seconds': round(time.time() - t0)}))


def official(a):
    """An official source's buildings that have a height, every field, under its own licence (no checks: taken as published)."""
    cc = a.cc.upper(); s = sk.OFFICIAL[cc]
    if 'parquet' not in s:
        sys.exit(f'{cc}: only Parquet sources are wired for all-buildings so far')
    d = OUT / cc.lower(); d.mkdir(parents=True, exist_ok=True)
    con = db()
    con.execute(f"""CREATE TABLE r AS SELECT *, {s['height']} AS height, 0.0 AS min_height, NULL::VARCHAR AS "check"
                    FROM read_parquet('{s['parquet']}') WHERE {s['height']} > 0""")
    con.execute(f"COPY r TO '{d}/{cc.lower()}.parquet' (FORMAT parquet, COMPRESSION zstd)")
    tile(con, 'r', 'buildings', OUT / f'{cc.lower()}.pmtiles')
    print(json.dumps({'official': cc, 'rows': con.execute('SELECT count(*) FROM r').fetchone()[0]}))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); s = ap.add_subparsers(dest='cmd', required=True)
    pl = s.add_parser('plan'); pl.add_argument('--target', type=int, default=15_000_000); pl.add_argument('--files')
    s.add_parser('region').add_argument('id')
    s.add_parser('official').add_argument('cc')
    a = ap.parse_args()
    duckdb.connect().execute('INSTALL spatial; INSTALL httpfs')
    {'plan': plan, 'region': region, 'official': official}[a.cmd](a)
