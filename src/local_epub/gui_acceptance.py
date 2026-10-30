"""Explicit, in-package GUI acceptance entry; never runs when imported.

This exercises the production Window and its real QProcess. It accepts paths,
not executable scripts, and writes test output only beneath --output's parent.
Run this entry under the target operating system's real network restrictions.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QTimer, QUrl, QProcess
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import getCppPointer

from .gui import Window
from .storage import atomic_json


def _configure_output():
    for stream in (sys.stdout, sys.stderr):
        configure = getattr(stream, "reconfigure", None)
        if configure is not None:
            configure(encoding="utf-8", errors="backslashreplace")


def _emit(result):
    # PyInstaller console=False intentionally supplies no stdout on Windows.
    if sys.stdout is not None:
        try:
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except (OSError, ValueError):
            pass  # The UTF-8 result.json remains the authoritative result.


def _session_info():
    session = {"name": os.environ.get("SESSIONNAME"), "id": None}
    if sys.platform == "win32":
        try:
            value = ctypes.c_uint32()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            lookup = kernel.ProcessIdToSessionId
            lookup.argtypes = (ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32))
            lookup.restype = ctypes.c_int
            if lookup(os.getpid(), ctypes.byref(value)):
                session["id"] = value.value
            else:
                session["error"] = "ProcessIdToSessionId failed: " + str(ctypes.get_last_error())
        except (AttributeError, OSError) as exc:
            session["error"] = str(exc)
    elif hasattr(os, "getsid"):
        session["id"] = os.getsid(0)
    return session


def parser():
    result = argparse.ArgumentParser(description="Run real in-package Qt GUI acceptance against explicitly supplied test files.")
    result.add_argument("--source", type=Path, required=True)
    result.add_argument("--resources", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--evidence", type=Path, required=True,
                        help="Evidence directory inside the parent directory of --output")
    result.add_argument("--timeout", type=int, default=240)
    result.add_argument("--password", default="", help="Optional test fixture password; never recorded in evidence")
    result.add_argument("--verify-merge", action="store_true")
    result.add_argument("--verify-cancel", action="store_true")
    result.add_argument("--verify-page-number-repair", action="store_true",
                        help="Verify explicit page-3 inference 22 using the five-page 04_page_number_gap.pdf fixture")
    result.add_argument("--verify-reader-open", action="store_true",
                        help="Open the real bundled reader in the system browser; test policy must permit local loopback")
    result.add_argument("--verify-rebuild-layout", action="store_true",
                        help="Click the real layout-refresh button after editing; verify raw hashes/mtimes, selected-page scope and re-export without OCR resources")
    result.add_argument("--verify-gpu-mode", choices=["directml", "cuda", "fallback", "strict"],
                        help="Windows only: verify actual DirectML/CUDA execution, explicit CPU fallback, or tracked strict failure using an OCR-containing PDF")
    result.add_argument("--gpu-backend", choices=["directml", "cuda"],
                        help="Required for fallback/strict acceptance; explicit backend to request, without confusing DirectML and CUDA")
    result.add_argument("--gpu-runtime", type=Path,
                        help="Explicit local GPU runtime directory; missing/incomplete paths may be supplied for fallback/strict acceptance")
    return result


def _paths(args):
    for name in ("source", "resources", "output", "evidence"):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if args.output.suffix.lower() != ".epub" or args.output == args.source:
        raise ValueError("--output must be a separate .epub file")
    if not args.evidence.is_relative_to(args.output.parent) or args.evidence == args.output:
        raise ValueError("--evidence must be a directory inside the explicit output directory")
    if args.output.is_relative_to(args.resources) or args.evidence.is_relative_to(args.resources):
        raise ValueError("Test output cannot modify the input resource directory")
    if not 10 <= args.timeout <= 3600:
        raise ValueError("--timeout must be between 10 and 3600 seconds")
    if args.verify_gpu_mode:
        if sys.platform != "win32":
            raise ValueError("GPU GUI acceptance is Windows-only; ordinary CPU acceptance remains available on Mac")
        if args.verify_gpu_mode in ("directml", "cuda"):
            if args.gpu_backend and args.gpu_backend != args.verify_gpu_mode:
                raise ValueError("--gpu-backend must match the requested execution verification mode")
            args.gpu_backend = args.verify_gpu_mode
        elif not args.gpu_backend:
            raise ValueError("Fallback/strict acceptance requires explicit --gpu-backend directml or cuda")
        if args.gpu_runtime is None:
            raise ValueError("GPU acceptance requires an explicit --gpu-runtime directory")
        args.gpu_runtime = args.gpu_runtime.expanduser().resolve()
        if args.output.is_relative_to(args.gpu_runtime) or args.evidence.is_relative_to(args.gpu_runtime):
            raise ValueError("Test output cannot modify the GPU runtime directory")
        if args.verify_gpu_mode == "strict" and args.output.exists():
            raise ValueError("Strict-failure acceptance requires a fresh --output path")
        if args.verify_gpu_mode == "strict" and args.verify_rebuild_layout:
            raise ValueError("Layout refresh requires completed recognition; run it separately from strict GPU failure acceptance")
    # No recursive cleanup occurs: existing files outside the named output and
    # known evidence filenames are never removed or modified by the harness.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.mkdir(parents=True, exist_ok=True)
    args.output_prepared = True
    if not args.source.is_file() or args.source.suffix.lower() != ".pdf":
        raise ValueError("--source must name an existing test PDF")
    if not args.resources.is_dir():
        raise ValueError("--resources must name an existing local resource directory")


class GuiAcceptance:
    def __init__(self, app, args):
        self.app = app
        self.args = args
        self.window = Window()
        self.phase = "start"
        self.started = time.monotonic()
        self.edit_id = ""
        self.marker = "人工校对验收标记"
        self.error = ""
        self.log_parts = []
        self.merged_sources = []
        self.row_identity = []
        self.flow = []
        self.workers = []
        self.cancel_verified = False
        self.stable_items_verified = False
        self.running_margins_default_off = False
        self.running_margins_toggle_verified = False
        self.reader_launch_verified = False
        self.body_only_verified = False
        self.parse_cache_unchanged = False
        self.parse_cache = {}
        self.scanned_page_numbers_verified = False
        self.page_number_repair_tested = False
        self.page_number_confirmed_record = None
        self.page_number_body_hash = None
        self.gpu_mode_verified = False
        self.execution_evidence = {}
        self.layout_rebuild_tested = False
        self.layout_rebuild_evidence = {}
        self.window.process.started.connect(self.worker_started)
        self.window.process.finished.connect(self.worker_finished)
        # Window's JSONL consumer was connected first. Inspect its actual latest
        # event after it runs; do not consume, replace, or fake the child stream.
        self.window.process.readyReadStandardOutput.connect(self.observe_progress)
        self.timer = QTimer()
        self.timer.timeout.connect(lambda: self.guard(self.poll))

    def capture(self, name):
        if not self.window.grab().save(str(self.args.evidence / name)):
            raise RuntimeError("Could not save GUI evidence image: " + name)

    def guard(self, function):
        if self.phase in ("done", "failure"):
            return
        try:
            function()
        except Exception as exc:
            self.fail(f"{type(exc).__name__}: {exc}")

    def worker_started(self):
        process = self.window.process
        self.workers.append({
            "pid": int(process.processId()),
            "executable": process.program(),
            "action": self.window._running_action,
            "parent_pid": os.getpid(),
        })

    def worker_finished(self, exit_code, exit_status):
        if self.workers:
            self.workers[-1].update(exit_code=exit_code,
                                    exit_status=getattr(exit_status, "value", str(exit_status)))

    def observe_progress(self):
        if self.phase == "cancel_start" and self.window._last_event.get("stage") == "parse":
            self.window.cancel_button.click()
            self.phase = "cancel_wait"
            self.flow.append("cancel button after real parse event")

    def fail(self, message):
        if self.phase == "failure":
            return
        self.error = str(message)
        self.phase = "failure"
        self.timer.stop()
        if self.window.process.state() != QProcess.NotRunning:
            self.window.process.kill()
            self.window.process.waitForFinished(3000)
        self.log_parts.append(self.window.log.toPlainText())
        try:
            self.capture("failure.png")
        except Exception as exc:
            self.log_parts.append("Evidence screenshot failed: " + str(exc))
        self.app.exit(1)

    def begin(self):
        window, args = self.window, self.args
        self.running_margins_default_off = (not window.retain_page_margins_check.isChecked()
                                           and window.retain_page_margins_check.isEnabled())
        if not self.running_margins_default_off:
            return self.fail("Running margins must be an explicit opt-in, available and initially unchecked")
        expected_default = "directml" if sys.platform == "win32" else "cpu"
        if window.ocr_device_combo.currentData() != expected_default or not window.gpu_fallback_check.isChecked():
            return self.fail("New Windows tasks must default to DirectML; Mac defaults to CPU, with GPU fallback enabled")
        if args.verify_gpu_mode:
            window.ocr_device_combo.setCurrentIndex(window.ocr_device_combo.findData(args.gpu_backend))
            window.gpu_fallback_check.setChecked(args.verify_gpu_mode == "fallback")
            window.gpu_runtime_edit.setText(str(args.gpu_runtime))
            self.flow.append("explicit " + args.gpu_backend + " selection in advanced GUI settings; expected " + args.verify_gpu_mode)
        window.resources_edit.setText(str(args.resources))
        if not window.check_resources():
            return self.fail(window.resources_detail.toPlainText())
        self.flow.append("resource integrity check")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(args.source))])
        enter = QDragEnterEvent(QPoint(200, 180), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(window, enter)
        drop = QDropEvent(QPointF(200, 180), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(window, drop)
        if Path(window.source_edit.text()).resolve() != args.source:
            return self.fail("PDF drag-and-drop did not select the requested source")
        self.flow.append("PDF drag/drop")
        window.output_edit.setText(str(args.output))
        window.password_edit.setText(args.password)
        self.capture("input.png")
        self.phase = "cancel_start" if args.verify_cancel else "convert"
        window.start_button.click()
        self.flow.append("start button")
        if window.job_dir and not window.job_dir.resolve().is_relative_to(args.output.parent):
            return self.fail("GUI selected a job outside the explicit output directory")

    def select_block(self, block_id):
        for index in range(self.window.block_list.count()):
            item = self.window.block_list.item(index)
            if not item.isHidden() and (item.data(Qt.UserRole) or {}).get("id") == block_id:
                self.window.block_list.setCurrentRow(index)
                return
        raise ValueError("Block is not present in the visible GUI list: " + block_id)

    def page_cache_snapshot(self):
        root = self.window.job_dir
        return {path.relative_to(root).as_posix(): (path.stat().st_mtime_ns,
                                             hashlib.sha256(path.read_bytes()).hexdigest())
                for folder in ("pages", "raw") for path in (root / folder).glob("*.json")}

    def verify_execution(self):
        if not self.window.job_dir:
            raise ValueError("Conversion did not create a task with execution evidence")
        job = json.loads((self.window.job_dir / "job.json").read_text(encoding="utf-8"))
        records = [{"page": p["number"], "status": p["status"], "error": p.get("error"),
                    "execution": p.get("execution")} for p in job.get("pages", [])]
        self.execution_evidence = {"requested": job.get("options", {}).get("ocr_device", "cpu"),
                                   "fallback_enabled": job.get("options", {}).get("gpu_fallback", True),
                                   "summary": job.get("execution_summary"), "pages": records,
                                   "displayed_device": self.window.actual_device_label.text(),
                                   "device_events": list(self.window._device_events),
                                   "gpu_worker_pids": sorted({e["gpu_worker_pid"] for e in self.window._device_events if e.get("gpu_worker_pid")})}
        mode = self.args.verify_gpu_mode
        if not mode:
            expected_default = "directml" if sys.platform == "win32" else "cpu"
            if self.execution_evidence["requested"] != expected_default:
                raise ValueError("Default GUI conversion did not preserve the platform-specific new-task device")
            return
        if self.execution_evidence["requested"] != self.args.gpu_backend:
            raise ValueError("GUI did not pass the explicit GPU backend selection to its worker")
        if self.execution_evidence["fallback_enabled"] != (mode == "fallback"):
            raise ValueError("GUI fallback checkbox did not reach the saved task options")
        completed = [p["execution"] for p in records if p["status"] == "success" and p["execution"]]
        accelerated = [e for e in completed if e.get("identity", {}).get("kind") == self.args.gpu_backend]
        any_gpu = [e for e in completed if e.get("identity", {}).get("kind") in ("directml", "cuda")]
        fallback = [e for e in completed if e.get("identity", {}).get("kind") == "cpu" and e.get("fallback_reason")]
        if mode in ("directml", "cuda") and (not accelerated or fallback or not all(e.get("verified") for e in accelerated)):
            raise ValueError("No verified actual " + mode + " OCR execution, or unexpected CPU fallback")
        if mode == "fallback" and (not fallback or any_gpu):
            raise ValueError("Missing-runtime acceptance did not record actual whole-page CPU fallback")
        if mode == "strict":
            failed = [p for p in records if p["status"] == "failed" and p.get("error")]
            if not failed or fallback or job.get("status") == "complete" or self.args.output.exists():
                raise ValueError("Strict GPU failure was not tracked, or incorrectly exported/marked complete")
            if not self.workers or self.workers[-1].get("exit_code") == 0:
                raise ValueError("Strict GPU failure unexpectedly returned success")
        self.gpu_mode_verified = True
        self.flow.append("actual backend execution evidence verified: " + mode)

    def verify_body_only_output(self, require_edit=True):
        xhtml = "{http://www.w3.org/1999/xhtml}"
        epub_type = "{http://www.idpf.org/2007/ops}type"
        with zipfile.ZipFile(self.args.output) as archive:
            names = archive.namelist()
            if "EPUB/page-info.xhtml" in names:
                raise ValueError("Removed page-information appendix is still present")
            nav = ET.fromstring(archive.read("EPUB/nav.xhtml"))
            if any(node.get(epub_type) == "page-list" for node in nav.iter()):
                raise ValueError("Removed original-page navigation is still present")
            chapters = [ET.fromstring(archive.read(name)) for name in names
                        if name.startswith("EPUB/chapter-") and name.endswith(".xhtml")]
            body = "".join("".join(root.find(xhtml + "body").itertext()) for root in chapters)
            if require_edit and self.marker not in body:
                raise ValueError("Export lost the manual edit")
            if any(node.get(epub_type) == "pagebreak" for root in chapters for node in root.iter()):
                raise ValueError("Removed page-break markers are still present")
            if any(node.get("data-srcpage") is not None for root in chapters for node in root.iter()):
                raise ValueError("Unchecked margins still include reader source-state markers")
        self.body_only_verified = True

    def verify_running_margin_output(self, confirmed_number=None):
        if self.window._last_error or not self.workers or self.workers[-1].get("exit_code") != 0:
            raise ValueError(self.window._last_error or "Running-margin export process failed")
        epub_type = "{http://www.idpf.org/2007/ops}type"
        with zipfile.ZipFile(self.args.output) as archive:
            if "EPUB/page-info.xhtml" in archive.namelist():
                raise ValueError("Running margins must not use an appendix")
            roots = [ET.fromstring(archive.read(name)) for name in archive.namelist()
                     if name.startswith("EPUB/chapter-") and name.endswith(".xhtml")]
            state_markers = [node for root in roots for node in root.iter()
                             if node.tag == "{http://www.w3.org/1999/xhtml}span" and node.get("data-srcpage") is not None]
            if not state_markers or any("".join(node.itertext()).strip() for node in state_markers):
                raise ValueError("Running-margin source markers must be present and empty, outside body text")
            if self.marker not in "".join("".join(root.itertext()) for root in roots):
                raise ValueError("Running-margin export lost the manual body edit")
            nav = ET.fromstring(archive.read("EPUB/nav.xhtml"))
            page_lists = [node for node in nav.iter() if node.get(epub_type) == "page-list"]
            if not page_lists:
                raise ValueError("Retained printed labels must be navigable in a standard page-list")
            if confirmed_number is not None:
                labels = [node.get("data-srcpage") for node in state_markers]
                if ("22" in labels) != confirmed_number:
                    raise ValueError("Confirmed/cleared label 22 does not match running-footer source data")
                page_labels = [node.get("title") or node.get("aria-label") for root in roots
                               for node in root.iter() if node.get(epub_type) == "pagebreak"]
                if ("22" in page_labels) != confirmed_number:
                    raise ValueError("Confirmed/cleared label 22 does not match EPUB page-break metadata")
        report = json.loads(self.args.output.with_suffix(".report.json").read_text(encoding="utf-8"))
        if not report["export_options"].get("retain_page_margins"):
            raise ValueError("Running-margin export option was not saved in the report")
        if self.parse_cache != self.page_cache_snapshot():
            raise ValueError("Changing running margins or inferred labels changed the original OCR/page checkpoints")

    def verify_page_number_gap(self):
        from .page_info import build_page_info
        pages = build_page_info(self.window.document)
        labels = [[item["text"] for item in page["page_numbers"]] for page in pages]
        if [page["number"] for page in pages] != [1, 2, 3, 4, 5] or labels != [["20"], ["21"], [], ["23"], ["24"]]:
            raise ValueError("The page-number repair fixture must contain real labels 20/21/blank/23/24; blank must remain absent from extraction")
        body_hash = hashlib.sha256(json.dumps(self.window.document["blocks"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if self.page_number_body_hash is None:
            self.page_number_body_hash = body_hash
        elif body_hash != self.page_number_body_hash:
            raise ValueError("Page-number confirmation or clear modified the extracted/edited body blocks")

    def verify_number_export(self, confirmed):
        if self.window._last_error or not self.workers or self.workers[-1].get("exit_code") != 0:
            raise ValueError(self.window._last_error or "Page-number export process failed")
        self.verify_running_margin_output(confirmed_number=confirmed)
        self.verify_page_number_gap()
        if self.parse_cache != self.page_cache_snapshot():
            raise ValueError("Page-number confirmation/clear/export changed original OCR/page checkpoints")
        report = json.loads(self.args.output.with_suffix(".report.json").read_text(encoding="utf-8"))
        records = report["content"].get("page_number_repairs", [])
        if confirmed:
            if len(records) != 1 or records[0].get("page") != 3 or records[0].get("label") != "22" or records[0].get("provenance") != "user_confirmed_inference" or records[0].get("confidence") is not None:
                raise ValueError("Export report lost confirmed inference identity")
            with zipfile.ZipFile(self.args.output) as epub:
                note = epub.read("EPUB/report.xhtml").decode("utf-8")
                if "人工推算" not in note or "非 OCR" not in note:
                    raise ValueError("Export failed to distinguish the human inference from OCR")
            atomic_json(self.args.evidence / "page-number-confirmed.report.json", report)
            (self.args.evidence / "page-number-confirmed.epub").write_bytes(self.args.output.read_bytes())
        elif records:
            raise ValueError("Cleared page-number confirmation still appears in exported records")

    def poll(self):
        window, args = self.window, self.args
        if time.monotonic() - self.started > args.timeout:
            return self.fail("GUI acceptance timeout during " + self.phase)
        if self.phase == "start" or window.process.state() != QProcess.NotRunning:
            return
        if self.phase == "cancel_start":
            return self.fail("Conversion ended before a cancellation checkpoint was observed: " + window.status_label.text())
        if self.phase == "cancel_wait":
            if not window.job_dir or not (window.job_dir / "job.json").is_file():
                return self.fail("Cancellation did not preserve a resumable task")
            job = json.loads((window.job_dir / "job.json").read_text(encoding="utf-8"))
            if job.get("status") != "cancelled":
                return self.fail("Cancellation status was not saved: " + str(job.get("status")))
            self.log_parts.append(window.log.toPlainText())
            self.capture("cancelled.png")
            self.cancel_verified = True
            self.phase = "convert"
            window.resume_button.click()
            self.flow.append("resume button from saved cancellation checkpoint")
        elif self.phase == "convert":
            if args.verify_gpu_mode == "strict":
                self.verify_execution()
                self.log_parts.append(window.log.toPlainText())
                self.capture("strict-failure.png")
                self.phase = "done"
                self.timer.stop()
                self.app.exit(0)
                return
            if (not args.output.is_file() or window._last_error
                    or window._last_status != "complete"):
                return self.fail(window._last_error or window.status_label.text())
            self.verify_execution()
            self.flow.append("QProcess conversion completed")
            self.log_parts.append(window.log.toPlainText())
            self.verify_body_only_output(require_edit=False)
            self.parse_cache = self.page_cache_snapshot()
            if args.source.name == "02_mixed_bad_layer.pdf":
                from .page_info import build_page_info
                information = build_page_info(window.document)
                for number in (2, 3):
                    printed = information[number - 1]["page_numbers"]
                    if len(printed) != 1 or printed[0]["text"] != str(number):
                        return self.fail("Original scanned page number was not recognized")
                    if not printed[0].get("confidence") or printed[0]["confidence"].get("source") != "RapidOCR recognition" or printed[0]["confidence"].get("context") != "page-margin crop":
                        return self.fail("Page number is not backed by actual margin-crop OCR evidence")
                self.scanned_page_numbers_verified = True
            self.flow.append("body-only EPUB has no original-page appendix; OCR digits retain actual provenance")
            blocks = window.document.get("blocks", [])
            if args.verify_merge:
                candidate = next((block for block in blocks if any(
                    isinstance(issue, dict) and issue.get("code") == "cross_page_candidate"
                    for issue in block.get("issues", []))), None)
                if candidate is None:
                    return self.fail("No cross-page candidate in this fixture")
                merged_id = candidate["id"]
                self.select_block(merged_id)
                if not window.merge_button.isEnabled():
                    return self.fail("GUI cross-page merge button is unavailable")
                window.merge_button.click()
                merged = next((block for block in window.document.get("blocks", [])
                               if block["id"] == merged_id), {})
                self.merged_sources = merged.get("sources", [])
                if len({source.get("page") for source in self.merged_sources}) < 2:
                    return self.fail("GUI merge did not preserve both source pages")
                self.flow.append("explicit cross-page merge retaining all sources")
                blocks = window.document.get("blocks", [])
            paragraph = next((block for block in blocks if block.get("kind") == "paragraph"
                              and block.get("text") and not block.get("page_margin_role") and not any(
                                  isinstance(issue, dict) and issue.get("code") == "margin_content"
                                  for issue in block.get("issues", []))), None)
            if paragraph is None:
                return self.fail("No reflowable paragraph in GUI result")
            self.select_block(paragraph["id"])
            self.edit_id = window.selected_block["id"]
            window.editor.setPlainText(window.editor.toPlainText() + "\n" + self.marker)
            window.save_button.click()
            if window._dirty:
                return self.fail("GUI did not persist manual edit")
            self.flow.append("block edit and save button")
            self.capture("review.png")
            self.row_identity = [getCppPointer(window.block_list.item(index))[0]
                                 for index in range(window.block_list.count())]
            self.phase = "export"
            window.export_button.click()
        elif self.phase == "export":
            if window._last_error or not self.workers or self.workers[-1].get("exit_code") != 0:
                return self.fail(window._last_error or "The export process did not finish successfully")
            if not window.selected_block or window.selected_block.get("id") != self.edit_id:
                return self.fail("Re-export lost the selected review block")
            rows = [getCppPointer(window.block_list.item(index))[0]
                    for index in range(window.block_list.count())]
            if rows != self.row_identity:
                return self.fail("Nonstructural export replaced native review items")
            self.stable_items_verified = True
            with zipfile.ZipFile(args.output) as epub:
                body = "\n".join(epub.read(name).decode("utf-8") for name in epub.namelist()
                                 if name.endswith(".xhtml"))
                if self.marker not in body:
                    return self.fail("Re-export lost manual text")
                if epub.read("mimetype") != b"application/epub+zip":
                    return self.fail("Invalid EPUB MIME")
            self.flow.append("re-export without OCR")
            self.log_parts.append(window.log.toPlainText())
            window.load_task(window.job_dir, preserve_form=True)
            block = next((block for block in window.document.get("blocks", [])
                          if block.get("id") == self.edit_id), None)
            if not block or self.marker not in block.get("text", ""):
                return self.fail("Reopening task lost manual edit")
            self.flow.append("task reopen retains manual edit")
            self.verify_body_only_output()
            if self.parse_cache != self.page_cache_snapshot():
                return self.fail("Re-export changed the parse/OCR cache")
            self.parse_cache_unchanged = True
            if window.retain_page_margins_check.isChecked() or not window.retain_page_margins_check.isEnabled():
                return self.fail("Reopening lost the explicit body-only export option")
            self.flow.append("reopen preserves explicit body-only choice and original OCR cache")
            window.retain_page_margins_check.setChecked(True)
            self.phase = "margins_on"
            window.export_button.click()
        elif self.phase == "margins_on":
            self.verify_running_margin_output()
            (args.evidence / "running-margins.epub").write_bytes(args.output.read_bytes())
            atomic_json(args.evidence / "running-margins.report.json",
                        json.loads(args.output.with_suffix(".report.json").read_text(encoding="utf-8")))
            window.load_task(window.job_dir)
            if not window.retain_page_margins_check.isChecked():
                return self.fail("Reopening lost the enabled running-margin option")
            self.log_parts.append(window.log.toPlainText())
            self.flow.append("opt-in re-export writes empty source-state markers and EPUB page-list; reopening restores checkbox")
            window.retain_page_margins_check.setChecked(False)
            self.phase = "margins_off"
            window.export_button.click()
        elif self.phase == "margins_off":
            if window._last_error or self.workers[-1].get("exit_code") != 0:
                return self.fail(window._last_error or "Unchecked-margin export failed")
            self.verify_body_only_output()
            if self.parse_cache != self.page_cache_snapshot():
                return self.fail("Margin on/off exports changed the original OCR/page checkpoints")
            window.load_task(window.job_dir)
            if window.retain_page_margins_check.isChecked():
                return self.fail("Reopening lost the unchecked running-margin option")
            self.running_margins_toggle_verified = True
            self.flow.append("unchecked re-export removes source-state/page-list metadata without OCR; manual text retained")
            self.phase = "number_prepare" if args.verify_page_number_repair else "preview_wait"
        elif self.phase == "number_prepare":
            window.retain_page_margins_check.setChecked(True)
            self.verify_page_number_gap()
            if window.document.get("page_number_repairs"):
                return self.fail("A deliberately blank printed label was filled before explicit confirmation")
            suggestions = window.document.get("page_number_suggestions", [])
            if [(item.get("page"), item.get("label")) for item in suggestions] != [(3, "22")]:
                return self.fail("Expected one unconfirmed suggestion for source page 3, printed label 22")
            window.review_detail_tabs.setCurrentIndex(window.page_number_tab_index)
            choice = window.page_number_choice_combo.findData(3)
            if choice < 0:
                return self.fail("GUI did not expose the original page-3 suggestion")
            window.page_number_choice_combo.setCurrentIndex(choice)
            # Only this newly created test job's derived preview is damaged.
            # The real PDF renderer must recover it; extraction files stay intact.
            preview = window.job_dir / "preview" / "0003.png"
            preview.parent.mkdir(exist_ok=True)
            preview.write_bytes(b"incomplete derived PNG cache")
            window.inspect_page_number_button.click()
            if window.page_combo.currentData() != 3:
                return self.fail("Page-number original-page action did not select source page 3")
            self.phase = "number_preview"
        elif self.phase == "number_preview":
            if window._threads:
                return
            if window.original_label.pixmap() is None or not window._original_display_path or not window._original_display_path[0].endswith("0003.png"):
                return self.fail("Page-number confirmation did not show the actual source page")
            self.flow.append("corrupt derived PNG cache is regenerated from the actual PDF before page-number confirmation")
            window.review_detail_tabs.setCurrentIndex(window.page_number_tab_index)
            if not window.confirm_page_number_button.isVisible() or not window.confirm_page_number_button.isEnabled():
                return self.fail("Explicit page-number confirmation button is unavailable")
            self.capture("page-number-suggestion.png")
            window.confirm_page_number_button.click()
            records = window.document.get("page_number_repairs", [])
            if len(records) != 1 or records[0].get("page") != 3 or records[0].get("label") != "22":
                return self.fail("GUI did not save the explicitly confirmed page-number suggestion")
            self.page_number_confirmed_record = records[0]
            if window.document.get("page_number_suggestions"):
                return self.fail("Confirmed inference still appears as an unconfirmed suggestion")
            self.verify_page_number_gap()
            self.flow.append("original source page 3 is blank; explicit confirm button records inference 22, not OCR or PDF page index 3")
            self.phase = "number_export"
            window.export_button.click()
        elif self.phase == "number_export":
            self.verify_number_export(True)
            window.load_task(window.job_dir)
            records = window.document.get("page_number_repairs", [])
            if records != [self.page_number_confirmed_record]:
                return self.fail("Task reopening lost the confirmed page-number record")
            self.verify_page_number_gap()
            window.review_detail_tabs.setCurrentIndex(window.page_number_tab_index)
            if not window.clear_page_number_button.isVisible() or not window.clear_page_number_button.isEnabled():
                return self.fail("Page-number clear button is unavailable after reopening")
            self.capture("page-number-confirmed.png")
            window.clear_page_number_button.click()
            if window.document.get("page_number_repairs"):
                return self.fail("GUI clear button did not remove the active inference record")
            if [(item.get("page"), item.get("label")) for item in window.document.get("page_number_suggestions", [])] != [(3, "22")]:
                return self.fail("Clearing inference did not restore the unconfirmed suggestion")
            self.flow.append("confirmed inference 22 enters running-footer source metadata and survives reopening; explicit clear restores the unconfirmed suggestion")
            self.phase = "number_clear_export"
            window.export_button.click()
        elif self.phase == "number_clear_export":
            self.verify_number_export(False)
            rows = [getCppPointer(window.block_list.item(index))[0] for index in range(window.block_list.count())]
            if rows != self.row_identity:
                return self.fail("Page-number actions replaced native review list items")
            self.page_number_repair_tested = True
            self.flow.append("clear re-export contains no confirmed inference; body, actual page numbers and OCR cache unchanged")
            self.log_parts.append(window.log.toPlainText())
            self.phase = "preview_wait"
        elif self.phase == "preview_wait":
            if window._threads:
                return
            if args.verify_rebuild_layout and not self.layout_rebuild_tested:
                blocks = [b for b in window.document.get("blocks", []) if b.get("kind") == "paragraph"
                          and b.get("text") and not b.get("page_margin_role") and b.get("sources")]
                ocr_pages = {p["number"] for p in window.document.get("pages", []) if p.get("method") in ("ocr", "mixed")}
                target = next((b for b in blocks if b["sources"][0]["page"] in ocr_pages), blocks[0] if blocks else None)
                if not target:
                    return self.fail("No paragraph is available for real layout-refresh acceptance")
                number = target["sources"][0]["page"]
                window.page_combo.setCurrentIndex(window.page_combo.findData(number))
                self.select_block(target["id"])
                marker = "版面整理前人工校对标记"
                window.editor.setPlainText(window.editor.toPlainText() + "\n" + marker)
                if not window._dirty:
                    return self.fail("Layout-refresh acceptance did not create an unsaved manual correction")
                snapshot = self.page_cache_snapshot()
                self.layout_rebuild_evidence = {
                    "selected_pages": [number], "manual_edit_id": target["id"], "manual_marker": marker,
                    "raw_before": {k: v for k, v in snapshot.items() if k.startswith("raw/")},
                    "unselected_pages_before": {k: v for k, v in snapshot.items()
                                                if k.startswith("pages/") and k != f"pages/{number:04d}.json"},
                    "worker_resources_path": str(args.evidence / "deliberately-missing-ocr-models"),
                }
                if f"raw/{number:04d}.json" not in self.layout_rebuild_evidence["raw_before"]:
                    return self.fail("Layout-refresh acceptance must contain a real nonempty raw checkpoint snapshot")
                self.log_parts.append(window.log.toPlainText())
                self.phase = "layout_rebuild"
                previous_resources = os.environ.get("LOCAL_EPUB_RESOURCES")
                os.environ["LOCAL_EPUB_RESOURCES"] = self.layout_rebuild_evidence["worker_resources_path"]
                try:
                    # QProcess snapshots its environment before start(); no
                    # files or installed model resources are moved or changed.
                    window.rebuild_layout_button.click()
                finally:
                    if previous_resources is None:
                        os.environ.pop("LOCAL_EPUB_RESOURCES", None)
                    else:
                        os.environ["LOCAL_EPUB_RESOURCES"] = previous_resources
                if window._running_action != "rebuild-layout" or window._dirty:
                    return self.fail("Layout-refresh button did not save the correction and start its real worker")
                self.flow.append("layout-refresh button saves unsaved correction and starts selected-page QProcess with unavailable OCR-model directory")
                return
            self.capture("complete.png")
            if args.verify_reader_open:
                if not window.compatible_reader_button.isEnabled():
                    return self.fail("Compatible-reader button is unavailable after export")
                self.phase = "reader_wait"
                window.compatible_reader_button.click()
                return
            self.phase = "done"
            self.timer.stop()
            self.app.exit(0)
        elif self.phase == "layout_rebuild":
            if window._last_error or not self.workers or self.workers[-1].get("action") != "rebuild-layout" or self.workers[-1].get("exit_code") != 0:
                return self.fail(window._last_error or "Layout-refresh worker failed")
            snapshot = self.page_cache_snapshot()
            raw_after = {k: v for k, v in snapshot.items() if k.startswith("raw/")}
            if raw_after != self.layout_rebuild_evidence["raw_before"]:
                return self.fail("Layout refresh changed original recognition hashes or mtimes")
            for name, expected in self.layout_rebuild_evidence["unselected_pages_before"].items():
                if snapshot.get(name) != expected:
                    return self.fail("Selected-page refresh rewrote an unselected page: " + name)
            window.load_task(window.job_dir)
            marker = self.layout_rebuild_evidence["manual_marker"]
            if not any(marker in b.get("text", "") for b in window.document.get("blocks", [])):
                return self.fail("Layout refresh lost the correction saved by its button")
            with zipfile.ZipFile(args.output) as epub:
                exported = "\n".join(epub.read(n).decode("utf-8") for n in epub.namelist() if n.endswith(".xhtml"))
            if marker not in exported or self.marker not in exported:
                return self.fail("Layout refresh did not re-export preserved manual corrections")
            self.layout_rebuild_evidence.update(raw_after=raw_after, raw_unchanged=True,
                                               unselected_pages_unchanged=True, manual_edit_preserved=True,
                                               worker=dict(self.workers[-1]))
            atomic_json(args.evidence / "rebuild-layout.json", self.layout_rebuild_evidence)
            self.layout_rebuild_tested = True
            self.log_parts.append(window.log.toPlainText())
            self.flow.append("layout refresh re-exported and reopened manual edits; original raw hashes/mtimes and unselected page checkpoints unchanged")
            self.phase = "preview_wait"
        elif self.phase == "reader_wait":
            if window._threads:
                return
            result = window._reader_result or {}
            if not str(result.get("url", "")).startswith("http://127.0.0.1:"):
                return self.fail("Local reader did not open: " + window.status_label.text())
            self.reader_launch_verified = True
            self.flow.append("compatible-reader button starts the real bundled loopback service and opens the system browser; rendering checked separately")
            self.phase = "done"
            self.timer.stop()
            self.app.exit(0)

    def result(self):
        return {
            "status": self.phase, "error": self.error,
            "source": str(self.args.source), "output": str(self.args.output),
            "job": str(self.window.job_dir) if self.window.job_dir else None,
            "manual_edit_id": self.edit_id, "manual_edit_marker": self.marker,
            "seconds": round(time.monotonic() - self.started, 2),
            "platform_plugin": self.app.platformName(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable, "pid": os.getpid(), "parent_pid": os.getppid(),
            "worker_executable": self.workers[0]["executable"] if self.workers else None,
            "workers": self.workers,
            "session": _session_info(),
            "platform": platform.platform(), "machine": platform.machine(),
            "python": sys.version, "stdout_available": sys.stdout is not None,
            "cancellation_requested": self.args.verify_cancel,
            "cancellation_and_resume_tested": self.cancel_verified and "QProcess conversion completed" in self.flow,
            "merged_sources": self.merged_sources,
            "stable_native_items_on_export": self.stable_items_verified,
            "running_margins_available_default_off": self.running_margins_default_off,
            "running_margins_on_off_reexport_tested": self.running_margins_toggle_verified,
            "reader_launch_requested": self.args.verify_reader_open,
            "reader_launch_tested": self.reader_launch_verified,
            "body_only_without_page_info_appendix": self.body_only_verified,
            "export_kept_parse_cache": self.parse_cache_unchanged,
            "scanned_page_numbers_verified": self.scanned_page_numbers_verified,
            "page_number_repair_requested": self.args.verify_page_number_repair,
            "page_number_repair_tested": self.page_number_repair_tested,
            "page_number_confirmed_record": self.page_number_confirmed_record,
            "gpu_mode_requested": self.args.verify_gpu_mode,
            "gpu_backend_requested": self.args.gpu_backend,
            "gpu_mode_tested": self.gpu_mode_verified,
            "gpu_runtime": str(self.args.gpu_runtime) if self.args.gpu_runtime else None,
            "execution_evidence": self.execution_evidence,
            "layout_rebuild_requested": self.args.verify_rebuild_layout,
            "layout_rebuild_tested": self.layout_rebuild_tested,
            "layout_rebuild_evidence": self.layout_rebuild_evidence,
            "gui_flow": self.flow,
        }

    def run(self):
        self.window.show()
        QTimer.singleShot(150, lambda: self.guard(self.begin))
        self.timer.start(100)
        code = self.app.exec()
        self.timer.stop()
        if self.phase != "done" and not self.error:
            self.error = "GUI event loop ended before acceptance completed"
            self.phase = "failure"
            code = 1
        if self.window.process.state() != QProcess.NotRunning:
            self.window.process.kill()
            self.window.process.waitForFinished(3000)
        for thread in list(self.window._threads):
            thread.wait(5000)
        self.window._cleanup_password()
        result = self.result()
        atomic_json(self.args.evidence / "result.json", result)
        (self.args.evidence / "worker.log").write_text("\n\n".join(self.log_parts), encoding="utf-8")
        _emit(result)
        return code


def main(argv=None):
    _configure_output()
    args = parser().parse_args(argv)
    try:
        _paths(args)
        app = QApplication.instance() or QApplication([sys.argv[0]])
        app.setApplicationName("本地书坊 · GUI 验收")
        app.setOrganizationName("Local EPUB")
        return GuiAcceptance(app, args).run()
    except (OSError, ValueError, RuntimeError) as exc:
        result = {"status": "failure", "error": str(exc),
                  "frozen": bool(getattr(sys, "frozen", False)), "pid": os.getpid(),
                  "executable": sys.executable, "platform": platform.platform(),
                  "session": _session_info(), "stdout_available": sys.stdout is not None}
        if getattr(args, "output_prepared", False):
            try:
                atomic_json(args.evidence / "result.json", result)
            except OSError:
                pass
        _emit(result)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
