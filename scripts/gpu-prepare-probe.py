#!/usr/bin/env python3
"""Explicit developer preparation of an isolated Windows CUDA probe.

Downloads only URLs and hashes from the supplied official wheel catalog. Existing
CPU Python distributions are copied by RECORD into a separate environment, never
modified. This is not the ordinary user's runtime installer.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib.metadata as md
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import venv
import zipfile

BASE_PACKAGES = ('rapidocr-onnxruntime', 'numpy', 'opencv-python', 'pypdfium2',
                 'pillow', 'pyclipper', 'shapely', 'PyYAML', 'six', 'tqdm',
                 'coloredlogs', 'humanfriendly', 'flatbuffers', 'packaging',
                 'protobuf', 'sympy', 'mpmath', 'pyreadline3', 'pefile')

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--catalog', type=Path, required=True)
    a = p.parse_args()
    if sys.platform != 'win32':
        raise RuntimeError('Windows-only isolated developer probe')
    root = a.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    wheelhouse = root / 'wheels'
    wheelhouse.mkdir(exist_ok=True)
    catalog = json.loads(a.catalog.read_text(encoding='utf-8'))
    def get(w):
        target = wheelhouse / w['filename']
        if target.exists() and target.stat().st_size == w['size'] and sha(target) == w['sha256']:
            print('verified cache: ' + w['filename'], flush=True)
            return
        if not w['url'].startswith('https://files.pythonhosted.org/'):
            raise ValueError('Unapproved wheel origin')
        partial = target.with_suffix('.download')
        print('download: ' + w['filename'], flush=True)
        # curl supports retry and reliably streams large official wheels on Windows.
        subprocess.run(['curl.exe', '--fail', '--location', '--retry', '4',
                        '--silent', '--show-error', '--output', str(partial), w['url']], check=True)
        if partial.stat().st_size != w['size'] or sha(partial) != w['sha256']:
            raise ValueError('Official wheel hash mismatch: ' + w['filename'])
        partial.replace(target)
        print('verified download: ' + w['filename'], flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(get, catalog['wheels']))
    env = root / '.venv'
    if not (env / 'Scripts/python.exe').exists():
        venv.EnvBuilder(with_pip=True).create(env)
    site = env / 'Lib/site-packages'
    copies = []
    for name in BASE_PACKAGES:
        dist = md.distribution(name)
        base = Path(dist.locate_file('')).resolve()
        count = 0
        for rel in dist.files or []:
            origin = Path(dist.locate_file(rel)).resolve()
            try:
                relative = origin.relative_to(base)
            except ValueError:
                continue  # Console scripts are not needed by an image OCR worker.
            if origin.is_file() and '__pycache__' not in relative.parts:
                target = site / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, target)
                count += 1
        copies.append({'name': name, 'version': dist.version, 'files_copied': count,
                       'source_environment': str(base), 'independent_copy': True})
    python = env / 'Scripts/python.exe'
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps',
                    *[str(wheelhouse / w['filename']) for w in catalog['wheels']]], check=True)
    licenses = root / 'licenses'
    licenses.mkdir(exist_ok=True)
    license_files = []
    for wheel in catalog['wheels']:
        with zipfile.ZipFile(wheelhouse / wheel['filename']) as z:
            for info in z.infolist():
                basename = Path(info.filename).name.lower()
                if not info.is_dir() and any(key in basename for key in ('license', 'copying', 'notice', 'eula')):
                    relative = Path(wheel['name']) / Path(info.filename).name
                    target = licenses / relative
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(z.read(info))
                    license_files.append({'file': relative.as_posix(), 'sha256': sha(target),
                                          'origin': wheel['filename'] + ':' + info.filename})
    record = {'prepared_at': datetime.now(timezone.utc).isoformat(), 'python': str(python),
              'wheels': catalog['wheels'], 'copied_distributions': copies,
              'licenses': license_files, 'cpu_environment_modified': False,
              'global_cuda_toolkit_installed': False,
              'note': 'RapidOCR declares onnxruntime distribution; GPU package supplies the same Python module. Installed with explicit no-deps; CPU and GPU ORT never coexist.'}
    (root / 'preparation.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'prepared': True, 'python': str(python), 'wheels': len(catalog['wheels']),
                      'wheel_bytes': sum(w['size'] for w in catalog['wheels'])}), flush=True)

if __name__ == '__main__':
    main()
