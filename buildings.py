"""Every building in the world that has a height, with every field, as GeoParquet and as map tiles (Helen, 2026-10-10:
"keep all fields and all buildings with heights"; zoomed out, tallest first; all fields in the tiles too).

~204 M Overture buildings + ~1.4 M building parts have a height (8 % of 2.5 billion), ~26 GB of Parquet, so the world is split
into regions (consecutive Overture files, which Overture sorts by location) built in parallel CI jobs. Official sources replace
Overture inside their country and keep their own licence and files. Nothing is filtered out: implausible heights get a `check`
value ('floors' or 'wikidata') and stay in, tiled from zoom 13 only so a wrong 800 m spike does not stand in the skyline.

    python buildings.py plan [--target 6000000]   # out/plan.json: regions sized by Overture's own height counts; wikidata cache
    python buildings.py region <id>               # out/<id>/*.parquet (one per Overture file) + out/<id>.pmtiles
    python buildings.py official <cc>             # out/<cc>/<cc>.parquet + out/<cc>.pmtiles
    python buildings.py region nl                 # every 3DBAG building, every layer: out/nl/<layer>-<batch>.parquet + out/nl.pmtiles
    python buildings.py plan --regions nl         # only these regions, added to the release already published (index.json)
SKY_DATA moves the data folder (default ./data next to this file)."""
import argparse, concurrent.futures as cf, gzip, hashlib, json, os, shutil, subprocess, sys, threading, time, urllib.request
from pathlib import Path

import duckdb

import skylines as sk                                                    # release(), get_json, coverage, wikidata, OFFICIAL

DATA = Path(os.environ.get('SKY_DATA', Path(__file__).resolve().parent / 'data'))
CACHE, OUT = DATA / 'cache', DATA / 'out'
sk.CACHE, sk.OUT = CACHE, OUT
# Tallest first: the zoom from which a building is drawn, by height (m). Every building is in the tiles from zoom 13.
# Official sources wired for every building (a Parquet of all of them). Only these replace Overture: NL's official layer is
# still the >= 30 m WFS subset, so Overture keeps the Netherlands until all of 3DBAG is in.
ALL_OFFICIAL = [cc for cc, s in sk.OFFICIAL.items() if 'parquet' in s]
# Regions with a builder of their own (not a Parquet official source). They add to Overture, never cut it: Helen chose
# (2026-10-11) to draw both Overture and 3DBAG in the Netherlands.
OWN = ['nl']
BAG_INDEX = 'https://data.3dbag.nl/latest/tile_index.fgb'          # 3DBAG's tile index: one GeoPackage per tile, sha256 each
BAG_LAYERS = ['pand', 'lod12_2d', 'lod13_2d', 'lod22_2d', 'lod12_3d', 'lod13_3d', 'lod22_3d']
BAG_BATCH = 400                                                    # tiles per output Parquet (~23 batches for ~8,900 tiles)
BANDS = [(150, 6), (80, 8), (40, 10), (20, 11), (10, 12)]
MAXZOOM = 13


def db():
    con = duckdb.connect()
    con.execute('LOAD spatial; LOAD httpfs')
    # A 16 GB runner died ("lost communication") on two 15 M-row regions on 2026-10-10: cap DuckDB so tippecanoe, which runs
    # alongside it, keeps room; DuckDB spills to disk past the cap. SKY_DUCKDB_MEM overrides.
    con.execute(f"SET memory_limit = '{os.environ.get('SKY_DUCKDB_MEM', '6GB')}'; SET threads = 2; SET preserve_insertion_order = false")
    return con


def height_count(url):
    r = db().execute(f"""SELECT sum(num_values) - sum(stats_null_count) FROM parquet_metadata('{url}')
                         WHERE path_in_schema = 'height'""").fetchone()[0]
    return int(r or 0)


def plan(a):
    CACHE.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True)
    if a.regions:                    # add regions to the release already published, keeping every region it lists
        idx = sk.get_json('https://huggingface.co/buckets/alreadyopen/world-buildings/resolve/index.json')
        own = {cc.lower() for cc in ALL_OFFICIAL} | set(OWN)
        p = {'release': idx['release'], 'regions': {r['id']: [] for r in idx['regions'] if r['id'] not in own}}
        (OUT / 'plan.json').write_text(json.dumps(p))
        print(json.dumps({'region': a.regions.split(',')}))
        return
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
    print(json.dumps({'region': sorted(p['regions']) + [cc.lower() for cc in ALL_OFFICIAL] + OWN}))


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
    if a.id == 'nl':
        return bag(a)
    if a.id.upper() in ALL_OFFICIAL:                                      # an official source is a region of its own
        a.cc = a.id; return official(a)
    p = json.loads((OUT / 'plan.json').read_text()); files = p['regions'][a.id]
    cover = sk.coverage(ALL_OFFICIAL)
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
    s = con.execute('SELECT n, ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) '
                    'FROM (SELECT count(*) n, ST_Extent_Agg(geometry) e FROM r)').fetchone()
    (OUT / f'{cc.lower()}.json').write_text(json.dumps({'region': cc.lower(), 'rows': s[0], 'checked': 0, 'bbox': s[1:]}))
    print(json.dumps({'official': cc, 'rows': s[0]}))


def bag_fetch(t, d):
    """one 3DBAG tile: download its GeoPackage, check the sha256 3DBAG publishes, unpack. Returns the .gpkg path."""
    tid, url, sha = t
    gz, gp = d / (tid.replace('/', '-') + '.gpkg.gz'), d / (tid.replace('/', '-') + '.gpkg')
    for n in range(5):
        try:
            urllib.request.urlretrieve(url, gz)
            if hashlib.sha256(gz.read_bytes()).hexdigest() == sha:
                break
            err = 'sha256 mismatch'
        except OSError as e:
            err = e
        time.sleep(2 ** n)
    else:
        raise SystemExit(f'3DBAG tile {tid}: {err}')
    with gzip.open(gz) as f, open(gp, 'wb') as o:
        shutil.copyfileobj(f, o)
    gz.unlink()
    return gp


def bag(a):
    """Every 3DBAG building, every layer and every field, from its per-tile GeoPackages (the 19.6 GB whole-country zip does
    not fit a runner unpacked; tiles stream in batches, each with a published sha256). Geometry goes RD New -> WGS84 (z stays
    NAP metres); `tile_id` + `fid` keep 3DBAG's per-tile row key, which GDAL hides, read from SQLite and checked row by row.
    Tiles: the LoD1.2 roof parts (3DBAG: a LoD1.2 model is its 2D parts extruded from b3_h_maaiveld) with every pand field,
    height = b3_h_70p - b3_h_maaiveld, as world-skylines' nl layer did."""
    d = OUT / 'nl'; tmp = DATA / 'bag-tmp'; d.mkdir(parents=True, exist_ok=True); tmp.mkdir(parents=True, exist_ok=True)
    con = db(); con.execute('INSTALL sqlite; LOAD sqlite')
    tiles = con.execute(f"SELECT tile_id, gpkg_download, gpkg_sha256 FROM ST_Read('/vsicurl/{BAG_INDEX}') ORDER BY tile_id").fetchall()
    if os.environ.get('BAG_TILES'):                                       # a probe: just these tiles
        tiles = [t for t in tiles if t[0] in os.environ['BAG_TILES'].split(',')]
    version = tiles[0][1].split('/')[3]                                    # https://data.3dbag.nl/v20250903/tiles/...
    t0 = time.time()
    for b in range(0, len(tiles), BAG_BATCH):
        outs = {l: d / f'{l}-{b // BAG_BATCH:03d}.parquet' for l in BAG_LAYERS}
        if all(o.exists() for o in outs.values()):
            continue
        with cf.ThreadPoolExecutor(8) as ex:
            gps = list(ex.map(lambda t: bag_fetch(t, tmp), tiles[b:b + BAG_BATCH]))
        for l in BAG_LAYERS:
            con.execute(f'DROP TABLE IF EXISTS {l}')
        for (tid, _, _), gp in zip(tiles[b:b + BAG_BATCH], gps):
            for l in BAG_LAYERS:
                sel = f"""SELECT '{tid}' AS tile_id, s.fid, s.sid, r.* EXCLUDE (rn)
                            REPLACE (ST_Transform(r.geom, 'EPSG:28992', 'EPSG:4326', always_xy := true) AS geom)
                          FROM (SELECT *, row_number() OVER () rn FROM ST_Read('{gp}', layer='{l}')) r
                          JOIN (SELECT fid, identificatie AS sid, row_number() OVER (ORDER BY fid) rn FROM sqlite_scan('{gp}', '{l}')) s USING (rn)"""
                if con.execute(f"SELECT count(*) FROM duckdb_tables() WHERE table_name = '{l}'").fetchone()[0]:
                    con.execute(f'INSERT INTO {l} BY NAME {sel}')
                else:
                    con.execute(f'CREATE TABLE {l} AS {sel}')
            gp.unlink()
        for l, o in outs.items():
            bad = con.execute(f'SELECT count(*) FROM {l} WHERE sid IS DISTINCT FROM identificatie').fetchone()[0]
            if bad:
                sys.exit(f'3DBAG {l}: {bad} rows where the SQLite fid order and GDAL order disagree')
            con.execute(f"COPY (SELECT * EXCLUDE (sid) FROM {l}) TO '{o.with_suffix('.part')}' (FORMAT parquet, COMPRESSION zstd)")
            o.with_suffix('.part').rename(o)
            con.execute(f'DROP TABLE {l}')
        print(f'3DBAG batch {b // BAG_BATCH}: tiles {b}-{b + len(gps) - 1} of {len(tiles)} ({time.time() - t0:.0f} s)', flush=True)
    for l in BAG_LAYERS:
        con.execute(f"CREATE OR REPLACE VIEW {l} AS SELECT * FROM read_parquet('{d}/{l}-*.parquet')")
    # one feature per LoD1.2 roof part, with its building's fields; a building without a LoD1.2 part (none expected) is
    # tiled from its BAG footprint without a height, so every building is still in the tiles
    con.execute("""CREATE OR REPLACE VIEW r AS
        SELECT ST_Force2D(l.geom) AS geometry, l.b3_h_70p - p.b3_h_maaiveld AS height, 0.0 AS min_height, NULL::VARCHAR AS "check",
               '3dbag' AS source, l.* EXCLUDE (geom), p.* EXCLUDE (geom, identificatie, tile_id, fid), p.fid AS pand_fid
        FROM lod12_2d l JOIN pand p USING (identificatie)
        UNION ALL BY NAME
        SELECT ST_Force2D(geom) AS geometry, NULL::DOUBLE AS height, 0.0 AS min_height, NULL::VARCHAR AS "check", '3dbag' AS source,
               * EXCLUDE (geom, fid), fid AS pand_fid
        FROM pand WHERE identificatie NOT IN (SELECT identificatie FROM lod12_2d)""")
    tile(con, 'r', 'buildings', OUT / 'nl.pmtiles')
    s = con.execute("""SELECT n, nd, ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)
                       FROM (SELECT count(*) n, count(DISTINCT identificatie) nd, ST_Extent_Agg(geom) e FROM pand)""").fetchone()
    layers = {l: con.execute(f'SELECT count(*) FROM {l}').fetchone()[0] for l in BAG_LAYERS}
    no_part = con.execute('SELECT count(*) FROM pand WHERE identificatie NOT IN (SELECT identificatie FROM lod12_2d)').fetchone()[0]
    # every building must be in the deepest zoom of the tiles
    with open(OUT / 'nl.pmtiles', 'rb') as f:
        mz = f.read(127)[101]                                              # PMTiles v3 header: max zoom at byte 101
    got = int(subprocess.run(f"tippecanoe-decode -Z{mz} -z{mz} -l buildings '{OUT / 'nl.pmtiles'}' | "
                             "grep -oE '\"identificatie\": \"[^\"]*\"' | sort -u -S 1G -T '" + str(tmp) + "' | wc -l",
                             shell=True, check=True, capture_output=True, text=True).stdout)
    meta = {'region': 'nl', 'source': '3dbag', 'rows': s[0], 'distinct_ids': s[1], 'checked': 0, 'bbox': list(s[2:]),
            'layers': layers, 'without_lod12': no_part, 'tiles_maxzoom': mz, 'ids_at_maxzoom': got,
            '3dbag_version': version, '3dbag_tiles': len(tiles)}
    (OUT / 'nl.json').write_text(json.dumps(meta))
    print(json.dumps({**meta, 'seconds': round(time.time() - t0)}))
    if got != s[1]:
        sys.exit(f'3DBAG tiles: {got} buildings at z{mz}, expected {s[1]}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); s = ap.add_subparsers(dest='cmd', required=True)
    pl = s.add_parser('plan'); pl.add_argument('--target', type=int, default=15_000_000); pl.add_argument('--files'); pl.add_argument('--regions')
    s.add_parser('region').add_argument('id')
    s.add_parser('official').add_argument('cc')
    a = ap.parse_args()
    duckdb.connect().execute('INSTALL spatial; INSTALL httpfs')
    {'plan': plan, 'region': region, 'official': official}[a.cmd](a)
