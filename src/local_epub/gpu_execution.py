"""Private-process ONNX Runtime OCR transport and truthful execution identities."""
from __future__ import annotations

from collections import deque
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

from .storage import digest

EXECUTION_VERSION = "ocr-execution-2"


class GPUExecutionError(RuntimeError):
    pass


class OCRCancelled(RuntimeError):
    pass


def cpu_identity():
    from .backend import engine_versions
    return {"kind": "cpu", "versions": engine_versions(),
            "providers": ["CPUExecutionProvider"]}


class _WindowsJob:
    """An OS-owned lifetime boundary; killing the CLI also kills its GPU child."""
    def __init__(self):
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel, self.handle = kernel, kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class OnnxExecutor:
    """One optional child per conversion; a failure latches CPU for this run."""
    def __init__(self, resource_dir, job_dir, runtime_dir=None, *, fallback=True,
                 progress=None, cancel=None, timeout=180, backend="cuda"):
        if backend not in ("cuda", "directml"):
            raise ValueError("GPU 后端必须为 cuda 或 directml")
        self.backend = backend
        self.label = "CUDA" if backend == "cuda" else "DirectML"
        self.resource_dir, self.job_dir = Path(resource_dir), Path(job_dir)
        self.runtime_dir = runtime_dir
        self.fallback = fallback
        self.emit = progress or (lambda _event: None)
        self.cancel = cancel or (lambda: False)
        self.timeout = timeout
        self.identity = None
        self.failure_reason = None
        self.prepared = False
        self.process = None
        self.job_handle = None
        self.temporary = None
        self.work_dir = None
        self.responses = queue.Queue()
        self.stderr_tail = deque(maxlen=30)
        self.sequence = 0
        self.current_page = None

    def _event(self, message, actual=None):
        self.emit({"event": "progress", "stage": "device", "message": message,
                   "requested_device": self.backend, "actual_device": actual,
                   "fallback_reason": self.failure_reason,
                   "gpu_worker_pid": self.process.pid if self.process else None})

    def prepare(self):
        if self.prepared:
            return
        self.prepared = True
        self._event(f"正在检查本地 {self.label} 运行包与显卡驱动；尚未验证 GPU 执行…")
        try:
            if self.cancel():
                raise OCRCancelled("任务已取消")
            if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
                raise GPUExecutionError(f"{self.label} OCR 目前仅支持 Windows 64 位；此平台使用 CPU")
            from .gpu_resources import check_gpu_runtime, default_gpu_runtime_dir
            runtime = check_gpu_runtime(self.runtime_dir or default_gpu_runtime_dir(self.backend), backend=self.backend)
            if not runtime["ok"]:
                details = runtime["errors"] + runtime["missing"]
                raise GPUExecutionError(f"{self.label} 运行包缺失或损坏：" + "；".join(details[:12])
                                        + f"。请导入本版本离线 {self.label} 运行包，或主动下载准备。")
            from .resources import require_resources
            manifest = require_resources(self.resource_dir)
            worker_path = (Path(sys._MEIPASS) / "gpu_worker.py" if getattr(sys, "frozen", False)
                           else Path(__file__).with_name("gpu_worker.py"))
            if not worker_path.is_file():
                raise GPUExecutionError("应用缺少 gpu_worker.py；请使用完整发布包")
            self.temporary = tempfile.TemporaryDirectory(prefix=f".{self.backend}-", dir=self.job_dir)
            self.work_dir = Path(self.temporary.name)
            # PyInstaller's DLL search directory can otherwise inject the CPU
            # runtime into a child. The embedded interpreter also uses -I/-S/-B
            # and its pinned _pth, not the user's Python/site packages.
            env = os.environ.copy()
            for name in list(env):
                if name.startswith(("PYTHON", "CUDA_", "CUDNN_", "_PYI")) or name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
                    env.pop(name, None)
            system_root = env.get("SystemRoot", r"C:\Windows")
            env.update(PATH=str(Path(system_root) / "System32") + os.pathsep + system_root,
                       PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", DO_NOT_TRACK="1")
            self.job_handle = _WindowsJob()
            # Temporarily restore the default DLL directory only in this
            # single-task CLI process; restore it immediately after Popen.
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
            kernel.GetDllDirectoryW.restype = wintypes.DWORD
            kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
            kernel.SetDllDirectoryW.restype = wintypes.BOOL
            buffer = ctypes.create_unicode_buffer(32768)
            kernel.GetDllDirectoryW(len(buffer), buffer)
            kernel.SetDllDirectoryW(None)
            try:
                self.process = subprocess.Popen(
                    [runtime["python_executable"], "-I", "-S", "-B", str(worker_path)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    cwd=self.work_dir, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            finally:
                kernel.SetDllDirectoryW(buffer.value or None)
            self.job_handle.assign(self.process)
            threading.Thread(target=self._read_responses, daemon=True).start()
            threading.Thread(target=self._read_errors, daemon=True).start()
            response = self._request("init", protocol=1, backend=self.backend, work_dir=str(self.work_dir),
                                     models={kind: {"path": str((self.resource_dir / (kind + ".onnx")).resolve()),
                                                    "sha256": manifest["files"][kind + ".onnx"]["sha256"]}
                                             for kind in ("det", "rec", "cls")})
            self.identity = {**response["identity"], "execution_version": EXECUTION_VERSION,
                             "runtime_manifest_sha256": runtime["manifest_sha256"],
                             "worker_sha256": digest(worker_path)}
            self._event(f"{self.label} 模型会话已就绪；首次实际运行后核实 GPU 计算节点，此后复用同会话")
        except OCRCancelled:
            self.close()
            raise
        except Exception as exc:
            self.fail(exc)

    def _read_responses(self):
        process = self.process
        try:
            while True:
                line = process.stdout.readline(16 * 1024 * 1024)
                if not line:
                    break
                if len(line) >= 16 * 1024 * 1024:
                    raise GPUExecutionError("GPU worker 响应过大")
                try:
                    value = json.loads(line)
                except ValueError:
                    self.stderr_tail.append(line[-2000:])
                    continue
                self.responses.put(value)
        except Exception as exc:
            self.responses.put(exc)
        finally:
            self.responses.put(GPUExecutionError("GPU worker 已退出"))

    def _read_errors(self):
        process = self.process
        try:
            for line in process.stderr:
                self.stderr_tail.append(line[-2000:])
        except (ValueError, OSError):
            pass

    def _request(self, action, **data):
        if self.cancel():
            raise OCRCancelled("任务已取消")
        self.sequence += 1
        request_id = self.sequence
        try:
            self.process.stdin.write(json.dumps({"id": request_id, "action": action, **data}, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise GPUExecutionError("无法写入 GPU worker：" + str(exc)) from exc
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.cancel():
                raise OCRCancelled("任务已取消；GPU 当前页尚未落盘")
            try:
                result = self.responses.get(timeout=.1)
            except queue.Empty:
                continue
            if isinstance(result, Exception):
                detail = "".join(self.stderr_tail)[-3000:]
                raise GPUExecutionError(str(result) + ("：" + detail if detail else ""))
            if not isinstance(result, dict) or result.get("id") != request_id:
                raise GPUExecutionError("GPU worker 响应序号或格式错误")
            if not result.get("ok"):
                raise GPUExecutionError(str(result.get("error", "CUDA 执行失败")))
            return result
        raise GPUExecutionError(f"GPU worker 在 {self.timeout} 秒内没有完成 {action}")

    def plan_identity(self):
        if self.identity:
            return self.identity
        if self.failure_reason and self.fallback:
            return cpu_identity()
        return {"kind": self.backend + "-unavailable", "execution_version": EXECUTION_VERSION}

    def begin_page(self, number):
        self.prepare()
        if self.failure_reason:
            raise GPUExecutionError(self.failure_reason)
        self.current_page = number
        self._request("begin_page", page=number)

    def __call__(self, array):
        from PIL import Image
        path = self.work_dir / f"image-{self.sequence + 1}.png"
        Image.fromarray(array).save(path, "PNG")
        try:
            response = self._request("infer", image_path=str(path))
            return response["result"], response["elapsed"]
        finally:
            path.unlink(missing_ok=True)

    def end_page(self):
        response = self._request("end_page")
        execution = response["execution"]
        if not execution.get("verified") or not any(m.get("accelerator_compute_nodes", 0) > 0 for m in execution.get("models", {}).values()):
            raise GPUExecutionError("GPU worker 未提供真实计算节点证据")
        execution["identity"] = self.identity
        execution["requested_device"] = self.backend
        # Keep compact, page-local evidence in raw/pages; full traces are only
        # transient and never contain input image pixels or recognized text.
        for path in self.work_dir.glob("profile-*.json"):
            path.unlink(missing_ok=True)
        self.current_page = None
        return execution

    def fail(self, error):
        if not self.failure_reason:
            self.failure_reason = f"{type(error).__name__}: {error}"
        self.identity = None
        self.close()
        message = (f"{self.label} 不可用，当前页及本次后续 OCR 回退 CPU：" if self.fallback
                   else f"{self.label} 不可用，已禁用 CPU 回退；需 OCR 的页面将失败：") + self.failure_reason
        self._event(message, "cpu" if self.fallback else None)

    def close(self):
        process = self.process
        if process is not None:
            if process.poll() is None:
                try:
                    process.stdin.write('{"action":"close"}\n')
                    process.stdin.flush()
                    process.wait(timeout=.3)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    if self.job_handle:
                        self.job_handle.close()
                    # AssignProcessToJobObject itself can fail. A created but
                    # unassigned Job does not own this child, so always retain
                    # an exact-Popen termination fallback (never enumerate or
                    # terminate unrelated Python/NVIDIA processes).
                    if process.poll() is None:
                        try:
                            process.kill()
                        except OSError:
                            pass  # Job termination may already be in flight.
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        if self.job_handle:
            self.job_handle.close()
        if self.temporary:
            self.temporary.cleanup()
        self.process = None
        self.job_handle = None
        self.temporary = None


# Backward-compatible internal name; both providers use the same transport.
CudaExecutor = OnnxExecutor
