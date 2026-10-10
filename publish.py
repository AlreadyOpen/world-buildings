"""Publish a build to Hugging Face (needs HF_TOKEN with write access to the alreadyopen org):
  dataset alreadyopen/world-skylines  <source>.parquet (buildings >= the height threshold, with footprints), rejected.parquet, card
  bucket  alreadyopen/world-skylines  <source>.pmtiles (z6-13, layer `skylines`), read in place by MapLibre
  manifest.json (both): release, row counts per file, sha256 of every file, sources
One file per source (overture, hk, ...), never merged: Overture is ODbL share-alike, official sources keep their own terms.
Nothing is uploaded unless the publish gate passes (manifest.py: no count drops > 10% against the published manifest).
Usage: python3 publish.py     (after skylines.py extract, build and check)"""
import json, os
from huggingface_hub import HfApi, batch_bucket_files, create_bucket
import manifest
from skylines import OUT

REPO = 'alreadyopen/world-skylines'

def main():
    b = json.loads((OUT / 'build.json').read_text()); rel = b['overture']['release']
    counts = {k: v for k, v in b['rows'].items() if k != 'rejected'}          # one count per licence file
    counts.update({f'rejected_{k}': v for k, v in b['rows'].get('rejected', {}).items()})
    files = sorted(f.name for f in [*OUT.glob('*.parquet'), *OUT.glob('*.pmtiles')])
    m = manifest.manifest(str(OUT), files, counts, rel)
    manifest.gate(m, f'https://huggingface.co/buckets/{REPO}/resolve/manifest.json')
    (OUT / 'manifest.json').write_text(json.dumps(m, indent=1))
    api = HfApi(); api.create_repo(REPO, repo_type='dataset', exist_ok=True)
    for f in [*OUT.glob('*.parquet'), OUT / 'manifest.json']:
        api.upload_file(path_or_fileobj=str(f), path_in_repo=f.name, repo_id=REPO, repo_type='dataset', commit_message=f'{f.name}: Overture {rel}')
    api.upload_file(path_or_fileobj=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hf', 'README.md'),
                    path_in_repo='README.md', repo_id=REPO, repo_type='dataset', commit_message='card')
    create_bucket(REPO, private=False, exist_ok=True)
    # manifest.json last: the gate reads the bucket's copy
    batch_bucket_files(REPO, add=[(str(f), f.name) for f in OUT.glob('*.pmtiles')])
    batch_bucket_files(REPO, add=[(str(OUT / 'manifest.json'), 'manifest.json')])
    print(json.dumps({'release': rel, 'counts': counts, 'dataset': f'https://huggingface.co/datasets/{REPO}', 'bucket': f'https://huggingface.co/buckets/{REPO}'}))

if __name__ == '__main__':
    main()
