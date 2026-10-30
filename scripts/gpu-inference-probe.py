#!/usr/bin/env python3
"""Real sample OCR + per-model ORT execution profiling; no app source patching."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata as md
import json
from pathlib import Path
import sys
import time

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def loaded_dlls():
    import ctypes
    from ctypes import wintypes
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = kernel.GetCurrentProcess()
    modules = (wintypes.HMODULE * 4096)()
    needed = wintypes.DWORD()
    psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    if not psapi.EnumProcessModules(handle, modules, ctypes.sizeof(modules), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    result = []
    for module in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
        name = ctypes.create_unicode_buffer(32768)
        if psapi.GetModuleFileNameExW(handle, module, name, len(name)):
            value = name.value
            if any(x in value.lower() for x in ('onnxruntime', 'directml', 'd3d12', 'dxgi', 'nvidia', 'cuda', 'cudnn', 'cublas', 'cufft', 'nvrtc', 'curand', 'vcruntime', 'msvcp')):
                result.append(value)
    return result

def normalize(text):
    return ''.join(c for c in text if not c.isspace())

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--resources', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', choices=('cpu', 'cuda', 'directml'), required=True)
    p.add_argument('--repeat', type=int, default=2)
    p.add_argument('--images-dir', type=Path, help='Reuse rendered synthetic PDF pages; embedded runtime need not include PDFium')
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    import onnxruntime as ort
    ort.disable_telemetry_events()
    if a.device == 'cuda':
        ort.preload_dlls(directory='')
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    from rapidocr_onnxruntime.utils.infer_engine import OrtInferSession
    init_options = OrtInferSession._init_sess_opts
    init_providers = OrtInferSession._get_ep_list
    def profiled(config):
        options = init_options(config)
        options.enable_profiling = True
        options.profile_file_prefix = str(a.output / Path(config['model_path']).stem)
        if a.device == 'directml':
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        return options
    OrtInferSession._init_sess_opts = staticmethod(profiled)
    def configured_providers(session):
        providers = init_providers(session)
        return [(name, {'device_id': 0} if name == 'DmlExecutionProvider' else
                 {**options, 'use_tf32': 0} if name == 'CUDAExecutionProvider' else options)
                for name, options in providers]
    OrtInferSession._get_ep_list = configured_providers
    use_cuda = a.device == 'cuda'
    start = time.perf_counter()
    engine = RapidOCR(**{kind + '_model_path': str(a.resources / (kind + '.onnx')) for kind in ('det', 'rec', 'cls')},
                      det_use_cuda=use_cuda, rec_use_cuda=use_cuda, cls_use_cuda=use_cuda,
                      det_use_dml=a.device == 'directml', rec_use_dml=a.device == 'directml', cls_use_dml=a.device == 'directml',
                      intra_op_num_threads=2, inter_op_num_threads=1, text_score=.1, print_verbose=False)
    initialization = time.perf_counter() - start
    sessions = {'det': engine.text_det.infer.session, 'rec': engine.text_rec.session.session,
                'cls': engine.text_cls.infer.session}
    expected_provider = {'cpu':'CPUExecutionProvider','cuda':'CUDAExecutionProvider','directml':'DmlExecutionProvider'}[a.device]
    if any(s.get_providers()[0] != expected_provider for s in sessions.values()):
        raise RuntimeError('Requested provider session creation fell back')
    truth = json.loads(a.source.with_suffix('.truth.json').read_text(encoding='utf-8'))
    images = []
    if a.images_dir:
        from PIL import Image
        for number in (2, 3, 4):
            with Image.open(a.images_dir/f'page-{number}.png') as image:
                images.append((number,np.asarray(image.convert('RGB'))))
    else:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(a.source))
        for number in (2, 3, 4):
            page = pdf[number - 1]
            bitmap = page.render(scale=180/72)
            pil = bitmap.to_pil().convert('RGB')
            images.append((number, np.asarray(pil)))
            pil.save(a.output / f'page-{number}.png')
            bitmap.close()
            page.close()
        pdf.close()
    records = []
    # Warm-up is measured separately; repeated real images do not stand in for
    # separate publications or corpus-wide accuracy claims.
    for iteration in range(a.repeat + 1):
        for number, image in images:
            start = time.perf_counter()
            result, elapsed = engine(image)
            wall = time.perf_counter() - start
            text = '\n'.join(row[1] for row in result or [])
            expected = truth['pages'][number - 1]['ordered_fragments']
            positions = [normalize(text).find(normalize(fragment)) for fragment in expected]
            passed = all(pos >= 0 for pos in positions) and positions == sorted(positions)
            records.append({'page': number, 'iteration': iteration, 'warmup': iteration == 0,
                            'wall_seconds': wall, 'stage_seconds': [float(t) for t in elapsed],
                            'text': text, 'truth_positions': positions, 'truth_passed': passed,
                            'lines': [{'text': r[1], 'confidence': float(r[2]),
                                       'confidence_source': 'RapidOCR recognition'} for r in result or []]})
            print(json.dumps({'page': number, 'iteration': iteration, 'device': a.device,
                              'wall_seconds': wall, 'truth_passed': passed}, ensure_ascii=False), flush=True)
    profiles = {}
    for kind, session in sessions.items():
        profile = Path(session.end_profiling())
        events = json.loads(profile.read_text(encoding='utf-8'))
        node_events = [e for e in events if e.get('cat') == 'Node' and e.get('args', {}).get('provider')]
        counts = Counter(e['args']['provider'] for e in node_events)
        cuda_ops = Counter(e['args'].get('op_name', '?') for e in node_events if e['args']['provider'] == 'CUDAExecutionProvider')
        requested_ops = Counter(e['args'].get('op_name', '?') for e in node_events if e['args']['provider'] == expected_provider)
        profiles[kind] = {'path': str(profile), 'sha256': sha(profile), 'providers': session.get_providers(),
                          'node_execution_count': dict(counts), 'cuda_operations': dict(cuda_ops),
                          'provider_options': session.get_provider_options(),
                          'requested_provider_operations': dict(requested_ops),
                          'actual_requested_provider_compute': any(op not in ('MemcpyFromHost', 'MemcpyToHost') for op in requested_ops),
                          'actual_cuda_compute': any(op not in ('MemcpyFromHost', 'MemcpyToHost') for op in cuda_ops)}
    record = {'observed_at': datetime.now(timezone.utc).isoformat(), 'device': a.device,
              'python': sys.executable, 'python_version': sys.version, 'ort_version': ort.__version__,
              'rapidocr_version': md.version('rapidocr-onnxruntime'), 'available_providers': ort.get_available_providers(),
              'source_sha256': sha(a.source), 'model_sha256': {n: sha(a.resources/(n+'.onnx')) for n in sessions},
              'initialization_seconds': initialization, 'profiles': profiles, 'samples': records,
              'loaded_dlls': loaded_dlls(), 'telemetry_explicitly_disabled': True,
              'python_path': sys.path, 'isolated': bool(sys.flags.isolated),
              'no_site': bool(sys.flags.no_site), 'no_bytecode': bool(sys.dont_write_bytecode),
              'actual_cuda_execution_all_three_models': all(x['actual_cuda_compute'] for x in profiles.values()),
              'actual_requested_provider_execution_all_three_models': all(x['actual_requested_provider_compute'] for x in profiles.values()),
              'expected_provider': expected_provider,
              'image_inputs': [{'page': number, 'rgb_sha256': hashlib.sha256(image.tobytes()).hexdigest(),
                                'shape': list(image.shape), 'dtype': str(image.dtype)} for number,image in images],
              'cuda_tf32_disabled': a.device == 'cuda',
              'dml_sequential_memory_pattern_disabled': a.device == 'directml',
              'truth_all_passed': all(x['truth_passed'] for x in records),
              'no_cpu_release_files_modified': True}
    (a.output/'result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    if not record['truth_all_passed'] or not record['actual_requested_provider_execution_all_three_models']:
        raise RuntimeError('Real OCR truth or CUDA profile failed; inspect result.json')
    print(json.dumps({'pass': True, 'device': a.device, 'result': str(a.output/'result.json')}), flush=True)

if __name__ == '__main__':
    main()
