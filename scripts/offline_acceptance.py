#!/usr/bin/env python3
"""OS-enforced network denial plus real Cocoa GUI and inherited worker verification.
No networking calls are mocked. Run on macOS with permission to invoke sandbox-exec.
"""
import argparse
from pathlib import Path
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--child', action='store_true')
parser.add_argument('--source', default='tests/fixtures/02_mixed_bad_layer.pdf')
parser.add_argument('--evidence', default='artifacts/offline')
args = parser.parse_args()
evidence = (ROOT / args.evidence).resolve()
evidence.mkdir(parents=True, exist_ok=True)

if args.child:
    # Python's socket is unchanged here; this is a real macOS kernel-policy probe.
    import socket
    probes = []
    for host in ('1.1.1.1', '127.0.0.1'):
        s = socket.socket()
        s.settimeout(2)
        try:
            s.connect((host, 443))
            result = {'host':host, 'connected':True}
        except OSError as e:
            result = {'host':host, 'connected':False, 'errno':e.errno, 'error':str(e)}
        finally:
            s.close()
        probes.append(result)
    (evidence/'network-probe.json').write_text(json.dumps(probes, indent=2))
    if any(p.get('errno') != 1 for p in probes):
        raise SystemExit('Kernel network-deny probe did not produce EPERM')
    sys.argv = ['gui_smoke.py', '--source', str(ROOT/args.source), '--resources', str(ROOT/'resources'), '--output', str(evidence/'offline.epub'), '--evidence', str(evidence/'gui'), '--verify-cancel']
    import runpy
    runpy.run_path(str(ROOT/'scripts/gui_smoke.py'), run_name='__main__')
else:
    env = dict(os.environ, PYTHONPATH=str(ROOT/'src'))
    env.pop('QT_QPA_PLATFORM', None) # exercise actual Cocoa windows
    command = ['sandbox-exec', '-f', str(ROOT/'scripts/deny-network.sb'), sys.executable, str(Path(__file__).resolve()), '--child', '--source', args.source, '--evidence', args.evidence]
    observed, samples = {}, []
    with (evidence/'run.log').open('w') as log:
        proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        while proc.poll() is None:
            tree = subprocess.run(['ps','-axo','pid=,ppid=,comm='], capture_output=True, text=True).stdout
            records = {}
            for line in tree.splitlines():
                fields=line.strip().split(None,2)
                if len(fields)==3:
                    records[int(fields[0])] = (int(fields[1]), fields[2])
            pids = {proc.pid}
            for _ in range(6):
                pids |= {p for p,(ppid,_) in records.items() if ppid in pids}
            for pid in pids:
                if pid in records:
                    observed[str(pid)] = records[pid][1]
            sockets = subprocess.run(['lsof','-nP','-a','-p',','.join(map(str,pids)),'-i'], capture_output=True, text=True).stdout
            samples.append({'seconds':time.time(),'pids':sorted(pids),'internet_sockets':sockets})
            time.sleep(.12)
        code = proc.wait()
    result={'exit_code':code,'kernel_policy':'deny network*; inherited by GUI and worker descendants','processes':observed,'samples':samples,'connected_sockets_observed':any(s['internet_sockets'].strip() for s in samples),'gui_evidence':str(evidence/'gui/result.json')}
    (evidence/'process-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='samples'},ensure_ascii=False))
    sys.exit(code)
