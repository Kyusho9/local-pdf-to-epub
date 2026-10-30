"""Platform defaults, explicit GPU CLI wiring, and resource-operation safety."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from pypdf import PdfReader, PdfWriter

from local_epub.cli import parser

ROOT = Path(__file__).resolve().parents[1]


def test_gpu_cli_defaults_and_resume_omissions_are_distinct(monkeypatch):
    parse = parser()
    converted = parse.parse_args(["convert", "sample.pdf", "--job", "new-task"])
    assert converted.ocr_device == ("directml" if sys.platform == "win32" else "cpu") and converted.gpu_fallback is True
    resumed = parse.parse_args(["resume", "old-task"])
    assert resumed.ocr_device is None and resumed.gpu_fallback is None
    strict = parse.parse_args(["resume", "old-task", "--ocr-device", "cuda", "--no-gpu-fallback", "--gpu-runtime", "runtime"])
    assert strict.ocr_device == "cuda" and strict.gpu_fallback is False and strict.gpu_runtime == "runtime"
    exported = parse.parse_args(["export", "task", "--output", "book.epub"])
    assert not hasattr(exported, "ocr_device") and not hasattr(exported, "gpu_runtime")
    resource = parse.parse_args(["gpu-resources", "download", "--dir", "runtime"])
    assert resource.action == "download" and resource.backend == "directml"
    assert parse.parse_args(["gpu-resources", "check", "--backend", "cuda"]).backend == "cuda"
    monkeypatch.setattr(sys, "platform", "win32")
    assert parser().parse_args(["convert", "sample.pdf", "--job", "new-task"]).ocr_device == "directml"
    assert parser().parse_args(["resume", "old-task"]).ocr_device is None
    monkeypatch.setattr(sys, "platform", "darwin")
    assert parser().parse_args(["convert", "sample.pdf", "--job", "new-task"]).ocr_device == "cpu"


def _cli(*args):
    result = subprocess.run([sys.executable, "-m", "local_epub", *map(str, args)],
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "ascii"},
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip().startswith("{")]
    return result, events


@pytest.mark.parametrize("backend", ["directml", "cuda"])
def test_real_cli_missing_gpu_falls_back_then_strict_failure_tracks_page_and_cpu_resume(tmp_path, backend):
    from local_epub.resources import check_resources
    resources = ROOT / "resources"
    if not check_resources(resources)["ok"]:
        pytest.skip("Requires prepared local OCR models")
    writer = PdfWriter()
    writer.add_page(PdfReader(ROOT / "tests/fixtures/02_mixed_bad_layer.pdf").pages[1])
    source = tmp_path / "one-scan.pdf"
    with source.open("wb") as stream:
        writer.write(stream)
    job, output = tmp_path / "task", tmp_path / "fallback.epub"
    result, events = _cli("convert", source, "--job", job, "--resources", resources,
                          "--output", output, "--ocr-device", backend, "--gpu-runtime", tmp_path / "missing-runtime")
    assert result.returncode == 0, (result.stdout, result.stderr)
    state = json.loads((job / "job.json").read_text(encoding="utf-8"))
    execution = state["pages"][0]["execution"]
    assert execution["identity"]["kind"] == "cpu" and execution["fallback_reason"]
    assert events[-1]["execution_summary"]["counts"][backend] == 0
    assert output.is_file()
    preserved = output.read_bytes()
    strict_output = tmp_path / "strict-must-not-exist.epub"
    result, _ = _cli("resume", job, "--resources", resources, "--retry-pages", "1",
                     "--no-gpu-fallback", "--gpu-runtime", tmp_path / "missing-runtime", "--output", strict_output)
    assert result.returncode != 0 and not strict_output.exists()
    state = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert state["options"]["ocr_device"] == backend and state["options"]["gpu_fallback"] is False
    assert state["pages"][0]["status"] == "failed" and state["pages"][0]["error"]
    assert output.read_bytes() == preserved
    result, _ = _cli("resume", job, "--resources", resources, "--ocr-device", "cpu", "--gpu-fallback",
                     "--retry-pages", "1", "--output", tmp_path / "cpu.epub")
    assert result.returncode == 0, (result.stdout, result.stderr)
    state = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert state["options"]["ocr_device"] == "cpu" and state["status"] == "complete"


@pytest.fixture
def qt_window(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from local_epub.gui import Window
    app = QApplication.instance() or QApplication([])
    window = Window()
    yield app, window
    _until(app, lambda: not window._threads)
    window.close()
    app.processEvents()


def _until(app, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Qt background work did not complete")
        time.sleep(0.005)
    app.processEvents()


def test_gui_gpu_selection_default_and_platform_boundary(qt_window):
    _, window = qt_window
    assert window.ocr_device_combo.currentData() == ("directml" if sys.platform == "win32" else "cpu")
    assert window.gpu_fallback_check.isChecked()
    if sys.platform != "win32":
        assert "--gpu-runtime" not in window._device_args()
        assert not window.ocr_device_combo.model().item(window.ocr_device_combo.findData("directml")).isEnabled()
        assert not window.ocr_device_combo.model().item(window.ocr_device_combo.findData("cuda")).isEnabled()
    assert "--ocr-device" not in window._output_args()
    assert "--gpu-runtime" not in window._output_args()


def test_gui_resource_thread_keeps_event_loop_alive_blocks_conversion_and_close(qt_window):
    from PySide6.QtCore import QTimer
    app, window = qt_window
    entered, release = threading.Event(), threading.Event()
    ticks, ids = [], []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(5)
    def bounded_operation():
        ids.append(threading.get_ident())
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test operation was not released")
        return {"ok": False, "missing": ["manifest.json"], "errors": []}
    try:
        assert window._resource_work(bounded_operation, "Test resource gate", category="gpu")
        _until(app, lambda: entered.is_set() and len(ticks) >= 3)
        assert ids == [ids[0]] and ids[0] != threading.get_ident()
        assert not window.start_button.isEnabled() and not window.gpu_import_button.isEnabled()
        assert not window.ocr_device_combo.isEnabled() and not window.cancel_button.isEnabled()
        window.start_conversion()
        assert window.job_dir is None
        assert not window._resource_work(lambda: {}, "Must not run", category="gpu")
        assert not window.close() and window._resource_busy
    finally:
        release.set()
        timer.stop()
        _until(app, lambda: not window._threads)
    assert window.start_button.isEnabled() and not window._resource_busy
    assert "manifest.json" in window.gpu_detail.toPlainText()


def test_gui_missing_runtime_check_uses_real_background_api(qt_window, tmp_path):
    app, window = qt_window
    window.gpu_runtime_edit.setText(str(tmp_path / "missing-runtime"))
    window.gpu_check_button.click()
    assert window._resource_busy and not window.start_button.isEnabled()
    _until(app, lambda: not window._threads)
    assert window._gpu_state and not window._gpu_state["ok"]
    assert window.start_button.isEnabled() and "运行包未就绪" in window.gpu_detail.toPlainText()
    assert window.ocr_device_combo.currentData() == ("directml" if sys.platform == "win32" else "cpu")


def test_gpu_package_switch_preserves_each_directory_and_cpu_choice(qt_window, tmp_path):
    _, window = qt_window
    window.ocr_device_combo.setCurrentIndex(window.ocr_device_combo.findData("cpu"))
    assert window.gpu_backend_combo.currentData() == "directml"
    dml, cuda = str(tmp_path / "my-dml"), str(tmp_path / "my-cuda")
    window.gpu_runtime_edit.setText(dml)
    window.gpu_backend_combo.setCurrentIndex(window.gpu_backend_combo.findData("cuda"))
    assert window.ocr_device_combo.currentData() == "cpu"
    assert window.gpu_runtime_edit.text() != dml
    window.gpu_runtime_edit.setText(cuda)
    window.gpu_backend_combo.setCurrentIndex(window.gpu_backend_combo.findData("directml"))
    assert window.gpu_runtime_edit.text() == dml
    window.ocr_device_combo.setCurrentIndex(window.ocr_device_combo.findData("cuda"))
    assert window.gpu_backend_combo.currentData() == "cuda" and window.gpu_runtime_edit.text() == cuda
    assert window._device_args()[-2:] == ["--gpu-runtime", cuda]
    window.gpu_backend_combo.setCurrentIndex(window.gpu_backend_combo.findData("directml"))
    assert window.ocr_device_combo.currentData() == "directml" and window.gpu_runtime_edit.text() == dml


@pytest.mark.parametrize("device", [None, "cuda", "directml"])
def test_reopen_preserves_old_cpu_and_explicit_gpu_tasks(qt_window, tmp_path, device):
    from local_epub.pipeline import create_job
    from local_epub.storage import atomic_json
    _, window = qt_window
    root = tmp_path / "task"
    job = create_job(ROOT / "tests/fixtures/01_chinese_crosspage.pdf", root, {"ocr_device": device or "cpu"})
    if device is None:
        job["options"].pop("ocr_device", None)
    else:
        job["gpu_runtime"] = {"path": str(tmp_path / (device + "-custom"))}
        if device == "directml":
            job["gpu_runtime"]["backend"] = device
    atomic_json(root / "job.json", job)
    window.load_task(root)
    assert window.ocr_device_combo.currentData() == (device or "cpu")
    assert "未确认执行设备" in window.actual_device_label.text()
    if device:
        assert window.gpu_backend_combo.currentData() == device
        assert window.gpu_runtime_edit.text() == job["gpu_runtime"]["path"]


def test_reopen_interrupted_backend_switch_does_not_reuse_other_runtime(qt_window, tmp_path):
    from local_epub.pipeline import create_job, update_job_device
    from local_epub.storage import atomic_json
    _, window = qt_window
    root = tmp_path / "switch-task"
    cuda_runtime = str(tmp_path / "cuda-runtime")
    job = create_job(ROOT / "tests/fixtures/01_chinese_crosspage.pdf", root, {"ocr_device": "cuda"})
    job["gpu_runtime"] = {"backend": "cuda", "path": cuda_runtime}
    atomic_json(root / "job.json", job)
    window.load_task(root)
    assert window.gpu_runtime_edit.text() == cuda_runtime
    update_job_device(root, "directml")
    window.load_task(root)
    assert window.gpu_backend_combo.currentData() == "directml"
    assert window.gpu_runtime_edit.text() != cuda_runtime
    assert cuda_runtime not in window._device_args()
    window.ocr_device_combo.setCurrentIndex(window.ocr_device_combo.findData("cuda"))
    assert window.gpu_runtime_edit.text() == cuda_runtime


def test_missing_runtime_event_keeps_full_error_in_log_without_expanding_status(qt_window, tmp_path):
    from local_epub.gpu_resources import check_gpu_runtime
    from local_epub.gpu_execution import CudaExecutor, GPUExecutionError
    _, window = qt_window
    state = check_gpu_runtime(tmp_path / "missing-runtime")
    details = state["errors"] + state["missing"]
    events = []
    executor = CudaExecutor(ROOT / "resources", tmp_path, fallback=True, progress=events.append)
    executor.fail(GPUExecutionError("CUDA 运行包缺失或损坏：" + "；".join(details[:12])))
    event = events[-1]
    window._handle_line(json.dumps(event, ensure_ascii=False))
    assert len(event["message"]) > 100
    assert len(window.status_label.text()) < 130
    assert event["message"] in window.log.toPlainText()
    assert window.status_label.toolTip() == event["message"]
    assert "等待页面执行确认" in window.actual_device_label.text()
    assert "实际完成设备" not in window.actual_device_label.text()
