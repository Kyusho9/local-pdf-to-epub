#!/usr/bin/env python3
"""Prepare a distinct DirectML runtime using the same verified common payloads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source-runtime',type=Path,required=True)
    p.add_argument('--cuda-catalog',type=Path,required=True)
    p.add_argument('--wheel',type=Path,required=True)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists() and any(a.output.iterdir()):
        raise ValueError('New independent DML directory required')
    a.output.mkdir(parents=True,exist_ok=True)
    cuda=json.loads(a.cuda_catalog.read_text(encoding='utf-8'))
    expected=json.loads((a.source_runtime/'manifest.json').read_text(encoding='utf-8'))
    dml=json.loads(a.wheel.read_text(encoding='utf-8'))
    archive=a.cache/dml['filename']
    if not archive.is_file():
        subprocess.run(['curl.exe','--fail','--location','--retry','4','--silent','--show-error','--output',str(archive),dml['url']],check=True)
    if archive.stat().st_size!=dml['bytes'] or sha(archive)!=dml['sha256']:
        raise ValueError('DirectML official wheel hash mismatch')
    components=[c for c in cuda['components'] if not c['name'].startswith('nvidia-') and c['name']!='onnxruntime-gpu']
    files={}
    for component in components:
        for mapping in component['files']:
            name=mapping['target']; source=a.source_runtime/name
            if sha(source)!=expected['files'][name]['sha256']:
                raise ValueError('Source common runtime mismatch: '+name)
            target=a.output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
            files[name]=expected['files'][name]
    generated={name:text for name,text in cuda['generated_files'].items() if
               name=='python312._pth' or name in ('licenses/MICROSOFT-VC-RUNTIME-LICENSE.txt','licenses/RapidOCR-1.4.4-LICENSE.txt','licenses/FlatBuffers-25.12.19-LICENSE.txt')}
    for name,text in generated.items():
        data=text.encode('utf-8');target=a.output/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        files[name]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'purpose':'Shared upstream license / isolated configuration'}
    dml['files']=[]
    with zipfile.ZipFile(archive) as z:
        for info in sorted(z.infolist(),key=lambda x:x.filename):
            if info.is_dir() or '/tests/' in info.filename or info.filename.endswith(('.onnx','.h','.lib','.pdb')):
                continue
            name='packages/'+info.filename
            data=z.read(info);target=a.output/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            files[name]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'purpose':'ONNX Runtime DirectML 1.22.0 / bundled Microsoft DirectML runtime'}
            dml['files'].append({'source':info.filename,'target':name})
    components.append(dml)
    catalog={'format_version':1,'id':'rapidocr-directml-win-x64-ort122-1','components':components,'generated_files':generated}
    manifest={'format_version':1,'id':catalog['id'],'purpose':'Independent DirectML image OCR runtime probe; no CUDA libraries or OCR models',
              'platform':'win32-amd64','versions':{c['name']:c['version'] for c in components},'files':files}
    (a.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    for name,obj in [('directml_runtime_catalog.json',catalog),('directml_runtime_manifest.json',manifest)]:
        (a.output.parent/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    record={'independent_runtime':str(a.output),'dml_wheel':dml['filename'],'dml_wheel_sha256':dml['sha256'],
            'download_bytes':sum(c['bytes'] for c in components),'runtime_bytes':sum(f['bytes'] for f in files.values()),
            'files':len(files),'bundled_dml_dlls':[n for n in files if n.lower().endswith('directml.dll')],
            'cuda_dlls':[n for n in files if '/nvidia/' in n or 'providers_cuda' in n],
            'existing_cuda_cpu_directories_modified':False}
    (a.output.parent/'preparation.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record))

if __name__=='__main__':main()
