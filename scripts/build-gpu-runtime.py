#!/usr/bin/env python3
"""Build a deterministic app-specific CUDA ZIP from reviewed official components.

Run on any build machine with the component archives present. It performs no
package installation and cannot derive executable trust from the ZIP it emits.
The application ships the exact same catalog and expected manifest.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def safe(name):
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ('', '.', '..') for x in p.parts) or '\\' in name or ':' in name:
        raise ValueError('Unsafe payload path: '+name)
    return name

def build(catalog_path, manifest_path, cache, output, archive_path):
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if catalog['id'] != manifest['id']:
        raise ValueError('Catalog and manifest mismatch')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be a new or empty directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cuda-build-',dir=output.parent) as temporary:
        staged = Path(temporary)/'runtime'
        staged.mkdir()
        seen = set()
        def write(name, stream):
            safe(name)
            if name in seen or name not in manifest['files']:
                raise ValueError('Unexpected or duplicate mapped payload: '+name)
            seen.add(name)
            target = staged/name
            target.parent.mkdir(parents=True, exist_ok=True)
            h=hashlib.sha256(); count=0
            with target.open('wb') as out:
                while data:=stream.read(1024*1024):
                    count+=len(data);h.update(data);out.write(data)
                    if count>manifest['files'][name]['bytes']:
                        raise ValueError('Oversized payload: '+name)
            if count!=manifest['files'][name]['bytes'] or h.hexdigest()!=manifest['files'][name]['sha256']:
                raise ValueError('Payload mismatch: '+name)
        for component in catalog['components']:
            path=cache/component['filename']
            if not path.is_file() or path.stat().st_size!=component['bytes'] or digest(path)!=component['sha256']:
                raise ValueError('Official component missing or corrupt: '+str(path))
            with zipfile.ZipFile(path) as z:
                for mapping in component['files']:
                    safe(mapping['source'])
                    with z.open(mapping['source']) as stream:
                        write(mapping['target'],stream)
        import io
        for name,text in catalog.get('generated_files',{}).items():
            write(name,io.BytesIO(text.encode('utf-8')))
        if seen!=set(manifest['files']):
            raise ValueError('Missing payloads: '+repr(set(manifest['files'])-seen))
        (staged/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        if output.exists():
            output.rmdir()
        staged.rename(output)
    archive_path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for name in sorted([*manifest['files'],'manifest.json']):
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16
            with (output/name).open('rb') as source,z.open(info,'w',force_zip64=True) as target:
                shutil.copyfileobj(source,target,1024*1024)
    record={'id':manifest['id'],'archive':str(archive_path),'archive_sha256':digest(archive_path),
            'archive_bytes':archive_path.stat().st_size,'payload_files':len(manifest['files']),
            'runtime_bytes':sum(x['bytes'] for x in manifest['files'].values()),
            'download_bytes':sum(x['bytes'] for x in catalog['components']),
            'catalog_sha256':digest(catalog_path),'manifest_sha256':digest(manifest_path),
            'output':str(output),'contains_worker':False,'contains_models':False}
    archive_path.with_suffix('.build.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--catalog',type=Path,default=Path('src/local_epub/gpu_runtime_catalog.json'))
    p.add_argument('--manifest',type=Path,default=Path('src/local_epub/gpu_runtime_manifest.json'))
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--zip',type=Path,required=True)
    a=p.parse_args()
    build(a.catalog,a.manifest,a.cache,a.output,a.zip)
