"""Read-only review of a windows-onnx-acceptance.py result directory.

Checks actual per-page content and ONNX compute evidence without running OCR.
No machine paths, adapter names, historical pass counts or timings are assumed.
This does not establish recognition accuracy, EPUBCheck validity or OS isolation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def check(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(page):
    return [(block['kind'], block.get('text'), block.get('rows')) for block in page['blocks']]


def compute_models(execution):
    models = {}
    check(set(execution['models']) == {'det', 'rec', 'cls'}, 'Three model profiles required')
    provider = {'directml': 'DmlExecutionProvider', 'cuda': 'CUDAExecutionProvider'}[execution['identity']['kind']]
    for name, model in execution['models'].items():
        operations = model['accelerator_operations']
        total = sum(count for op, count in operations.items()
                    if op != 'unknown' and 'copy' not in op.casefold() and 'memcpy' not in op.casefold())
        check(total > 0 and total == model['accelerator_compute_nodes'], f'{name}: no valid accelerator compute evidence')
        check(model['node_counts'].get(provider, 0) >= total, f'{name}: provider node count inconsistent')
        models[name] = {'compute_nodes_excluding_copy_and_memcpy': total, 'operations': operations,
                        'node_counts': model['node_counts'], 'profile_sha256': model['profile_sha256'],
                        'scope': model['scope']}
    return models


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, required=True, help='Evidence directory containing result.json and benchmark-* task directories.')
    parser.add_argument('--output', type=Path, help='New JSON file (default: RUN/review.json); no overwrite.')
    parser.add_argument('--source', type=Path, help='Optional checkout whose four production source hashes must equal the recorded run.')
    settings = parser.parse_args()
    run = settings.run.expanduser().resolve()
    output = settings.output.expanduser().resolve() if settings.output else run / 'review.json'
    if output.exists():
        parser.error('--output already exists; choose a new file to preserve evidence.')
    review = {'pass': False, 'run': str(run), 'scope': 'Read-only content/profile evidence review; no OCR or runtime changes.'}
    try:
        result = read(run / 'result.json')
        check(result.get('pass'), 'Original acceptance did not pass')
        benchmark = result['benchmark_12_pages']
        devices = list(benchmark['measurements'])
        check(bool(devices), 'No benchmark outputs recorded')
        check(all(device in ('cpu', 'directml', 'cuda') for device in devices), 'Unexpected benchmark device')
        docs = {device: read(run / f'benchmark-{device}/document.json') for device in devices}
        check(hashlib.sha256((run / 'mixed-12.pdf').read_bytes()).hexdigest() == benchmark['source_sha256'], 'Benchmark PDF digest changed')
        source_hashes = result.get('source_hashes', {})
        if settings.source:
            source = settings.source.expanduser().resolve()
            hashes = {name: hashlib.sha256((source / 'src/local_epub' / name).read_bytes()).hexdigest()
                      for name in ('backend.py', 'pipeline.py', 'gpu_execution.py', 'gpu_worker.py')}
            check(hashes == source_hashes, 'Current source does not match recorded acceptance source')
        comparisons = []
        for index in range(benchmark['pdf_pages']):
            pages = {device: doc['pages'][index] for device, doc in docs.items()}
            check(all(page['number'] == index + 1 and page['status'] == 'success' for page in pages.values()), f'Page {index + 1}: missing or failed')
            first = canonical(next(iter(pages.values())))
            same = all(canonical(page) == first for page in pages.values())
            check(same, f'Page {index + 1}: ordered block kind/text/rows differ between backends')
            comparisons.append({'page': index + 1, 'ordered_kind_text_rows_equal': same,
                                'methods': {device: page['method'] for device, page in pages.items()}})
        profiles = {}
        for device, doc in docs.items():
            check(not doc['missing_pages'] and len(doc['pages']) == benchmark['pdf_pages'], f'{device}: incomplete source coverage')
            if device == 'cpu':
                check(all(page['execution']['identity']['kind'] in ('cpu', 'native') for page in doc['pages']), 'CPU benchmark used a different backend')
                continue
            executions = [(page['number'], page['execution']) for page in doc['pages'] if page['execution']['identity']['kind'] == device]
            check(len(executions) == benchmark['ocr_pages'], f'{device}: accelerator page count differs')
            number, first = executions[0]
            check(first['evidence'] == 'onnxruntime-first-page-model-profile', f'{device}: missing initial real profile')
            models = compute_models(first)
            if source_hashes:
                check(first['identity']['worker_sha256'] == source_hashes['gpu_worker.py'], f'{device}: worker source differs from recorded run')
            for page_number, execution in executions:
                check(execution['verified'] and execution['verification_page'] == number, f'{device} page {page_number}: unverified identity')
                check(execution['identity'] == first['identity'], f'{device} page {page_number}: identity changed')
                compute_models(execution)
                if page_number != number:
                    check(execution['evidence'] == 'same-verified-session-successful-inference', f'{device}: missing same-session execution evidence')
                    check(execution['models_scope'] == 'verification-profile-not-current-page-counters', f'{device}: profile reuse scope is ambiguous')
                    check(sum(execution['current_page_model_calls'].values()) > 0, f'{device}: no current page model calls')
            profiles[device] = {'identity': first['identity'], 'verification_page': number,
                                'models': models, 'later_verified_session_pages': [n for n, _ in executions[1:]],
                                'later_profiles_are_not_current_page_samples': True}
        review.update({'pass': True, 'source_hashes': source_hashes,
                       'functional_backend': result['backend'], 'functional_steps': result['steps'],
                       'benchmark': benchmark,
                       'content': {'compared_devices': devices, 'comparisons': comparisons,
                                   'comparison_scope': 'Per-page ordered block kind/text/rows; not an accuracy guarantee.',
                                   'totals': {device: {'pages': len(doc['pages']), 'blocks': len(doc['blocks']),
                                               'text_characters': sum(len(block.get('text') or '') for block in doc['blocks'])}
                                              for device, doc in docs.items()}},
                       'profiles': profiles,
                       'limits': ['Timings are single complete CLI runs on the recorded hardware and fixture, not universal speed guarantees.',
                                  'The recovery matrix applies to functional_backend; additional benchmark backends do not imply independently repeated recovery matrices.',
                                  'Some auxiliary operators may execute on CPU; real GPU compute does not mean all nodes execute on GPU.',
                                  'OS firewall, actual loaded DLL paths, GUI acceptance and EPUBCheck require separate evidence.']})
    except Exception as exc:
        review['failure'] = str(exc)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(review, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'pass': review['pass'], 'output': str(output), 'failure': review.get('failure')}, ensure_ascii=False))
    return 0 if review['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
