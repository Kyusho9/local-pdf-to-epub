"""Standalone ONNX Runtime OCR worker in a private GPU runtime.

This file is also bundled as plain source. It deliberately does not import the
application package (or its CPU ONNX Runtime). Protocol data stays on stdio and
all images, models and profiles are local files supplied by the parent.
"""
from __future__ import annotations

from collections import Counter
import contextlib
import ctypes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import time
import uuid

PROTOCOL_VERSION = 1
WORKER_VERSION = "onnx-ocr-2-persistent-sessions"


def profile_summary(events, execution_provider="CUDAExecutionProvider"):
    """Count executed nodes, not advertised/registered execution providers."""
    providers, operations = Counter(), Counter()
    for event in events:
        args = event.get("args", {})
        provider = args.get("provider")
        if event.get("cat") != "Node" or not provider:
            continue
        providers[provider] += 1
        if provider == execution_provider:
            operations[str(args.get("op_name", "unknown"))] += 1
    compute = sum(n for name, n in operations.items()
                  if name != "unknown" and "copy" not in name.casefold() and "memcpy" not in name.casefold())
    result = {"node_counts": dict(providers), "accelerator_operations": dict(operations),
              "accelerator_compute_nodes": compute, "executed": bool(providers)}
    prefix = "cuda" if execution_provider == "CUDAExecutionProvider" else "directml"
    result[prefix + "_operations"] = dict(operations)
    result[prefix + "_compute_nodes"] = compute
    return result


def _offline():
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    def denied(*_args, **_kwargs):
        raise RuntimeError("GPU OCR 子进程禁止网络连接")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied


def _device_info(index):
    # The driver is an OS prerequisite; all user-mode CUDA/cuDNN DLLs are local.
    driver = ctypes.WinDLL("nvcuda.dll", winmode=0x00000800)
    def checked(name, *args):
        result = getattr(driver, name)(*args)
        if result:
            raise RuntimeError(f"NVIDIA 驱动 {name} 失败（CUDA 错误 {result}）")
    checked("cuInit", 0)
    count, device, version = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    checked("cuDeviceGetCount", ctypes.byref(count))
    if not 0 <= index < count.value:
        raise RuntimeError("找不到所选 NVIDIA CUDA 显卡")
    checked("cuDeviceGet", ctypes.byref(device), index)
    name = ctypes.create_string_buffer(256)
    checked("cuDeviceGetName", name, len(name), device)
    checked("cuDriverGetVersion", ctypes.byref(version))
    memory, major, minor = ctypes.c_size_t(), ctypes.c_int(), ctypes.c_int()
    checked("cuDeviceTotalMem_v2", ctypes.byref(memory), device)
    checked("cuDeviceComputeCapability", ctypes.byref(major), ctypes.byref(minor), device)
    uuid = (ctypes.c_ubyte * 16)()
    checked("cuDeviceGetUuid", ctypes.byref(uuid), device)
    return {"index": index, "name": name.value.decode("utf-8", errors="replace"),
            "total_memory_bytes": memory.value, "driver_cuda_api": version.value,
            "compute_capability": [major.value, minor.value], "uuid": bytes(uuid).hex()}


def _directml_device_info(index=0):
    """Resolve the exact DXGI EnumAdapters index passed to ORT's DML EP.

    QueryInterface supplies GetDesc1/software status without assuming that
    NVIDIA's device numbering matches DXGI (or that this is an NVIDIA GPU).
    """
    from ctypes import wintypes
    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16),
                    ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]
    class LUID(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.LONG)]
    class DESC(ctypes.Structure):
        _fields_ = [("name", wintypes.WCHAR * 128), ("vendor", wintypes.UINT),
                    ("device", wintypes.UINT), ("subsystem", wintypes.UINT), ("revision", wintypes.UINT),
                    ("video", ctypes.c_size_t), ("system", ctypes.c_size_t), ("shared", ctypes.c_size_t),
                    ("luid", LUID), ("flags", wintypes.UINT)]
    def iid(value):
        return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)
    def method(pointer, slot, result, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *args)(table[slot])
    def checked(code):
        if code < 0:
            raise RuntimeError(f"DXGI 适配器查询失败：0x{code & 0xffffffff:08x}")
    dxgi = ctypes.WinDLL("dxgi.dll", winmode=0x00000800)
    create = dxgi.CreateDXGIFactory1
    create.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_long
    factory, adapter, extended = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        checked(create(ctypes.byref(iid("770aae78-f26f-4dba-a829-253c83d1b387")), ctypes.byref(factory)))
        checked(method(factory, 7, ctypes.c_long, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(factory, index, ctypes.byref(adapter)))
        checked(method(adapter, 0, ctypes.c_long, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            adapter, ctypes.byref(iid("29038f61-3839-4626-91fd-086879011a05")), ctypes.byref(extended)))
        description = DESC()
        checked(method(extended, 10, ctypes.c_long, ctypes.POINTER(DESC))(extended, ctypes.byref(description)))
        return {"index": index, "name": description.name, "vendor_id": description.vendor,
                "device_pci_id": description.device, "dedicated_video_bytes": description.video,
                "software": bool(description.flags & 2),
                "luid": f"{description.luid.high & 0xffffffff:08x}:{description.luid.low:08x}",
                "binding": "explicit DML device_id; IDXGIFactory.EnumAdapters + IDXGIAdapter1.GetDesc1",
                "driver_version": None}
    finally:
        for pointer in (extended, adapter, factory):
            if pointer:
                method(pointer, 2, wintypes.ULONG)(pointer)


class _TrackedSession:
    def __init__(self, session, worker, name):
        self.session, self.worker, self.name = session, worker, name

    def __getattr__(self, name):
        return getattr(self.session, name)

    def run(self, outputs, inputs, *args, **kwargs):
        if self.session.get_providers()[0] != self.worker.provider:
            raise RuntimeError("模型会话 provider 发生变化；拒绝静默改用 CPU")
        result = self.session.run(outputs, inputs, *args, **kwargs)
        if self.session.get_providers()[0] != self.worker.provider:
            raise RuntimeError("模型执行期间 provider 发生变化")
        self.worker.model_calls[self.name] += 1
        return result


class Worker:
    def __init__(self):
        self.engine = None
        self.sessions = {}
        self.models = {}
        self.identity = None
        self.page = None
        self.calls = 0
        self.model_calls = {name: 0 for name in ("det", "rec", "cls")}
        self.verification = None
        self.verification_page = None

    def initialize(self, request):
        if sys.platform != "win32" or sys.version_info[:2] != (3, 12):
            raise RuntimeError("此 GPU 运行包仅支持 Windows 64 位 Python 3.12")
        if request.get("protocol") != PROTOCOL_VERSION:
            raise ValueError("GPU worker 协议版本不兼容")
        self.backend = request.get("backend", "cuda")
        if self.backend not in ("cuda", "directml"):
            raise ValueError("未知 ONNX Runtime 执行后端")
        self.provider = "CUDAExecutionProvider" if self.backend == "cuda" else "DmlExecutionProvider"
        self.directory = Path(request["work_dir"]).resolve(strict=True)
        if not self.directory.is_dir():
            raise ValueError("GPU 临时目录无效")
        for kind in ("det", "rec", "cls"):
            item = request["models"][kind]
            path = Path(item["path"]).resolve(strict=True)
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("GPU 模型校验失败：" + kind)
            self.models[kind] = path
        # The parent has verified this private runtime's complete manifest.
        # Retain these handles for deferred cuDNN/NVRTC kernel dependencies;
        # never discover CUDA through the user's PATH or CUDA_PATH.
        private_nvidia = Path(sys.executable).resolve().parent / "packages" / "nvidia"
        self.dll_handles = ([os.add_dll_directory(str(path))
                             for path in sorted(private_nvidia.glob("*/bin")) if path.is_dir()]
                            if self.backend == "cuda" else [])
        import onnxruntime as ort
        ort.disable_telemetry_events()
        if ort.__version__ != "1.22.0" or importlib.metadata.version("rapidocr-onnxruntime") != "1.4.4":
            raise RuntimeError("GPU worker 需要对应 ONNX Runtime 1.22.0 与 RapidOCR 1.4.4")
        if self.backend == "cuda":
            ort.preload_dlls(directory="")
        if self.provider not in ort.get_available_providers():
            raise RuntimeError("本地 ONNX Runtime 未提供 " + self.provider)
        self.device = _device_info(0) if self.backend == "cuda" else _directml_device_info(0)
        if self.device.get("software"):
            raise RuntimeError("DXGI 默认适配器是软件渲染器，不能声称 DirectML GPU 加速")
        distribution = "onnxruntime-gpu" if self.backend == "cuda" else "onnxruntime-directml"
        versions = {name: importlib.metadata.version(name) for name in
                    (distribution, "rapidocr-onnxruntime", "numpy", "opencv-python", "Pillow")}
        self.identity = {"kind": self.backend, "worker": WORKER_VERSION,
                         "protocol": PROTOCOL_VERSION, "python": sys.version.split()[0],
                         "versions": versions, "hardware": self.device,
                         "providers": [self.provider, "CPUExecutionProvider"],
                         "provider_options": ({"device_id": 0, "use_tf32": 0} if self.backend == "cuda" else {"device_id": 0}),
                         "session_policy": "persistent-first-page-profile-then-verified-session-runs",
                         "threads": {"intra": 2, "inter": 1}}
        # Session construction can still fail despite the provider being listed.
        self._new_engine()
        return {"identity": self.identity, "provider_available_only": True}

    def _new_engine(self):
        import onnxruntime as ort
        from rapidocr_onnxruntime import RapidOCR
        from rapidocr_onnxruntime.utils.infer_engine import OrtInferSession
        original = OrtInferSession._init_sess_opts
        original_providers = OrtInferSession._get_ep_list
        prefix = self.directory / ("profile-" + str(time.time_ns()))
        def options(config):
            value = original(config)
            value.enable_profiling = True
            if self.backend == "directml":
                value.enable_mem_pattern = False
                value.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            value.profile_file_prefix = str(prefix) + "-" + Path(config["model_path"]).stem
            return value
        OrtInferSession._init_sess_opts = staticmethod(options)
        def providers(session):
            return [(name, {**values, "use_tf32": 0} if name == "CUDAExecutionProvider"
                     else {"device_id": 0} if name == "DmlExecutionProvider" else values)
                    for name, values in original_providers(session)]
        OrtInferSession._get_ep_list = providers
        try:
            engine = RapidOCR(det_model_path=str(self.models["det"]),
                              rec_model_path=str(self.models["rec"]),
                              cls_model_path=str(self.models["cls"]),
                              det_use_cuda=self.backend == "cuda", rec_use_cuda=self.backend == "cuda", cls_use_cuda=self.backend == "cuda",
                              det_use_dml=self.backend == "directml", rec_use_dml=self.backend == "directml", cls_use_dml=self.backend == "directml",
                              intra_op_num_threads=2, inter_op_num_threads=1,
                              print_verbose=False, text_score=.1)
        finally:
            OrtInferSession._init_sess_opts = staticmethod(original)
            OrtInferSession._get_ep_list = original_providers
        sessions = {"det": engine.text_det.infer.session,
                    "rec": engine.text_rec.session.session,
                    "cls": engine.text_cls.infer.session}
        for name, session in sessions.items():
            if session.get_providers()[0] != self.provider:
                raise RuntimeError(f"{name} 模型未启用 {self.provider}；请检查驱动与独立运行包")
            actual_options = session.get_provider_options().get(self.provider, {})
            if self.backend == "cuda" and (str(actual_options.get("use_tf32")) != "0" or str(actual_options.get("device_id")) != "0"):
                raise RuntimeError(f"{name} CUDA 实际 provider 参数未采用 FP32/device 0 设置")
            if self.backend == "directml":
                actual_session = session.get_session_options()
                if actual_session.enable_mem_pattern or actual_session.execution_mode != ort.ExecutionMode.ORT_SEQUENTIAL:
                    raise RuntimeError("DirectML 需要关闭内存模式及并行执行")
            # Graph-assigned CPU shape nodes are allowed; silent session-level
            # retry with CPU after an EP failure must instead reach the parent.
            session.disable_fallback()
        sessions = {name: _TrackedSession(session, self, name) for name, session in sessions.items()}
        engine.text_det.infer.session = sessions["det"]
        engine.text_rec.session.session = sessions["rec"]
        engine.text_cls.infer.session = sessions["cls"]
        self.engine, self.sessions = engine, sessions
        self._check_device()

    def _check_device(self):
        if self.backend == "directml" and _directml_device_info(0) != self.device:
            raise RuntimeError("DirectML 默认 DXGI 适配器身份发生变化，停止 GPU 执行并按设置回退")

    def begin_page(self, request):
        if not self.identity or self.page is not None:
            raise RuntimeError("GPU worker 页面状态无效")
        if self.engine is None:
            self._new_engine()
        self.page, self.calls = int(request["page"]), 0
        self.model_calls = {name: 0 for name in self.sessions}
        self._check_device()
        return {"page": self.page}

    def infer(self, request):
        if self.page is None or self.engine is None:
            raise RuntimeError("GPU 页面尚未开始")
        path = Path(request["image_path"]).resolve(strict=True)
        if path.parent != self.directory or path.suffix.lower() != ".png":
            raise ValueError("GPU 仅接受当前任务临时目录内的 PNG")
        from PIL import Image
        import numpy as np
        with Image.open(path) as image:
            # Match the CPU caller's RGB ndarray exactly (RapidOCR applies
            # different channel conversions to a filename and an ndarray).
            array = np.asarray(image.convert("RGB"))
        result, elapsed = self.engine(array)
        self.calls += 1
        return {"result": result, "elapsed": elapsed}

    def end_page(self, _request):
        if self.page is None or not self.calls:
            raise RuntimeError("没有可验证的 GPU OCR 执行")
        self._check_device()
        actual_calls = dict(self.model_calls)
        first_verification = self.verification is None
        if first_verification:
            import numpy as np
            evidence = {}
            for name, session in self.sessions.items():
                self_check = actual_calls[name] == 0
                if self_check:
                    # A blank first page need not invoke cls/rec. Exercise the
                    # untouched model explicitly; discard all outputs and never
                    # describe this synthetic shape check as OCR accuracy.
                    shape = {"det": [1, 3, 64, 64], "rec": [1, 3, 48, 320], "cls": [1, 3, 48, 192]}[name]
                    session.run(None, {session.get_inputs()[0].name: np.zeros(shape, dtype=np.float32)})
                path = Path(session.end_profiling())
                data = path.read_bytes()
                summary = profile_summary(json.loads(data), self.provider)
                summary.update(profile_sha256=hashlib.sha256(data).hexdigest(),
                               scope="runtime-shape-self-check-no-accuracy-claim" if self_check else "first-document-page")
                if not summary["accelerator_compute_nodes"]:
                    raise RuntimeError(f"{name} 未记录实际 {self.provider} 计算节点，不能将 provider 可用当成 GPU 执行")
                evidence[name] = summary
            self.verification, self.verification_page = evidence, self.page
        for session in self.sessions.values():
            if session.get_providers()[0] != self.provider:
                raise RuntimeError("已验证模型会话 provider 发生变化")
        self.page = None
        return {"execution": {"identity": self.identity, "verified": True,
                "evidence": "onnxruntime-first-page-model-profile" if first_verification else "same-verified-session-successful-inference",
                "models": self.verification, "models_scope": "verification-profile-not-current-page-counters",
                "verification_page": self.verification_page, "current_page_model_calls": actual_calls,
                "calls": self.calls, "cpu_auxiliary_nodes_scope": "verification-profile",
                "cpu_auxiliary_nodes": sum(e["node_counts"].get("CPUExecutionProvider", 0) for e in self.verification.values())}}


def _json_default(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(type(value).__name__)


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    _offline()
    protocol_output = sys.stdout
    worker = Worker()
    for line in sys.stdin:
        request = {}
        try:
            if len(line) > 1024 * 1024:
                raise ValueError("CUDA 请求超过大小限制")
            request = json.loads(line)
            action = request.get("action")
            if action == "close":
                break
            dispatch = {"init": worker.initialize, "begin_page": worker.begin_page,
                        "infer": worker.infer, "end_page": worker.end_page}
            if action not in dispatch:
                raise ValueError("未知 CUDA worker 操作")
            with contextlib.redirect_stdout(sys.stderr):
                result = dispatch[action](request)
            response = {"id": request.get("id"), "ok": True, **result}
        except Exception as exc:
            response = {"id": request.get("id"), "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"}
        protocol_output.write(json.dumps(response, ensure_ascii=False, allow_nan=False,
                                         default=_json_default) + "\n")
        protocol_output.flush()


if __name__ == "__main__":
    main()
