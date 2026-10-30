"""The layout refresh action reuses real cached recognition through QProcess."""
import hashlib
import json
from pathlib import Path
import time

import pytest

from local_epub.cli import parser


ROOT = Path(__file__).resolve().parents[1]


def test_layout_cli_has_export_options_but_no_ocr_runtime_controls():
    arguments = parser().parse_args(["rebuild-layout", "existing-task", "--pages", "1,3", "--output", "book.epub",
                                     "--title", "新版段落", "--font-size", "1.2", "--allow-partial",
                                     "--password-file", "private.txt", "--cancel-file", "cancel.flag"])
    assert arguments.pages == "1,3" and arguments.title == "新版段落" and arguments.allow_partial
    assert not hasattr(arguments, "resources") and not hasattr(arguments, "ocr_device")
    assert not hasattr(arguments, "gpu_runtime")


def _wait(app, predicate, timeout=30):
    until = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        if time.monotonic() >= until:
            raise AssertionError("Layout worker did not complete")
        time.sleep(.005)
    app.processEvents()


def test_layout_gui_button_saves_edits_reuses_raw_without_models_and_exports(tmp_path, monkeypatch):
    from local_epub.pipeline import create_job, run_job, build_document
    from local_epub.resources import check_resources
    resources = ROOT / "resources"
    if not check_resources(resources)["ok"]:
        pytest.skip("Prepared models required only to create the initial test task")
    job = tmp_path / "task"
    create_job(ROOT / "tests/fixtures/02_mixed_bad_layer.pdf", job, {"ocr_device": "cpu"})
    assert run_job(job, resources)["status"] == "complete"
    before = {path.name: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
              for path in (job / "raw").glob("*.json")}
    untouched_page = (job / "pages/0001.json").read_bytes()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("LOCAL_EPUB_RESOURCES", str(tmp_path / "missing-models"))
    from PySide6.QtCore import QProcess, Qt
    from PySide6.QtWidgets import QApplication
    from local_epub.gui import Window
    app = QApplication.instance() or QApplication([])
    window = Window()
    exits = []
    window.process.finished.connect(lambda code, _status: exits.append(code))
    try:
        window.load_task(job)
        window.resources_edit.setText(str(tmp_path / "missing-models"))
        window.output_edit.setText(str(tmp_path / "reflow.epub"))
        window.page_combo.setCurrentIndex(window.page_combo.findData(2))
        index = next(i for i in range(window.block_list.count()) if not window.block_list.item(i).isHidden()
                     and (window.block_list.item(i).data(Qt.UserRole) or {}).get("kind") == "paragraph")
        window.block_list.setCurrentRow(index)
        window.editor.insertPlainText("人工校对保留标记")
        assert window._dirty
        window.rebuild_layout_button.click()
        assert window._running_action == "rebuild-layout"
        assert not window._dirty and not window.rebuild_layout_button.isEnabled()
        command = window.process.arguments()
        assert "--pages" in command and command[command.index("--pages") + 1] == "2"
        assert "--resources" not in command and "--ocr-device" not in command and "--gpu-runtime" not in command
        _wait(app, lambda: bool(exits) and window.process.state() == QProcess.NotRunning)
        assert exits == [0], window.log.toPlainText()
        assert (tmp_path / "reflow.epub").is_file()
        assert before == {path.name: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                          for path in (job / "raw").glob("*.json")}
        assert (job / "pages/0001.json").read_bytes() == untouched_page
        edits = json.loads((job / "edits.json").read_text(encoding="utf-8"))
        assert any("人工校对保留标记" in record["text"] for record in edits["blocks"].values())
        document = build_document(job)
        assert any("人工校对保留标记" in block.get("text", "") for block in document["blocks"])
        assert window.rebuild_layout_button.isEnabled()
    finally:
        if window.process.state() != QProcess.NotRunning:
            window.process.kill()
            window.process.waitForFinished(3000)
        _wait(app, lambda: not window._threads)
        window.close()
        app.processEvents()


def test_wrong_layout_password_can_retry_in_gui_without_rerunning_recognition(tmp_path, monkeypatch):
    from pypdf import PdfReader, PdfWriter
    from local_epub.pipeline import create_job, run_job, rebuild_layout, load_job
    source, job = tmp_path / "encrypted.pdf", tmp_path / "task"
    writer = PdfWriter()
    writer.add_page(PdfReader(ROOT / "tests/fixtures/01_chinese_crosspage.pdf").pages[0])
    writer.encrypt("layout-retry-password")
    writer.write(source)
    create_job(source, job, {"mode": "text"}, password="layout-retry-password")
    assert run_job(job, ROOT / "resources", password="layout-retry-password")["status"] == "complete"
    paths = [job / "raw/0001.json", job / "pages/0001.json"]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    failed = rebuild_layout(job, password="incorrect-password", pages=[1])
    assert failed["pages"][0]["status"] == "failed"
    assert failed["pages"][0]["layout_rebuild_failed"]
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == before[path] for path in paths)

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("LOCAL_EPUB_RESOURCES", str(tmp_path / "missing-models"))
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication
    from local_epub.gui import Window
    app = QApplication.instance() or QApplication([])
    window = Window()
    exits = []
    window.process.finished.connect(lambda code, _status: exits.append(code))
    try:
        window.load_task(job)
        window.output_edit.setText(str(tmp_path / "recovered.epub"))
        window.password_edit.setText("layout-retry-password")
        window.page_combo.setCurrentIndex(window.page_combo.findData(1))
        assert not window.partial_check.isChecked()
        assert window.rebuild_layout_button.isEnabled()
        window.rebuild_layout_button.click()
        assert window._running_action == "rebuild-layout", window.status_label.text()
        _wait(app, lambda: bool(exits) and window.process.state() == QProcess.NotRunning)
        assert exits == [0], window.log.toPlainText()
        recovered = load_job(job)
        assert recovered["status"] == "complete"
        assert not recovered["pages"][0].get("layout_rebuild_failed")
        assert recovered["layout_rebuild"]["ocr_initialized"] is False
        assert (paths[0].read_bytes(), paths[0].stat().st_mtime_ns) == before[paths[0]]
        assert (tmp_path / "recovered.epub").is_file()
    finally:
        if window.process.state() != QProcess.NotRunning:
            window.process.kill()
            window.process.waitForFinished(3000)
        _wait(app, lambda: not window._threads)
        window.close()
        app.processEvents()
