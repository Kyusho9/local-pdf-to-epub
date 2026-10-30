#!/usr/bin/env python3
"""Maintainer-only generation of fixed CUDA runtime manifests from official ZIPs.

Run with Windows CPython 3.12 so wheel tags match the target. Generated catalog
contains only deterministic ZIP-member mappings; application download/import uses
the reviewed frozen catalog and never runs this resolver.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import urllib.request
import zipfile
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

PINS = {'rapidocr-onnxruntime':'1.4.4', 'numpy':'1.26.4', 'opencv-python':'4.11.0.86',
        'pillow':'11.1.0', 'pyclipper':'1.4.0', 'shapely':'2.2.0', 'PyYAML':'6.0.3',
        'six':'1.17.0', 'tqdm':'4.70.1', 'coloredlogs':'15.0.1', 'humanfriendly':'10.0',
        'flatbuffers':'25.12.19', 'packaging':'26.3', 'protobuf':'7.36.2',
        'sympy':'1.14.0', 'mpmath':'1.3.0', 'pyreadline3':'3.5.4', 'colorama':'0.4.6'}
RUNTIME_ID = 'rapidocr-cuda-win-x64-ort122-cu128-cudnn98-1'

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def target_for(component, member):
    path = PurePosixPath(member)
    if path.is_absolute() or '..' in path.parts or '\\' in member or ':' in member:
        raise ValueError('Unsafe archive member: ' + member)
    low = member.lower()
    if component['name'] == 'cpython-embed':
        if path.name in ('python312._pth', 'vcruntime140.dll', 'vcruntime140_1.dll'):
            return None
        return member
    if component['name'] == 'microsoft-vc-runtime':
        if '/x64/microsoft.vc143.crt/' in low and 'debug_nonredist' not in low and low.endswith('.dll'):
            return path.name.lower()
        return None
    is_license = any(term in path.name.lower() for term in ('license', 'licence', 'copying', 'notice', 'eula'))
    if component['name'].startswith('nvidia-'):
        if low.endswith('.dll') or path.name == '__init__.py' or '.dist-info/' in low or is_license:
            return 'packages/' + member
        return None
    if not is_license:
        if any(part in ('tests', 'test', 'models', 'datasets', '__pycache__', 'include') for part in path.parts):
            return None
        if path.suffix.lower() in ('.h', '.hpp', '.c', '.cpp', '.a', '.lib', '.onnx', '.pyc', '.pdb'):
            return None
        if '.data/' in low or 'onnxruntime_providers_tensorrt' in low:
            return None
        if component['name'] == 'opencv-python' and 'ffmpeg' in low:
            return None  # OCR uses images only; do not distribute unused codecs.
    return 'packages/' + member

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3,12):
        raise RuntimeError('Resolve using Windows CPython 3.12')
    seed = json.loads(args.seed.read_text(encoding='utf-8'))
    supported = list(sys_tags())
    priority = {tag:i for i,tag in enumerate(supported)}
    def resolve(pair):
        name, version = pair
        url = f'https://pypi.org/pypi/{name}/{version}/json'
        with urllib.request.urlopen(url, timeout=60) as response:
            data = json.load(response)
        candidates = []
        for wheel in data['urls']:
            if wheel['packagetype'] != 'bdist_wheel' or wheel.get('yanked'):
                continue
            _,_,_,tags = parse_wheel_filename(wheel['filename'])
            ranks = [priority[t] for t in tags if t in priority]
            if ranks:
                candidates.append((min(ranks),wheel))
        if not candidates:
            raise ValueError('No target wheel: ' + name)
        w = min(candidates, key=lambda x:x[0])[1]
        return {'name':name,'version':version,'filename':w['filename'],'url':w['url'],
                'sha256':w['digests']['sha256'],'bytes':w['size'], 'metadata_url':url}
    with ThreadPoolExecutor(max_workers=6) as pool:
        resolved = list(pool.map(resolve, PINS.items()))
    components = seed['components'] + resolved
    args.cache.mkdir(parents=True, exist_ok=True)
    def download(component):
        path = args.cache / component['filename']
        if path.is_file() and sha(path) == component['sha256']:
            if path.stat().st_size != component['bytes']:
                raise ValueError('Pinned byte count mismatch: ' + path.name)
            return
        partial = path.with_suffix('.download')
        subprocess.run(['curl.exe','--fail','--location','--retry','4','--silent','--show-error',
                        '--output',str(partial),component['url']],check=True)
        if sha(partial) != component['sha256'] or partial.stat().st_size != component['bytes']:
            raise ValueError('Official component checksum mismatch: '+path.name)
        partial.replace(path)
        print('verified: '+path.name,flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download,components))
    files = {}
    for component in components:
        mappings = []
        with zipfile.ZipFile(args.cache/component['filename']) as z:
            for info in sorted(z.infolist(),key=lambda x:x.filename):
                if info.is_dir():
                    continue
                target = target_for(component,info.filename)
                if target is None:
                    continue
                data = z.read(info)
                value = {'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
                         'purpose':component['name']+' '+component['version']+' runtime / license'}
                if target in files:
                    if files[target]['sha256'] != value['sha256']:
                        raise ValueError('Conflicting shared payload: '+target)
                    continue
                files[target] = value
                mappings.append({'source':info.filename,'target':target})
        component['files'] = mappings
    generated = seed['generated_files']
    for target,text in generated.items():
        if target in files:
            raise ValueError('Generated payload collision')
        data = text.encode('utf-8')
        files[target] = {'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
                         'purpose':'Isolated Python configuration / application runtime licensing notice'}
    catalog = {'format_version':1,'id':RUNTIME_ID,'components':components,'generated_files':generated}
    manifest = {'format_version':1,'id':RUNTIME_ID,'purpose':'本地书坊专用可选 NVIDIA CUDA OCR 运行组件；不含模型；默认 CPU 不受影响',
                'platform':'win32-amd64','versions':{c['name']:c['version'] for c in components},
                'files':files}
    args.output.mkdir(parents=True,exist_ok=True)
    for name,value in [('gpu_runtime_catalog.json',catalog),('gpu_runtime_manifest.json',manifest)]:
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'id':RUNTIME_ID,'components':len(components),'payload_files':len(files),
                      'download_bytes':sum(c['bytes'] for c in components),'runtime_bytes':sum(v['bytes'] for v in files.values())}))

if __name__ == '__main__':
    main()
