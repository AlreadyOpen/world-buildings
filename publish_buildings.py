"""Publish world-buildings (buildings.py) to Hugging Face. Needs HF_TOKEN (alreadyopen-ci).
  region <id>   after `buildings.py region|official <id>`: uploads that region under a versioned path, so nothing live changes
                dataset alreadyopen/world-buildings  v/<release>/<id>/*.parquet (every field, one file per Overture file)
                bucket  alreadyopen/world-buildings  v/<release>/<id>.pmtiles (layer `buildings`)
                writes out/<id>.pub.json (counts, bbox, sha256) for finalize
  finalize      with every out/*.pub.json: gate (manifest.py), then index.json + manifest.json at the root of both, last.
                /map reads index.json: {release, layer, regions: [{id, source, bbox, rows, tiles}]}.
One region per source licence: Overture regions are ODbL, `hk` is CSDI terms; never merged."""
import json, os, sys
from pathlib import Path
from huggingface_hub import HfApi, batch_bucket_files, create_bucket
import manifest

REPO = 'alreadyopen/world-buildings'
OUT = Path(os.environ.get('SKY_DATA', Path(__file__).resolve().parent / 'data')) / 'out'
BUCKET = f'https://huggingface.co/buckets/{REPO}/resolve'


def release():
    return json.loads((OUT / 'plan.json').read_text())['release']


def region(rid):
    rel, api = release(), HfApi()
    api.create_repo(REPO, repo_type='dataset', exist_ok=True); create_bucket(REPO, private=False, exist_ok=True)
    meta = json.loads((OUT / f'{rid}.json').read_text()) if (OUT / f'{rid}.json').exists() else {}
    parquet = sorted((OUT / rid).glob('*.parquet'))
    tiles = OUT / f'{rid}.pmtiles'
    files = {f'v/{rel}/{rid}/{p.name}': p for p in parquet} | {f'v/{rel}/{rid}.pmtiles': tiles}
    api.upload_folder(folder_path=str(OUT / rid), path_in_repo=f'v/{rel}/{rid}', repo_id=REPO, repo_type='dataset',
                      allow_patterns='*.parquet', commit_message=f'{rid}: Overture {rel}')
    batch_bucket_files(REPO, add=[(str(tiles), f'v/{rel}/{rid}.pmtiles')])
    pub = {'id': rid, 'source': 'hk' if rid == 'hk' else 'overture_part' if rid == 'parts' else 'overture',
           'rows': meta.get('rows'), 'checked': meta.get('checked'), 'bbox': meta.get('bbox'),
           'tiles': f'{BUCKET}/v/{rel}/{rid}.pmtiles',
           'files': {k: {'sha256': manifest.sha256(p), 'bytes': p.stat().st_size} for k, p in files.items()}}
    (OUT / f'{rid}.pub.json').write_text(json.dumps(pub))
    print(json.dumps({k: pub[k] for k in ('id', 'rows', 'tiles')}))


def finalize():
    rel = release()
    regs = [json.loads(p.read_text()) for p in sorted(OUT.glob('*.pub.json'))]
    counts = {}
    for r in regs:
        counts[r['source']] = counts.get(r['source'], 0) + (r['rows'] or 0)
    m = {'built': rel, 'counts': counts, 'files': {k: v for r in regs for k, v in r['files'].items()},
         'sources': json.load(open(Path(__file__).resolve().parent / 'sources.json'))}
    # the gate compares counts per source; file paths are versioned, so only counts are compared across releases
    old = manifest.published(f'{BUCKET}/manifest.json')
    p = manifest.problems(m, old and {**old, 'files': {}})
    if p and os.environ.get('PUBLISH_FORCE') != '1':
        sys.exit('publish gate: not publishing (PUBLISH_FORCE=1 overrides):\n  ' + '\n  '.join(p))
    index = {'release': rel, 'layer': 'buildings',
             'regions': [{k: r[k] for k in ('id', 'source', 'bbox', 'rows', 'checked', 'tiles')} for r in regs]}
    (OUT / 'index.json').write_text(json.dumps(index, indent=1)); (OUT / 'manifest.json').write_text(json.dumps(m, indent=1))
    api = HfApi()
    for name, path in [('README.md', Path(__file__).resolve().parent / 'hf' / 'world-buildings.md'),
                       ('index.json', OUT / 'index.json'), ('manifest.json', OUT / 'manifest.json')]:
        api.upload_file(path_or_fileobj=str(path), path_in_repo=name, repo_id=REPO, repo_type='dataset', commit_message=f'{name}: {rel}')
    batch_bucket_files(REPO, add=[(str(OUT / 'index.json'), 'index.json')])
    batch_bucket_files(REPO, add=[(str(OUT / 'manifest.json'), 'manifest.json')])   # last: the gate reads this copy
    print(json.dumps({'release': rel, 'counts': counts, 'regions': len(regs), 'index': f'{BUCKET}/index.json'}))


if __name__ == '__main__':
    {'region': lambda: region(sys.argv[2]), 'finalize': finalize}[sys.argv[1]]()
