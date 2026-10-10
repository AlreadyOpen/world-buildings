"""World skylines: tall buildings with heights for the zooms where Overture's own tiles carry none (z6-13).

Official building sources win inside their country; Overture fills the rest. Usage:
    python skylines.py extract [--min-height 30] [--files 00058,00377]   # out/parts/<file>.parquet, resumable
    python skylines.py build                                              # out/<source>.parquet + <source>.pmtiles + sources.json
    python skylines.py check                                              # fails if Overture is left inside official coverage
    python skylines.py latest                                             # prints the newest Overture release name
SKY_DATA moves the data folder (default ./data next to this file).
"""
import argparse, concurrent.futures as cf, hashlib, json, os, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

import duckdb

DATA = Path(os.environ.get('SKY_DATA', Path(__file__).resolve().parent / 'data'))
CACHE, OUT = DATA / 'cache', DATA / 'out'
STAC = 'https://stac.overturemaps.org/catalog.json'
GISCO = 'https://gisco-services.ec.europa.eu/distribution/v2/countries/geojson/CNTR_RG_01M_2020_4326.geojson'
COLS = 'id VARCHAR, height DOUBLE, min_height DOUBLE, name VARCHAR, source VARCHAR, num_floors INTEGER, geometry GEOMETRY'
PARTS = OUT / 'parts-v2'  # bump when the extract's columns change (the CI cache is keyed on this name too)
WIKIDATA = 'https://query.wikidata.org/sparql'
# Buildings and towers with a height and coordinates, for checking supertall heights. Classes listed (no subclass walk):
# skyscraper, high-rise, building, tower, office building, hotel, TV tower, observation tower, church building, cathedral,
# chimney, mosque, residential skyscraper, ..., bell tower. The full "anything with a height" query times out.
WD_CLASSES = ('Q11303 Q18142 Q41176 Q12518 Q1021645 Q27686 Q11166728 Q1440476 Q16970 Q2977 Q2962545 Q32815 Q13402009 '
              'Q1307276 Q15848826 Q2088357').split()
WD_QUERY = '''SELECT DISTINCT ?item ?h ?coord WHERE {{ ?item wdt:P31 wd:{c} ;
  p:P2048/psv:P2048/wikibase:quantityNormalized ?n ; wdt:P625 ?coord .
  ?n wikibase:quantityAmount ?h ; wikibase:quantityUnit wd:Q11573 . FILTER(?h >= 100 && ?h < 1000) }}'''
SUPERTALL, MAX_STOREY = 300, 8  # metres: >= SUPERTALL needs Wikidata or a 2.5-6 m storey; height/num_floors > MAX_STOREY is a bad height

# Official building sources, by GISCO country id. They replace Overture inside that country.
# One without a parquet URL yet is skipped, and Overture stays there until it is published.
OFFICIAL = {
    'HK': {'title': 'Lands Department buildings, via AlreadyOpen hk-buildings',
           'parquet': 'https://huggingface.co/datasets/alreadyopen/hk-buildings/resolve/main/buildings.parquet',
           'select': "BuildingCSUID, height_m, 0, BuildingNameEN, 'landsd', Storeys, geometry",
           'licence': 'CSDI Terms of Use https://portal.csdi.gov.hk/csdi-webpage/doc/TNC',
           'attribution': 'Government of the HKSAR, CSDI Portal; Lands Department'},
}


def db():
    con = duckdb.connect()
    con.execute('LOAD spatial; LOAD httpfs')  # installed once in __main__: parallel INSTALLs race on a fresh machine
    return con


def get_json(url, tries=4):
    for n in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except OSError:  # DNS blips and resets under 16 parallel requests
            if n == tries - 1:
                raise
            time.sleep(2 ** n)


def release():
    """Newest Overture release and its building files (each release is deleted ~60 days after it is published)."""
    rel = sorted(l['href'] for l in get_json(STAC)['links'] if l['rel'] == 'child')[-1]
    coll = rel.replace('catalog.json', 'buildings/building/collection.json')
    items = [l['href'] for l in get_json(coll)['links'] if l['rel'] == 'item']
    with cf.ThreadPoolExecutor(16) as ex:
        files = sorted((i['id'], i['assets']['aws']['href']) for i in ex.map(get_json, items))
    return rel.rstrip('/').split('/')[-2], files


def coverage():
    """WKT of the countries whose official source is published, or None."""
    live = [cc for cc, s in OFFICIAL.items() if s['parquet']]
    if not live:
        return None
    p = CACHE / 'gisco-countries-01m.geojson'
    if not p.exists():
        urllib.request.urlretrieve(GISCO, p)
    ids = ','.join(f"'{c}'" for c in live)
    return db().execute(f"SELECT ST_AsText(ST_Union_Agg(geom)) FROM ST_Read('{p}') WHERE CNTR_ID IN ({ids})").fetchone()[0]


def extract_one(fid, url, min_h):
    out = PARTS / f'{fid}.parquet'
    if out.exists():
        return fid, 'kept'
    tmp = out.with_suffix('.part')
    db().execute(f"""COPY (SELECT id, height, coalesce(min_height, 0) AS min_height, names.primary AS name,
                                  'overture' AS source, num_floors, geometry
                           FROM read_parquet('{url}') WHERE height >= {min_h})
                     TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)""")
    tmp.rename(out)  # only finished files count, so a failed run resumes where it stopped
    return fid, db().execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]


def extract(a):
    CACHE.mkdir(parents=True, exist_ok=True); PARTS.mkdir(parents=True, exist_ok=True)
    rel, files = release()
    if a.files:
        files = [f for f in files if f[0] in a.files.split(',')]
    (OUT / 'release.json').write_text(json.dumps({'release': rel, 'min_height': a.min_height, 'files': len(files)}))
    t = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for fid, n in ex.map(lambda f: extract_one(*f, a.min_height), files):
            print(f'{fid} {n} ({time.time() - t:.0f} s)', flush=True)


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def wikidata():
    """Tall buildings and towers from Wikidata, one query per class: a single query times out on CI runners."""
    p = CACHE / 'wikidata-tall.csv'
    if p.exists():
        return p
    rows = ['item,h,coord']
    for c in WD_CLASSES:
        url = WIKIDATA + '?' + urllib.parse.urlencode({'query': WD_QUERY.format(c=c)})
        hdr = {'Accept': 'text/csv', 'User-Agent': 'AlreadyOpen-world-skylines (https://github.com/AlreadyOpen/world-skylines)'}
        for n in range(4):
            body = urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=120).read().decode()
            if 'Exception' not in body:  # a WDQS timeout still answers 200: partial rows, then a Java stack trace
                break
            time.sleep(10 * (n + 1))
        else:
            sys.exit(f'Wikidata query for {c} kept timing out')
        rows += body.strip().splitlines()[1:]
    if len(rows) < 1000:
        sys.exit(f'Wikidata gave only {len(rows)} rows')
    p.write_text('\n'.join(rows) + '\n')
    return p


def reject(con):
    """Move implausible Overture heights from table b to out/rejected.parquet, with the reason."""
    con.execute(f"CREATE OR REPLACE TABLE w AS SELECT DISTINCT h, ST_GeomFromText(coord) p FROM read_csv('{wikidata()}')")
    # Suspect: a storey over MAX_STOREY m, or SUPERTALL+ without a plausible 2.5-6 m storey (Wikidata misses many real
    # supertalls, e.g. Central Park Tower). A matching Wikidata height clears either (the floor count may be the wrong field).
    con.execute(f"""CREATE OR REPLACE TABLE r AS
        SELECT b.id, CASE WHEN b.height / nullif(b.num_floors, 0) > {MAX_STOREY} THEN 'floors' ELSE 'wikidata' END AS reason
        FROM b WHERE (coalesce(b.height / nullif(b.num_floors, 0) > {MAX_STOREY}, false)
                      OR (b.height >= {SUPERTALL} AND NOT coalesce(b.height / nullif(b.num_floors, 0) BETWEEN 2.5 AND 6, false)))
          AND NOT EXISTS (SELECT 1 FROM w WHERE abs(ST_Y(w.p) - ST_Y(ST_Centroid(b.geometry))) < 0.003
                            AND abs(ST_X(w.p) - ST_X(ST_Centroid(b.geometry))) < 0.004
                            AND ST_Distance_Sphere(w.p, ST_Centroid(b.geometry)) < 300 AND abs(b.height - w.h) / w.h <= 0.15)""")
    con.execute(f"COPY (SELECT b.*, r.reason FROM b JOIN r USING (id) ORDER BY height DESC) TO '{OUT}/rejected.parquet' (FORMAT parquet, COMPRESSION zstd)")
    con.execute('DELETE FROM b WHERE id IN (SELECT id FROM r)')
    return dict(con.execute('SELECT reason, count(*) FROM r GROUP BY 1').fetchall())


def build(a):
    """One Parquet + one PMTiles per source, never merged: Overture is ODbL (share-alike) and official sources keep their own terms."""
    meta = json.loads((OUT / 'release.json').read_text())
    con = db()
    # Parts hold all of Overture; official countries are cut here, so cached parts stay valid when sources change.
    cover = coverage()
    skip = f"WHERE NOT ST_Intersects(ST_Centroid(geometry), ST_GeomFromText('{cover}'))" if cover else ''
    groups = {'overture': f"SELECT * FROM read_parquet('{PARTS}/*.parquet') {skip}"}
    for cc, s in OFFICIAL.items():
        if s['parquet']:
            groups[cc.lower()] = f"SELECT {s['select']} FROM read_parquet('{s['parquet']}') WHERE height_m >= {meta['min_height']}"
    outputs, rows = {}, {}
    for name, q in groups.items():
        con.execute(f'CREATE OR REPLACE TABLE b ({COLS})'); con.execute(f'INSERT INTO b {q}')
        if name == 'overture':  # official sources are taken as published; Overture's heights are checked
            rows['rejected'] = reject(con)
            outputs['rejected.parquet'] = {'bytes': (OUT / 'rejected.parquet').stat().st_size, 'sha256': sha256(OUT / 'rejected.parquet')}
        table, pm, seq = OUT / f'{name}.parquet', OUT / f'{name}.pmtiles', CACHE / f'{name}.geojsonl'
        con.execute(f"COPY (SELECT * FROM b ORDER BY height DESC) TO '{table}' (FORMAT parquet, COMPRESSION zstd)")
        con.execute(f"COPY (SELECT height, min_height, name, geometry FROM b) TO '{seq}' WITH (FORMAT gdal, DRIVER 'GeoJSONSeq')")
        # Tallest first, so a full tile drops small buildings, never the skyline. Options exist in tippecanoe 2.49 (Ubuntu 24.04).
        subprocess.run(['tippecanoe', '-o', str(pm), '--force', '-l', 'skylines', '-Z6', '-z13',
                        '--order-descending-by=height', '--drop-smallest-as-needed', '--extend-zooms-if-still-dropping',
                        '--quiet', str(seq)], check=True)
        rows[name] = con.execute('SELECT count(*) FROM b').fetchone()[0]
        outputs.update({p.name: {'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in (table, pm)})
    sources = {
        'overture': {'release': meta['release'], 'theme': 'buildings/building', 'min_height_m': meta['min_height'],
                     'files': meta['files'], 'licence': 'ODbL-1.0',
                     'attribution': 'Overture Maps Foundation; (c) OpenStreetMap contributors'},
        'official': {cc.lower(): {k: v for k, v in s.items() if k != 'select'} for cc, s in OFFICIAL.items() if s['parquet']},
        'rows': rows, 'outputs': outputs,
    }
    (OUT / 'sources.json').write_text(json.dumps(sources, indent=1))
    print(json.dumps({'rows': rows, 'outputs': outputs}))


def latest(a):
    print(sorted(l['href'] for l in get_json(STAC)['links'] if l['rel'] == 'child')[-1].rstrip('/').split('/')[-2])


def check(a):
    cover = coverage()
    if not cover:
        print('no official source published yet: nothing to check')
        return
    n = db().execute(f"""SELECT count(*) FROM read_parquet('{OUT}/overture.parquet')
                         WHERE ST_Intersects(ST_Centroid(geometry), ST_GeomFromText('{cover}'))""").fetchone()[0]
    print(f'Overture buildings inside official coverage: {n}')
    sys.exit(1 if n else 0)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest='cmd', required=True)
    e = s.add_parser('extract')
    e.add_argument('--min-height', type=float, default=30)
    e.add_argument('--files')
    e.add_argument('--workers', type=int, default=4)
    s.add_parser('build'); s.add_parser('check'); s.add_parser('latest')
    a = p.parse_args()
    duckdb.connect().execute('INSTALL spatial; INSTALL httpfs')
    {'extract': extract, 'build': build, 'check': check, 'latest': latest}[a.cmd](a)
