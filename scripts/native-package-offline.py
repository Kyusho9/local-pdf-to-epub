#!/usr/bin/env python3
"""Launch the frozen macOS GUI under kernel network denial for manual acceptance.

The test harness may use a developer Python, but the app receives system-only
PATH and no Python/Qt development paths. Close the GUI to finish the report.
UI interaction is deliberately left to the operator.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--evidence', type=Path, default=ROOT/'artifacts/native-release-final')
args = parser.parse_args()
evidence = args.evidence.resolve()
evidence.mkdir(parents=True, exist_ok=True)
if sys.platform != 'darwin':
    raise SystemExit('This kernel-denial harness requires macOS.')
policy = ['sandbox-exec', '-f', str(ROOT/'scripts/deny-network.sb')]
probe = '''import socket, json
results = []
for host in ('1.1.1.1', '127.0.0.1'):
    s=socket.socket(); s.settimeout(2)
    try:
        s.connect((host,443)); results.append({'host':host,'connected':True})
    except OSError as e:
        results.append({'host':host,'connected':False,'errno':e.errno,'error':str(e)})
    finally:
        s.close()
print(json.dumps(results))
raise SystemExit(0 if all(p.get('errno') == 1 for p in results) else 1)
'''
checked = subprocess.run([*policy,sys.executable,'-c',probe],capture_output=True,text=True)
(evidence/'network-probe.json').write_text(checked.stdout)
checked.check_returncode()
env = {k:v for k,v in os.environ.items() if k not in (
    'PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','CONDA_PREFIX','QT_PLUGIN_PATH',
    'QT_QPA_PLATFORM','QT_QPA_PLATFORM_PLUGIN_PATH','DYLD_LIBRARY_PATH','DYLD_FRAMEWORK_PATH')}
env['PATH']='/usr/bin:/bin:/usr/sbin:/sbin'
env['LOCAL_EPUB_RESOURCES']=str(evidence/'imported-resources')
executable=ROOT/'dist/LocalPDFtoEPUB.app/Contents/MacOS/LocalPDFtoEPUB'
observed, samples = {}, []
with (evidence/'app.log').open('w') as log:
    proc=subprocess.Popen([*policy,str(executable)],cwd=evidence,env=env,stdout=log,stderr=subprocess.STDOUT)
    print(json.dumps({'pid':proc.pid,'executable':str(executable),'resource_directory':env['LOCAL_EPUB_RESOURCES']}),flush=True)
    while proc.poll() is None:
        tree=subprocess.run(['ps','-axo','pid=,ppid=,comm='],capture_output=True,text=True).stdout
        records={}
        for line in tree.splitlines():
            fields=line.strip().split(None,2)
            if len(fields)==3:
                records[int(fields[0])]=(int(fields[1]),fields[2])
        pids={proc.pid}
        for _ in range(6):
            pids|={p for p,(parent,_) in records.items() if parent in pids}
        for pid in pids:
            if pid in records:
                observed[str(pid)]=records[pid][1]
        sockets=subprocess.run(['lsof','-nP','-a','-p',','.join(map(str,pids)),'-i'],capture_output=True,text=True).stdout
        samples.append({'time':time.time(),'pids':sorted(pids),'internet_sockets':sockets})
        time.sleep(.25)
result={'exit_code':proc.returncode,'executable':str(executable),'network_policy':'macOS deny network* inherited by GUI and worker',
        'initial_pythonioencoding':env.get('PYTHONIOENCODING'),
        'developer_paths_removed':True,'processes':observed,'samples':samples,
        'connected_sockets_observed':any(s['internet_sockets'].strip() for s in samples)}
(evidence/'process-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='samples'},ensure_ascii=False))
raise SystemExit(proc.returncode)
