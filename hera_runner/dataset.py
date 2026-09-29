"""Download and verify the separately versioned executable dataset."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
LOCK = json.loads((PROJECT / 'dataset.lock.json').read_text())


def validate(root):
    root = Path(root).resolve()
    manifest = root / 'checksums.sha256'
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != LOCK['checksums_sha256']:
        raise ValueError('Dataset differs from dataset.lock.json; use the download command.')
    seen = set()
    for line in manifest.read_text().splitlines():
        digest, rel = line.split('  ', 1)
        path = Path(rel)
        if path.is_absolute() or '..' in path.parts or rel in seen:
            raise ValueError('Invalid checksum path')
        seen.add(rel)
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
            raise ValueError(f'Dataset checksum mismatch: {rel}')
    tasks = json.loads((root / 'tasks.json').read_text())
    if [t['number'] for t in tasks] != list(range(1, 61)):
        raise ValueError('Expected the 60 ordered HERA task pairs')
    return root, tasks


def download(destination):
    from huggingface_hub import snapshot_download
    root = snapshot_download(LOCK['repo_id'], repo_type='dataset', revision=LOCK['revision'],
                             local_dir=str(destination))
    validate(root)
    return str(Path(root).resolve())


def controller(root, *arguments):
    return [sys.executable, '-I', '-B', str(Path(root) / 'benchmark.py'), *map(str, arguments)]


def grade(root, number, side, submission, output):
    result = subprocess.run(controller(root, 'grade', '--number', number, '--side', side,
                                       '--submission', submission, '--output', output),
                            capture_output=True, text=True)
    if result.returncode not in (0, 1) or not Path(output).is_file():
        raise RuntimeError('Dataset grader failed; inspect the controller inputs locally.')
    return json.loads(Path(output).read_text())
