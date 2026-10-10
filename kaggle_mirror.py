"""Mirror the published files to a public Kaggle dataset (the same file in every AlreadyOpen pipeline).
Kaggle's "Remote URL" cannot pull from Hugging Face (every HF download is a 302 to a signed URL, which Kaggle refuses), so the
files are uploaded. Runs after publish.py as its own step with continue-on-error: Kaggle failing never blocks Hugging Face.
Needs KAGGLE_API_TOKEN (Helen's token, as a repo secret). The dataset is owned by her account: a Kaggle group cannot own one.
Description = hf/README.md (or $CARD) without its YAML header; licence "other" (per-file terms are in the description).
Usage: python3 kaggle_mirror.py <owner/slug> "<title, 6-50 chars>" <file>...    (files are uploaded by base name)"""
import json, os, re, subprocess, sys, tempfile

def card_body(path=os.environ.get('CARD', 'hf/README.md')):
    t = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), path), encoding='utf-8').read()
    return re.sub(r'\A---\n.*?\n---\n', '', t, flags=re.S).strip()

def main(slug, title, files):
    exists = subprocess.run(['kaggle', 'datasets', 'files', slug], capture_output=True).returncode == 0
    with tempfile.TemporaryDirectory() as d:
        for f in files:
            os.symlink(os.path.abspath(f), os.path.join(d, os.path.basename(f)))
        meta = {'title': title, 'id': slug, 'licenses': [{'name': 'other'}], 'description': card_body()}
        json.dump(meta, open(os.path.join(d, 'dataset-metadata.json'), 'w'), indent=1)
        if exists:
            cmd = ['kaggle', 'datasets', 'version', '-p', d, '-m', os.environ.get('KAGGLE_MESSAGE', 'update'), '-t', '-q']
        else:
            cmd = ['kaggle', 'datasets', 'create', '-p', d, '--public', '-t', '-q']
        subprocess.run(cmd, check=True)
    print(json.dumps({'kaggle': f'https://www.kaggle.com/datasets/{slug}', 'files': [os.path.basename(f) for f in files],
                      'created' if not exists else 'versioned': True}))

if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
