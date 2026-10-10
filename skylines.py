"""World skylines: tall buildings with heights for the zooms where Overture's own tiles carry none (z6-13).

Official building sources win inside their country; Overture fills the rest. Usage:
    python skylines.py extract [--min-height 30] [--files 00058,00377]   # out/parts/<file>.parquet, resumable
    python skylines.py build                                              # out/<source>.parquet + <source>.pmtiles + sources.json
    python skylines.py check                                              # fails if Overture is left inside official coverage
    python skylines.py latest                                             # prints the newest Overture release name
SKY_DATA moves the data folder (default ./data next to this file).
"""
import argparse, concurrent.futures as cf, hashlib, json, os, subprocess, sys, time, urllib.request
from pathlib import Path

import duckdb

DATA = Path(os.environ.get('SKY_DATA', Path(__file__).resolve().parent / 'data'))
CACHE, OUT = DATA / 'cache', DATA / 'out'
STAC = 'https://stac.overturemaps.org/catalog.json'
GISCO = 'https://gisco-services.ec.europa.eu/distribution/v2/countries/geojson/CNTR_RG_01M_2020_4326.geojson'
COLS = 'id VARCHAR, height DOUBLE, min_height DOUBLE, name VARCHAR, source VARCHAR, geometry GEOMETRY'

# Official building sources, by GISCO country id. They replace Overture inside that country.
# One without a parquet URL yet is skipped, and Overture stays there until it is published.
OFFICIAL = {
    'HK': {'title': 'Lands Department buildings, via AlreadyOpen hk-buildings', 'parquet': None,
           'select': "BuildingCSUID, height_m, 0, BuildingNameEN, 'landsd', geometry",
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
    out = OUT / 'parts' / f'{fid}.parquet'
    if out.exists():
        return fid, 'kept'
    tmp = out.with_suffix('.part')
    db().execute(f"""COPY (SELECT id, height, coalesce(min_height, 0) AS min_height, names.primary AS name,
                                  'overture' AS source, geometry
                           FROM read_parquet('{url}') WHERE height >= {min_h})
                     TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)""")
    tmp.rename(out)  # only finished files count, so a failed run resumes where it stopped
    return fid, db().execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]


def extract(a):
    CACHE.mkdir(parents=True, exist_ok=True); (OUT / 'parts').mkdir(parents=True, exist_ok=True)
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


def build(a):
    """One Parquet + one PMTiles per source, never merged: Overture is ODbL (share-alike) and official sources keep their own terms."""
    meta = json.loads((OUT / 'release.json').read_text())
    con = db()
    # Parts hold all of Overture; official countries are cut here, so cached parts stay valid when sources change.
    cover = coverage()
    skip = f"WHERE NOT ST_Intersects(ST_Centroid(geometry), ST_GeomFromText('{cover}'))" if cover else ''
    groups = {'overture': f"SELECT * FROM read_parquet('{OUT}/parts/*.parquet') {skip}"}
    for cc, s in OFFICIAL.items():
        if s['parquet']:
            groups[cc.lower()] = f"SELECT {s['select']} FROM read_parquet('{s['parquet']}') WHERE height_m >= {meta['min_height']}"
    outputs, rows = {}, {}
    for name, q in groups.items():
        con.execute(f'CREATE OR REPLACE TABLE b ({COLS})'); con.execute(f'INSERT INTO b {q}')
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
