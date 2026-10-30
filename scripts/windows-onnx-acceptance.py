"""Reproduce real Windows ONNX OCR acceptance using only new task directories.

Run with the locked developer environment; no downloads or runtime installation.
The deliberate termination cases target only processes spawned by this run.
This does not install firewall rules or replace separate frozen-GUI/offline tests.
"""
from pathlib import Path
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('--backend', choices=('directml', 'cuda'), default='directml', help='Backend for recovery, fallback and cancellation cases.')
parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1], help='Source checkout root (default: parent of scripts).')
parser.add_argument('--python', type=Path, default=Path(sys.executable), help='CPU application/developer Python; never point at an isolated GPU runtime.')
parser.add_argument('--resources', type=Path, required=True, help='Prepared local OCR model directory.')
parser.add_argument('--directml-runtime', type=Path, help='Prepared, verified DirectML runtime directory.')
parser.add_argument('--cuda-runtime', type=Path, help='Prepared, verified CUDA runtime directory.')
parser.add_argument('--output', type=Path, required=True, help='New, nonexistent evidence directory; never overwrites a prior run.')
parser.add_argument('--benchmark-devices', nargs='+', choices=('cpu', 'directml', 'cuda'), help='12-page benchmark devices (default: cpu and selected backend); specify cpu directml cuda for three-way comparison.')
parser.add_argument('--expect-adapter', help='Optional exact adapter name assertion; otherwise record the actual reported adapter.')
parser.add_argument('--timeout-seconds', type=float, default=240, help='Maximum seconds for each CLI invocation (default: 240).')
settings = parser.parse_args()
if os.name != 'nt':
    parser.error('Real acceptance requires Windows; --help is available on all platforms.')
if sys.flags.optimize:
    parser.error('Assertions are acceptance checks; do not run Python with -O.')
if settings.timeout_seconds <= 0:
    parser.error('--timeout-seconds must be positive.')
BACKEND = settings.backend
SOURCE = settings.source.expanduser().resolve()
PYTHON = settings.python.expanduser().resolve()
MODELS = settings.resources.expanduser().resolve()
RUN = settings.output.expanduser().resolve()
BOOK = SOURCE / 'tests/fixtures/02_mixed_bad_layer.pdf'
RUNTIMES = {kind: path.expanduser().resolve() if path else None for kind, path in
            [('directml', settings.directml_runtime), ('cuda', settings.cuda_runtime)]}
BENCHMARK_DEVICES = list(dict.fromkeys(settings.benchmark_devices or ['cpu', BACKEND]))
for kind in set(BENCHMARK_DEVICES + [BACKEND]) - {'cpu'}:
    if RUNTIMES[kind] is None or not (RUNTIMES[kind] / 'python.exe').is_file():
        parser.error(f'--{kind}-runtime must contain the prepared embedded python.exe.')
if PYTHON in [path / 'python.exe' for path in RUNTIMES.values() if path]:
    parser.error('--python must be the CPU developer environment, not a GPU runtime.')
for path in (PYTHON, BOOK, SOURCE / 'src/local_epub/gpu_worker.py'):
    if not path.is_file():
        parser.error(f'Required local file missing: {path}')
if not MODELS.is_dir():
    parser.error(f'Model directory missing: {MODELS}')
try:
    RUN.mkdir(parents=True, exist_ok=False)
except FileExistsError:
    parser.error('--output already exists; choose a new directory to preserve evidence.')
ENV = {**os.environ, 'PYTHONPATH': str(SOURCE / 'src'), 'PYTHONIOENCODING': 'utf-8'}
ENV.pop('PYTHONOPTIMIZE', None)
ENV.pop('PYTHONHOME', None)
SOURCE_HASHES = {name: hashlib.sha256((SOURCE / 'src/local_epub' / name).read_bytes()).hexdigest()
                 for name in ('backend.py', 'pipeline.py', 'gpu_execution.py', 'gpu_worker.py')}
result = {'run': str(RUN), 'platform': 'Windows', 'os_network_blocked': False,
          'network_note': 'Application socket guards only. This script does not configure OS firewall rules; use separate offline acceptance for that claim.',
          'steps': [], 'backend': BACKEND, 'pass': False, 'source_hashes': SOURCE_HASHES,
          'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'configuration': {'source': str(SOURCE), 'python': str(PYTHON), 'resources': str(MODELS),
                            'runtimes': {k: str(v) if v else None for k, v in RUNTIMES.items()},
                            'benchmark_devices': BENCHMARK_DEVICES}}

def runtime_for(device):
    return RUNTIMES.get(device) or RUNTIMES[BACKEND]

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def page_stamps(job):return {p.name:(p.stat().st_mtime_ns,sha(p)) for p in (job/'pages').glob('*.json')}
def process_parents():
    """A read-only snapshot, used only to prove ancestry of this run's worker."""
    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = ProcessEntry(); entry.dwSize = ctypes.sizeof(entry)
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return parents


class OwnedWorker:
    """Retain exact process handles; never kill by name or a reused PID.

    The PID must come from our own CLI stdout, its ancestry must lead to the
    Popen we created, and its executable must be our selected runtime Python.
    The immediate parent can be below a Windows venv launcher; holding its
    handle lets the hard-parent test exercise JobObject cleanup itself.
    """
    def __init__(self, pid, proc):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handles = []
        try:
            self.worker = self._open(pid)
            buf = ctypes.create_unicode_buffer(32768); length = wintypes.DWORD(len(buf))
            if not self.kernel.QueryFullProcessImageNameW(self.worker, 0, buf, ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            if os.path.normcase(buf.value) != os.path.normcase(str(runtime_for(BACKEND) / 'python.exe')):
                raise AssertionError('Worker executable is not this run’s selected runtime Python')
            parents = process_parents(); ancestor = pid; seen = set()
            while ancestor != proc.pid and ancestor not in seen:
                seen.add(ancestor); ancestor = parents.get(ancestor, 0)
                if not ancestor:
                    break
            if ancestor != proc.pid or proc.poll() is not None:
                raise AssertionError('Worker is not a live descendant of this exact CLI invocation')
            self.parent_pid = parents[pid]
            self.parent = self._open(self.parent_pid)
        except BaseException:
            self.close()
            raise

    def _open(self, pid):
        handle = self.kernel.OpenProcess(0x1000 | 0x0001, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handles.append(handle)
        return handle

    def terminate(self, parent=False):
        handle = self.parent if parent else self.worker
        code = wintypes.DWORD()
        if self.kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:
            return
        if not self.kernel.TerminateProcess(handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        for handle in self.handles:
            self.kernel.CloseHandle(handle)
        self.handles = []


def cli(name, args, action=None):
    events = []; start = time.perf_counter(); trigger = {}; worker_pid = None
    actor = None; owned = None; error = None
    stderr_path = RUN / (name + '.stderr')
    with stderr_path.open('w', encoding='utf-8') as stderr_file:
        proc = subprocess.Popen([str(PYTHON), '-m', 'local_epub', *map(str, args)],
                                stdout=subprocess.PIPE, stderr=stderr_file, text=True,
                                encoding='utf-8', env=ENV, cwd=SOURCE)
        lines = queue.Queue()
        def read_stdout():
            try:
                for line in proc.stdout:
                    lines.put(line)
            finally:
                lines.put(None)
        reader = threading.Thread(target=read_stdout, daemon=True); reader.start()

        def act(job, cancel_file):
            try:
                seen = []; deadline = time.monotonic() + 30
                while time.monotonic() < deadline and proc.poll() is None:
                    for file in job.glob('.' + BACKEND + '-*/image-*.png'):
                        if file.name not in seen:
                            seen.append(file.name)
                    # Second crop means full-page OCR already returned: exercise
                    # cancellation or whole-page fallback after partial GPU work.
                    if len(seen) >= 2:
                        trigger.update(observed_images=seen, worker_pid=worker_pid,
                                       launcher_pid=proc.pid, parent_pid=owned.parent_pid,
                                       action=action['kind'], process_ownership_verified=True)
                        if action['kind'] == 'cancel':
                            cancel_file.touch()
                        elif action['kind'] == 'kill-child':
                            owned.terminate(); trigger['kill_returncode'] = 0
                        elif action['kind'] == 'kill-parent':
                            owned.terminate(parent=True)
                        return
                    time.sleep(.001)
                trigger['error'] = 'Second page-crop image was not observed while the CLI was running'
            except BaseException as exc:
                trigger['error'] = repr(exc)

        try:
            deadline = time.monotonic() + settings.timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'{name} exceeded {settings.timeout_seconds} seconds')
                try:
                    line = lines.get(timeout=min(remaining, .5))
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    event = {'raw': line}
                events.append(event)
                if event.get('gpu_worker_pid'):
                    worker_pid = event['gpu_worker_pid']
                    if action and owned is None:
                        owned = OwnedWorker(worker_pid, proc)
                if action and event.get('stage') == 'parse' and event.get('page') == 3 and actor is None:
                    if owned is None:
                        raise AssertionError('Missing verified process handle for this CLI worker')
                    actor = threading.Thread(target=act, args=(action['job'], action.get('cancel_file')))
                    actor.start()
            proc.wait(timeout=max(1, deadline - time.monotonic()))
        except BaseException as exc:
            error = exc
        finally:
            if error is not None and proc.poll() is None:
                # Cleanup uses only retained handles from this invocation. No
                # taskkill /IM, process-name matching, or unrelated process tree.
                if owned:
                    for is_parent in (True, False):
                        try:
                            owned.terminate(parent=is_parent)
                        except OSError:
                            pass
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=10)
            if actor:
                actor.join(timeout=35)
            if owned:
                owned.close()
            reader.join(timeout=2)
    stderr = stderr_path.read_text(encoding='utf-8')
    rec = {'name': name, 'args': list(map(str, args)), 'exit_code': proc.returncode,
           'elapsed_seconds': time.perf_counter()-start, 'events': events,
           'stderr': stderr, 'trigger': trigger, 'harness_error': repr(error) if error else None}
    (RUN / (name + '.json')).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding='utf-8')
    result['steps'].append({'name': name, 'exit_code': proc.returncode,
                            'elapsed_seconds': rec['elapsed_seconds'], 'evidence': name + '.json'})
    if error:
        raise error
    if action and trigger.get('error'):
        raise AssertionError(trigger['error'])
    return rec

def convert(job,device=None,fallback=False,extra=None):
    device=device or BACKEND
    args=['convert',BOOK,'--job',job,'--resources',MODELS,'--ocr-device',device,'--gpu-runtime',runtime_for(device),'--gpu-fallback' if fallback else '--no-gpu-fallback']
    return args+(extra or [])
def resume(job,device=None,fallback=False,extra=None):
    device=device or BACKEND
    return ['resume',job,'--resources',MODELS,'--ocr-device',device,'--gpu-runtime',runtime_for(device),'--gpu-fallback' if fallback else '--no-gpu-fallback']+(extra or [])
def assert_complete(rec,job):
    assert rec['exit_code']==0,rec
    state=read(job/'job.json');assert state['status']=='complete',state
    assert all(p['status']=='success' for p in state['pages'])
    assert not list(job.glob('.directml-*')) and not list(job.glob('.cuda-*'))
    return state

def pid_alive(pid):
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong];k.OpenProcess.restype=ctypes.c_void_p
    k.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)];k.CloseHandle.argtypes=[ctypes.c_void_p]
    h=k.OpenProcess(0x1000,False,pid)
    if not h:return False
    value=ctypes.c_ulong();k.GetExitCodeProcess(h,ctypes.byref(value));k.CloseHandle(h);return value.value==259

try:
    gpu=RUN/'gpu';rec=cli('gpu-convert',convert(gpu,extra=['--output',RUN/'gpu.epub']));state=assert_complete(rec,gpu)
    assert state['execution_summary']['counts']=={'native':1,'cpu':0,'directml':3 if BACKEND=='directml' else 0,'cuda':3 if BACKEND=='cuda' else 0},state
    pages=[read(gpu/'pages'/f'{n:04d}.json') for n in range(1,5)]
    for page in pages[1:]:
        ex=page['execution'];assert ex['verified'] and ex['identity']['kind']==BACKEND
        if BACKEND=='cuda':assert ex['identity']['provider_options']['use_tf32']==0
        else:assert ex['identity']['hardware']['luid'] and ex['identity']['hardware']['software'] is False and 'EnumAdapters' in ex['identity']['hardware']['binding']
        assert ex['identity']['hardware']['name']
        if settings.expect_adapter:assert ex['identity']['hardware']['name']==settings.expect_adapter
        assert all(not model['executed'] or model['accelerator_compute_nodes']>0 for model in ex['models'].values())
    assert pages[1]['execution']['evidence']=='onnxruntime-first-page-model-profile'
    assert all(p['execution']['evidence']=='same-verified-session-successful-inference' for p in pages[2:])
    assert all(p['execution']['models_scope']=='verification-profile-not-current-page-counters' for p in pages[1:])
    text=''.join(b.get('text','') for p in pages for b in p['blocks'])
    assert '青山不改' in text and '春风又绿江南岸' in text
    for number in (2,3):assert any(b.get('text')==str(number) for b in pages[number-1]['blocks'])
    stamps=page_stamps(gpu);rec=cli('gpu-resume-cache',resume(gpu));assert_complete(rec,gpu)
    assert stamps==page_stamps(gpu)
    assert len([e for e in rec['events'] if e.get('stage')=='cache'])==4
    assert not [e for e in rec['events'] if e.get('stage')=='parse']
    document=read(gpu/'document.json');block=next(b for b in document['blocks'] if b['sources'][0]['page']==2 and b['kind']=='paragraph' and not b.get('page_margin_role'))
    revised=block['text']+'人工 GPU 校对保留标记';textfile=RUN/'edit.txt';textfile.write_text(revised,encoding='utf-8')
    assert cli('manual-edit',['edit',gpu,block['id'],'--text-file',textfile])['exit_code']==0
    edits=(gpu/'edits.json').read_bytes()
    assert cli('export-only',['export',gpu,'--output',RUN/'edited.epub'])['exit_code']==0
    assert stamps==page_stamps(gpu)
    old_keys=[p['cache_key'] for p in read(gpu/'job.json')['pages']]
    state=assert_complete(cli('switch-to-cpu',resume(gpu,device='cpu')),gpu)
    assert state['execution_summary']['counts']=={'native':1,'cpu':3,'directml':0,'cuda':0}
    assert old_keys!=[p['cache_key'] for p in state['pages']]
    assert (gpu/'edits.json').read_bytes()==edits
    cpu_pages=[read(gpu/'pages'/f'{n:04d}.json') for n in range(1,5)]
    comparisons=[]
    for before,after in zip(pages,cpu_pages):
        comparisons.append({'page':before['number'],'same_block_text':[(b['kind'],b.get('text')) for b in before['blocks']]==[(b['kind'],b.get('text')) for b in after['blocks']], 'gpu_method':before['method'],'cpu_method':after['method']})
    result['cpu_gpu_content_comparison']=comparisons
    state=assert_complete(cli('switch-back-gpu',resume(gpu)),gpu)
    assert state['execution_summary']['counts'][BACKEND]==3
    assert (gpu/'edits.json').read_bytes()==edits
    final_doc=read(gpu/'document.json')
    assert any(b.get('text')==revised for b in final_doc['blocks']) or any(c.get('edit',{}).get('text')==revised for c in final_doc['edit_conflicts'])
    missing=RUN/'missing-runtime';job=RUN/'missing-fallback'
    args=convert(job,fallback=True);args[args.index('--gpu-runtime')+1]=missing
    state=assert_complete(cli('missing-runtime-fallback',args),job)
    assert state['execution_summary']['counts']=={'native':1,'cpu':3,'directml':0,'cuda':0}
    assert len(state['execution_summary']['fallback_pages'])==3
    strict=RUN/'missing-strict';args=convert(strict);args[args.index('--gpu-runtime')+1]=missing
    rec=cli('missing-runtime-strict',args);state=read(strict/'job.json')
    assert rec['exit_code']!=0 and state['status']=='incomplete'
    assert state['pages'][0]['status']=='success' and all(p['status']=='failed' for p in state['pages'][1:])
    canceljob=RUN/'cancel';cancel=RUN/'cancel.signal'
    rec=cli('cancel-mid-page',convert(canceljob,extra=['--cancel-file',cancel]),action={'kind':'cancel','job':canceljob,'cancel_file':cancel})
    assert rec['exit_code']==130 and rec['trigger'].get('observed_images'),rec
    state=read(canceljob/'job.json');assert state['status']=='cancelled'
    assert state['pages'][1]['status']=='success' and state['pages'][1]['execution']['identity']['kind']==BACKEND
    assert state['pages'][2]['status']=='pending' and not (canceljob/'pages/0003.json').exists()
    assert not state['execution_summary']['fallback_pages']
    assert not pid_alive(rec['trigger']['worker_pid'])
    assert not list(canceljob.glob('.'+BACKEND+'-*'))
    cancel.unlink();state=assert_complete(cli('cancel-resume',resume(canceljob)),canceljob)
    assert state['execution_summary']['counts'][BACKEND]==3
    killjob=RUN/'kill-child'
    rec=cli('kill-child-fallback',convert(killjob,fallback=True),action={'kind':'kill-child','job':killjob})
    state=assert_complete(rec,killjob)
    assert rec['trigger'].get('kill_returncode')==0,rec
    assert state['execution_summary']['counts']=={'native':1,'cpu':2,'directml':1 if BACKEND=='directml' else 0,'cuda':1 if BACKEND=='cuda' else 0},state
    assert state['pages'][1]['execution']['identity']['kind']==BACKEND
    for index in (2,3):
        assert state['pages'][index]['execution']['identity']['kind']=='cpu'
        assert state['pages'][index]['execution']['fallback_reason']
        fallback_page=read(killjob/'pages'/f'{index+1:04d}.json')
        assert [(b['kind'],b.get('text')) for b in fallback_page['blocks']]==[(b['kind'],b.get('text')) for b in cpu_pages[index]['blocks']]
    hardjob=RUN/'kill-parent'
    rec=cli('kill-parent-jobobject',convert(hardjob),action={'kind':'kill-parent','job':hardjob})
    assert rec['exit_code']!=0 and rec['trigger'].get('worker_pid'),rec
    deadline=time.monotonic()+10
    while pid_alive(rec['trigger']['worker_pid']) and time.monotonic()<deadline:time.sleep(.1)
    assert not pid_alive(rec['trigger']['worker_pid']),'GPU worker survived parent termination'
    state=read(hardjob/'job.json');assert state['pages'][2]['status']=='processing'
    assert not (hardjob/'pages/0003.json').exists()
    # A forcibly terminated Python cannot remove its temporary directory; the
    # next run must remain valid and close its own worker. The leftover contains
    # only local task-owned image/profile files, no runtime mutation.
    rec=cli('hard-kill-resume',resume(hardjob));state=read(hardjob/'job.json')
    assert rec['exit_code']==0 and state['status']=='complete'
    assert state['execution_summary']['counts'][BACKEND]==3
    result['hard_kill_leftover_temp_dirs']=[str(p) for p in hardjob.glob('.'+BACKEND+'-*')]
    benchmark=RUN/'mixed-12.pdf'
    # Use the explicitly selected developer Python for its locked pypdf dependency.
    make_book = ('import sys; from pypdf import PdfReader, PdfWriter; '
                 'reader=PdfReader(sys.argv[1]); writer=PdfWriter(); '
                 '[writer.add_page(page) for _ in range(3) for page in reader.pages]; '
                 'writer.write(sys.argv[2])')
    subprocess.run([str(PYTHON), '-c', make_book, str(BOOK), str(benchmark)],
                   env=ENV, cwd=SOURCE, check=True, timeout=settings.timeout_seconds)
    original_book=BOOK;BOOK=benchmark
    timings={}
    for device in BENCHMARK_DEVICES:
        benchjob=RUN/('benchmark-'+device)
        rec=cli('benchmark-12-'+device,convert(benchjob,device=device,extra=['--output',RUN/('benchmark-'+device+'.epub')]))
        state=assert_complete(rec,benchjob);timings[device]={'elapsed_seconds':rec['elapsed_seconds'],'counts':state['execution_summary']['counts']}
    result['benchmark_12_pages']={'source_sha256':sha(benchmark),'pdf_pages':12,'ocr_pages':9,'scope':'Single full CLI invocation including runtime integrity hashing, startup, first real profile, persistent sessions, PDF parsing, OCR, structure and EPUB export. Same PDF/options apart from device. Not a universal speed or accuracy claim.','measurements':timings}
    if 'cpu' in timings:
        for device in ('directml','cuda'):
            if device in timings:result['benchmark_12_pages']['cpu_over_'+device+'_ratio']=timings['cpu']['elapsed_seconds']/timings[device]['elapsed_seconds']
    BOOK=original_book
    assert SOURCE_HASHES=={name:sha(SOURCE/'src/local_epub'/name) for name in SOURCE_HASHES}, 'Production source changed during acceptance'
    result['pass']=True
except Exception as exc:
    result['failure']=repr(exc);result['traceback']=traceback.format_exc()
finally:
    (RUN/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pass':result['pass'],'run':str(RUN),'failure':result.get('failure'),'steps':result['steps']},ensure_ascii=False))

raise SystemExit(0 if result['pass'] else 1)
