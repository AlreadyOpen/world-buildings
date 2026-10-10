"""Publish a build to Hugging Face (needs HF_TOKEN with write access to the alreadyopen org):
  dataset alreadyopen/world-skylines  <source>.parquet (every building >= the height threshold, with footprint) + sources.json + card
  bucket  alreadyopen/world-skylines  <source>.pmtiles (z6-13, layer `skylines`) + sources.json, read in place by MapLibre
One file per source (overture, hk, ...), never merged: Overture is ODbL share-alike, official sources keep their own terms.
Usage: python3 publish.py     (after skylines.py extract, build and check)"""
import json
from pathlib import Path
from huggingface_hub import HfApi, batch_bucket_files, create_bucket
from skylines import OUT

REPO = 'alreadyopen/world-skylines'

def main():
    src = json.loads((OUT / 'sources.json').read_text()); rel = src['overture']['release']
    api = HfApi()
    api.create_repo(REPO, repo_type='dataset', exist_ok=True)
    for f in [*OUT.glob('*.parquet'), OUT / 'sources.json']:
        api.upload_file(path_or_fileobj=str(f), path_in_repo=f.name, repo_id=REPO, repo_type='dataset',
                        commit_message=f'{f.name}: Overture {rel}')
    api.upload_file(path_or_fileobj=str(Path(__file__).resolve().parent / 'hf' / 'README.md'),
                    path_in_repo='README.md', repo_id=REPO, repo_type='dataset', commit_message='card')
    create_bucket(REPO, private=False, exist_ok=True)
    batch_bucket_files(REPO, add=[(str(f), f.name) for f in [*OUT.glob('*.pmtiles'), OUT / 'sources.json']])
    print(json.dumps({'release': rel, 'rows': src['rows'], 'outputs': src['outputs'],
                      'dataset': f'https://huggingface.co/datasets/{REPO}', 'bucket': f'https://huggingface.co/buckets/{REPO}'}))

if __name__ == '__main__':
    main()
