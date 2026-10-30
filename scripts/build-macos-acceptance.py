#!/usr/bin/env python3
"""Target-host release checks; invoke build only after the release source freezes.

Each phase is explicit so a maintainer can run tests before source freeze and
leave build/signature evidence tied to the final source. The GUI uses real Cocoa
and QProcess, under the macOS kernel's deny network* policy, never offscreen.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
APP = ROOT / 'dist/LocalPDFtoEPUB.app'
ARCHIVE = ROOT / 'dist' / f'LocalPDFtoEPUB-{VERSION}-macos-arm64.zip'
POLICY = ['/usr/bin/sandbox-exec', '-f', str(ROOT / 'scripts/deny-network.sb')]
JAVA = '/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home/bin/java'
JAR = ROOT / 'tools/epubcheck-5.4.0/epubcheck.jar'
X = '{http://www.w3.org/1999/xhtml}'
E = '{http://www.idpf.org/2007/ops}'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def source_hashes():
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT/'src').rglob('*'))
            if p.is_file() and p.suffix in ('.py', '.json', '.txt') and '__pycache__' not in p.parts}


def clean_environment():
    removed = {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'QT_PLUGIN_PATH',
               'QT_QPA_PLATFORM', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH'}
    env = {k: v for k, v in os.environ.items() if k not in removed}
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', PYTHONIOENCODING='ascii')
    return env


def run(command, **kwargs):
    return subprocess.run([str(a) for a in command], cwd=ROOT, capture_output=True,
                          text=True, encoding='utf-8', errors='replace', **kwargs)


def tests(evidence):
    start = time.monotonic()
    before = source_hashes()
    command = [sys.executable, '-m', 'pytest', '-q']
    result = run(command)
    (evidence/'tests.log').write_text(result.stdout, encoding='utf-8')
    (evidence/'tests.stderr.log').write_text(result.stderr, encoding='utf-8')
    record = {'date': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
              'version': VERSION, 'command': command, 'returncode': result.returncode,
              'elapsed_seconds': round(time.monotonic()-start, 2),
              'summary': result.stdout.strip().splitlines()[-1] if result.stdout.strip() else '',
              'source_unchanged_during_test': before == source_hashes(), 'source_sha256': before}
    save(evidence/'tests.json', record)
    print(record['summary'], flush=True)
    result.check_returncode()
    assert record['source_unchanged_during_test'], 'Source changed during tests; rerun after freeze.'


def build(evidence):
    with (evidence/'macos-build.log').open('w', encoding='utf-8') as log:
        result = subprocess.run([sys.executable, 'scripts/build.py'], cwd=ROOT,
                                stdout=log, stderr=subprocess.STDOUT)
    result.check_returncode()
    shutil.copyfile(ROOT/'dist/build-info.json', evidence/'macos-build-info.json')
    print(f'Built {ARCHIVE.name}', flush=True)


def gui(evidence, repair=False):
    folder = evidence/('macos-page-number-repair-gui' if repair else 'macos-gui')
    folder.mkdir(parents=True, exist_ok=True)
    probe = '''import socket,json
results=[]
for host in ('1.1.1.1','127.0.0.1'):
 s=socket.socket();s.settimeout(2)
 try:s.connect((host,443));results.append({'host':host,'connected':True})
 except OSError as e:results.append({'host':host,'connected':False,'errno':e.errno,'error':str(e)})
 finally:s.close()
print(json.dumps(results))
raise SystemExit(0 if all(r.get('errno')==1 for r in results) else 1)
'''
    checked = run([*POLICY, sys.executable, '-c', probe])
    (folder/'network-probe.json').write_text(checked.stdout, encoding='utf-8')
    (folder/'network-probe.stderr.log').write_text(checked.stderr, encoding='utf-8')
    checked.check_returncode()
    executable = APP/'Contents/MacOS/LocalPDFtoEPUB'
    fixture = '04_page_number_gap.pdf' if repair else '02_mixed_bad_layer.pdf'
    command = [*POLICY, str(executable), 'gui-test', '--source', str(ROOT/'tests/fixtures'/fixture),
               '--resources', str(ROOT/'resources'), '--output', str(folder/'book.epub'),
               '--evidence', str(folder/'gui'), '--verify-page-number-repair' if repair else '--verify-cancel']
    if not repair:
        command.append('--verify-rebuild-layout')
    observed, samples, errors = {}, [], []
    started = time.monotonic()
    with (folder/'app.stdout.log').open('w', encoding='utf-8') as stdout, (folder/'app.stderr.log').open('w', encoding='utf-8') as stderr:
        proc = subprocess.Popen(command, cwd=ROOT, env=clean_environment(), stdout=stdout, stderr=stderr)
        while proc.poll() is None:
            if time.monotonic()-started > 600:
                proc.terminate()
                errors.append('GUI acceptance exceeded 600 seconds')
                break
            tree = run(['/bin/ps', '-axo', 'pid=,ppid=,comm='])
            records = {}
            if tree.returncode:
                errors.append('ps: ' + tree.stderr)
            for line in tree.stdout.splitlines():
                fields = line.strip().split(None, 2)
                if len(fields) == 3:
                    records[int(fields[0])] = (int(fields[1]), fields[2])
            pids = {proc.pid}
            for _ in range(8):
                pids |= {pid for pid, (parent, _) in records.items() if parent in pids}
            observed.update({str(pid): records[pid][1] for pid in pids if pid in records})
            sockets = run(['/usr/sbin/lsof', '-nP', '-a', '-p', ','.join(map(str, sorted(pids))), '-i'])
            if sockets.returncode not in (0, 1):
                errors.append('lsof: ' + sockets.stderr)
            samples.append({'time': time.time(), 'pids': sorted(pids), 'internet_sockets': sockets.stdout})
            time.sleep(.05)
        code = proc.wait(timeout=15)
    audit = {'version': VERSION, 'command': command, 'exit_code': code,
             'network_policy': 'macOS deny network* inherited by actual Cocoa GUI and frozen workers',
             'initial_pythonioencoding': 'ascii', 'developer_paths_removed': True,
             'application_zip_sha256': sha(ARCHIVE), 'processes': observed, 'samples': samples,
             'errors': errors, 'connected_sockets_observed': any(s['internet_sockets'].strip() for s in samples),
             'sampling_limit': 'Short-lived workers can finish between ps/lsof samples; all inherit kernel denial. Actual QProcess worker list is in gui/result.json.'}
    save(folder/'process-audit.json', audit)
    assert code == 0 and not errors and not audit['connected_sockets_observed'], audit
    result = json.loads((folder/'gui/result.json').read_text(encoding='utf-8'))
    assert result['frozen'] and result['platform_plugin'] == 'cocoa' and result['status'] == 'done', result
    expected = ['stable_native_items_on_export', 'running_margins_available_default_off',
                'running_margins_on_off_reexport_tested',
                'body_only_without_page_info_appendix', 'export_kept_parse_cache']
    expected += ['page_number_repair_tested'] if repair else ['cancellation_and_resume_tested', 'scanned_page_numbers_verified',
                                                            'layout_rebuild_requested', 'layout_rebuild_tested']
    for key in expected:
        assert result[key], (key, result)
    if not repair:
        assert all(result['layout_rebuild_evidence'][key] for key in
                   ('raw_unchanged', 'unselected_pages_unchanged', 'manual_edit_preserved')), result
    assert result['worker_executable'] == str(APP/'Contents/MacOS/LocalPDFtoEPUB-CLI')
    assert all(w['executable'] == result['worker_executable'] for w in result['workers'])
    # A just-exited macOS process may appear with a truncated name in ps.
    # Correlate actual PIDs with QProcess records, retaining the raw ps names.
    audit['observed_worker_pids'] = sorted(w['pid'] for w in result['workers'] if str(w['pid']) in observed)
    save(folder/'process-audit.json', audit)
    assert audit['observed_worker_pids'], 'No frozen worker PID observed in process samples.'
    check_targets = [(folder/'book.epub', 'epubcheck.json'),
                     (folder/'gui/running-margins.epub', 'epubcheck-running-margins.json')]
    if repair:
        check_targets.append((folder/'gui/page-number-confirmed.epub', 'epubcheck-confirmed-page-number.json'))
    for target, record_name in check_targets:
        check_command = [JAVA, '-jar', JAR, target]
        checked = run(check_command)
        save(folder/record_name, {'version': '5.4.0', 'command': list(map(str, check_command)),
                                  'epub_sha256': sha(target), 'returncode': checked.returncode,
                                  'stdout': checked.stdout, 'stderr': checked.stderr,
                                  'scope': 'format only; running-page rendering is independently verified in the compatible reader'})
        checked.check_returncode()
        assert '0 fatals / 0 errors / 0 warnings / 0 infos' in checked.stdout, checked.stdout
    print(f'Frozen Cocoa {fixture}: GUI/edit/cache, kernel network denial, EPUBCheck passed.', flush=True)


def verify(evidence):
    build_info = json.loads((evidence/'macos-build-info.json').read_text(encoding='utf-8'))
    assert source_hashes() == build_info['source_sha256'], 'Source changed after build.'
    signature = run(['/usr/bin/codesign', '--verify', '--deep', '--strict', APP])
    signature.check_returncode()
    with (APP/'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    assert info['CFBundleShortVersionString'] == VERSION
    archive_digest = sha(ARCHIVE)
    with tempfile.TemporaryDirectory(prefix=f'local-epub-{VERSION}-archive-', dir='/private/tmp') as temp:
        extracted = Path(temp)
        run(['/usr/bin/ditto', '-x', '-k', ARCHIVE, extracted]).check_returncode()
        app = extracted/APP.name
        links = [p for p in app.rglob('*') if p.is_symlink()]
        broken = [str(p.relative_to(app)) for p in links if not p.exists()]
        assert not broken, broken
        signature2 = run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
        signature2.check_returncode()
        cli = app/'Contents/MacOS/LocalPDFtoEPUB-CLI'
        help_result = run([cli, '--help'], env=clean_environment())
        help_result.check_returncode()
        inspect = run([*POLICY, cli, 'inspect', ROOT/'tests/fixtures/01_chinese_crosspage.pdf'], env=clean_environment())
        inspect.check_returncode()
        assert json.loads(inspect.stdout)['page_count'] == 2
        bundled_docs = {name: sha(app/'Contents/Resources/docs'/name) for name in ('THIRD_PARTY.md', 'PLATFORMS.md')}
        assert all(digest == sha(ROOT/'docs'/name) for name, digest in bundled_docs.items())
        reader_files = {}
        for local, bundled in (('vendor/reader', 'reader'), ('vendor/sources', 'reader-sources')):
            resource = app/'Contents/Resources'/bundled/'vivliostyle-2.45.2'
            original = ROOT/local/'vivliostyle-2.45.2'
            assert sha(resource/'manifest.json') == sha(original/'manifest.json')
            manifest = json.loads((resource/'manifest.json').read_text(encoding='utf-8'))
            entries = manifest['files']
            if isinstance(entries, dict):
                entries = [dict(value, path=key) for key, value in entries.items()]
            for entry in entries:
                relative = Path(entry['path'])
                assert not relative.is_absolute() and '..' not in relative.parts
                file = resource/relative
                assert file.stat().st_size == entry['bytes'] and sha(file) == entry['sha256']
                assert sha(file) == sha(original/relative)
            reader_files[bundled] = {'manifest_sha256': sha(resource/'manifest.json'),
                                     'files_verified': len(entries)}
        extraction = {'version': VERSION, 'archive': str(ARCHIVE.relative_to(ROOT)), 'archive_sha256': archive_digest,
                      'isolated_temporary_directory': True, 'codesign_returncode': signature2.returncode,
                      'codesign_output': signature2.stdout+signature2.stderr,
                      'cli_help_returncode': help_result.returncode, 'cli_help_stdout': help_result.stdout,
                      'cli_help_stderr': help_result.stderr, 'cli_inspect_with_ascii_environment_returncode': inspect.returncode,
                      'cli_inspect_utf8_stdout': inspect.stdout, 'cli_inspect_stderr': inspect.stderr,
                      'symlinks_checked': len(links), 'broken_symlinks': broken, 'developer_paths_removed': True,
                      'bundled_document_sha256': bundled_docs, 'bundled_reader_and_corresponding_source': reader_files}
        save(evidence/'macos-archive-extraction-check.json', extraction)
    folder = evidence/'macos-gui'
    report = json.loads((folder/'book.report.json').read_text(encoding='utf-8'))
    gui_result = json.loads((folder/'gui/result.json').read_text(encoding='utf-8'))
    repair_folder = evidence/'macos-page-number-repair-gui'
    repair_result = json.loads((repair_folder/'gui/result.json').read_text(encoding='utf-8'))
    assert repair_result['page_number_repair_tested'] and repair_result['frozen']
    assert repair_result['page_number_confirmed_record']['provenance'] == 'user_confirmed_inference'
    with zipfile.ZipFile(folder/'book.epub') as book:
        chapters = [name for name in book.namelist() if name.startswith('EPUB/chapter-') and name.endswith('.xhtml')]
        body = ''.join(''.join(ET.fromstring(book.read(name)).itertext()) for name in chapters)
        xmls = [ET.fromstring(book.read(name)) for name in book.namelist() if name.endswith('.xhtml')]
        checks = {'body_chapter_count': len(chapters), 'page_information_appendix_absent': 'EPUB/page-info.xhtml' not in book.namelist(),
                  'page_list_and_pagebreak_absent': all(not ({'page-list','pagebreak'} & set(n.get(E+'type','').split())) for r in xmls for n in r.iter()),
                  'manual_edit_retained': gui_result['manual_edit_marker'] in body,
                  'representative_ocr_text_retained': '青山不改' in body and '春风又绿江南岸' in body,
                  'eight_page_margin_blocks_omitted_from_output': report['content']['margin_omitted_count'] == 8,
                  'no_automatic_body_exclusion_list': report['content']['excluded_blocks'] == []}
    assert checks['body_chapter_count'] == 4 and all(value for key,value in checks.items() if key != 'body_chapter_count'), checks
    retained = folder/'gui/running-margins.epub'
    with zipfile.ZipFile(retained) as book:
        roots = [ET.fromstring(book.read(name)) for name in book.namelist()
                 if name.startswith('EPUB/chapter-') and name.endswith('.xhtml')]
        markers = [node for root in roots for node in root.iter(X+'span') if node.get('data-srcpage') is not None]
        nav = ET.fromstring(book.read('EPUB/nav.xhtml'))
        retained_checks = {
            'no_page_information_appendix': 'EPUB/page-info.xhtml' not in book.namelist(),
            'source_markers_empty': bool(markers) and all(not ''.join(node.itertext()) for node in markers),
            'source_page_labels_1_to_4': {'1','2','3','4'} <= {node.get('data-srcpage') for node in markers},
            'page_list_navigation': any(n.get(E+'type') == 'page-list' and n.get('hidden') is not None for n in nav.iter()),
            'manual_text_retained': gui_result['manual_edit_marker'] in ''.join(''.join(r.itertext()) for r in roots),
        }
    with zipfile.ZipFile(repair_folder/'gui/page-number-confirmed.epub') as book:
        nodes = [node for name in book.namelist() if name.endswith('.xhtml')
                 for node in ET.fromstring(book.read(name)).iter()]
        retained_checks['confirmed_22_in_footer_data_as_human_inference'] = any(
            node.get('data-srcpage') == '22' and node.get('data-page-number-origin') == 'user_confirmed_inference'
            for node in nodes)
    assert all(retained_checks.values()), retained_checks
    video = [p.name for p in APP.rglob('*') if p.is_file() and p.suffix == '.dylib' and any(s in p.name.lower() for s in ('avcodec','avformat','ffmpeg','x264','x265'))]
    assert not video
    integrity = {'version': VERSION, 'platform': platform.platform(), 'source_hashes_match_current_source': True,
                 'ad_hoc_codesign_deep_strict_returncode': signature.returncode,
                 'archive': {'name': ARCHIVE.name, 'bytes': ARCHIVE.stat().st_size, 'sha256': archive_digest},
                 'opencv_local_build': build_info['opencv_runtime']['local_build']['local_build'],
                 'unexpected_video_libraries': video, 'developer_certificate_signed': False, 'notarized': False,
                 'bundled_document_sha256': bundled_docs, 'bundled_reader_and_corresponding_source': reader_files,
                 'book_content_checks': checks, 'running_margin_output_checks': retained_checks,
                 'frozen_gui_acceptance': str((folder/'gui/result.json').relative_to(ROOT)),
                 'frozen_gui_network_audit': str((folder/'process-audit.json').relative_to(ROOT)),
                 'frozen_gui_epubcheck': str((folder/'epubcheck.json').relative_to(ROOT)),
                 'frozen_page_number_repair_acceptance': str((repair_folder/'gui/result.json').relative_to(ROOT)),
                 'frozen_page_number_repair_network_audit': str((repair_folder/'process-audit.json').relative_to(ROOT)),
                 'frozen_page_number_repair_epubcheck': str((repair_folder/'epubcheck.json').relative_to(ROOT)),
                 'archive_extraction_check': str((evidence/'macos-archive-extraction-check.json').relative_to(ROOT))}
    save(evidence/'macos-package-integrity.json', integrity)
    checksum_file = ROOT/'dist/SHA256SUMS'
    lines = checksum_file.read_text(encoding='utf-8').splitlines() if checksum_file.exists() else []
    lines = [line for line in lines if not line.endswith('  '+ARCHIVE.name)]
    lines.append(f'{archive_digest}  {ARCHIVE.name}')
    checksum_file.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps(integrity['archive']), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('tests','build','gui','gui-repair','verify'))
    parser.add_argument('--evidence', type=Path, default=ROOT/'artifacts'/f'release-{VERSION}')
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('This target-host harness requires macOS arm64.')
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    if args.phase == 'gui-repair':
        gui(evidence, repair=True)
    else:
        globals()[args.phase](evidence)


if __name__ == '__main__':
    main()
