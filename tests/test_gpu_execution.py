"""Execution integrity checks; OCR fallback uses the real local CPU models."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from local_epub.backend import engine_versions
from local_epub.gpu_worker import profile_summary
from local_epub.pipeline import (STAGE_VERSION, _cache_key, build_document, create_job,
                                 load_job, run_job, save_edit, update_job_device)
from local_epub.resources import check_resources

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "resources"
FIXTURES = ROOT / "tests/fixtures"


def test_cpu_cache_fingerprint_is_backward_compatible():
    resource_state = check_resources(RESOURCES)
    old_job = {"source": {"sha256": "sample-source-digest"}, "options": {"mode": "auto", "dpi": 180}}
    old_payload = {"source": old_job["source"]["sha256"], "options": old_job["options"],
                   "engines": engine_versions(), "models": resource_state["manifest"], "stage": STAGE_VERSION}
    old_key = hashlib.sha256(json.dumps(old_payload, sort_keys=True).encode()).hexdigest()
    assert _cache_key(old_job, resource_state) == old_key
    new_job = {**old_job, "options": {**old_job["options"], "ocr_device": "cpu", "gpu_fallback": True}}
    assert _cache_key(new_job, resource_state, {"kind": "cpu"}) == old_key
    new_job["options"]["ocr_device"] = "cuda"
    assert _cache_key(new_job, resource_state, {"kind": "cpu"}) != old_key
    assert _cache_key(new_job, resource_state, {"kind": "cpu"}) != _cache_key(new_job, resource_state, {"kind": "cuda"})
    assert _cache_key(new_job, resource_state, {"kind": "cuda", "runtime_manifest_sha256": "a"}) != _cache_key(new_job, resource_state, {"kind": "cuda", "runtime_manifest_sha256": "b"})


def test_registered_provider_is_not_actual_gpu_compute():
    assert profile_summary([])["cuda_compute_nodes"] == 0
    cpu = {"cat": "Node", "args": {"provider": "CPUExecutionProvider", "op_name": "Conv"}}
    copy = {"cat": "Node", "args": {"provider": "CUDAExecutionProvider", "op_name": "MemcpyFromHost"}}
    assert profile_summary([cpu, copy])["cuda_compute_nodes"] == 0
    gpu = {"cat": "Node", "args": {"provider": "CUDAExecutionProvider", "op_name": "Conv"}}
    result = profile_summary([cpu, copy, gpu])
    assert result["cuda_compute_nodes"] == 1
    assert result["node_counts"] == {"CPUExecutionProvider": 1, "CUDAExecutionProvider": 2}


def test_directml_fused_compute_is_evidence_but_copy_is_not():
    copy = {"cat": "Node", "args": {"provider": "DmlExecutionProvider", "op_name": "DmlMemcpyFromHost"}}
    assert profile_summary([copy], "DmlExecutionProvider")["accelerator_compute_nodes"] == 0
    fused = {"cat": "Node", "args": {"provider": "DmlExecutionProvider", "op_name": "DmlFusedConv"}}
    result = profile_summary([copy, fused], "DmlExecutionProvider")
    assert result["accelerator_compute_nodes"] == result["directml_compute_nodes"] == 1
    assert "cuda_compute_nodes" not in result


def test_real_unavailable_runtime_fallback_cache_and_revisions(tmp_path):
    if not check_resources(RESOURCES)["ok"]:
        pytest.skip("Local OCR models are not prepared")
    job = tmp_path / "任务"
    create_job(FIXTURES / "02_mixed_bad_layer.pdf", job)
    assert run_job(job, RESOURCES)["status"] == "complete"
    original = build_document(job)
    block = next(b for b in original["blocks"] if b["sources"][0]["page"] == 2 and b["kind"] == "paragraph")
    corrected = block["text"] + "人工校对保留"
    save_edit(job, block["id"], corrected)
    edits_before = (job / "edits.json").read_bytes()
    old_keys = [p["cache_key"] for p in load_job(job)["pages"]]
    update_job_device(job, "cuda", True)
    events = []
    result = run_job(job, RESOURCES, gpu_runtime_dir=tmp_path / "missing-runtime", progress=events.append)
    assert result["status"] == "complete"
    assert result["execution_summary"]["counts"] == {"native": 1, "cpu": 3, "directml": 0, "cuda": 0}
    assert len(result["execution_summary"]["fallback_pages"]) == 3
    assert all(p["execution"]["identity"]["kind"] == "cpu" for p in result["pages"][1:])
    assert all(p["execution"].get("fallback_reason") for p in result["pages"][1:])
    assert any(e.get("actual_device") == "cpu" and e.get("fallback_reason") for e in events)
    assert old_keys != [p["cache_key"] for p in result["pages"]]
    assert (job / "edits.json").read_bytes() == edits_before
    assert any(b.get("text") == corrected for b in build_document(job)["blocks"])
    times = {p.name: p.stat().st_mtime_ns for p in (job / "pages").glob("*.json")}
    assert run_job(job, RESOURCES, gpu_runtime_dir=tmp_path / "missing-runtime")["status"] == "complete"
    assert times == {p.name: p.stat().st_mtime_ns for p in (job / "pages").glob("*.json")}
    update_job_device(job, gpu_fallback=False)
    strict = run_job(job, RESOURCES, gpu_runtime_dir=tmp_path / "missing-runtime")
    assert strict["status"] == "incomplete"
    assert strict["pages"][0]["status"] == "success"
    assert all(p["status"] == "failed" and "CUDA" in p["error"] for p in strict["pages"][1:])
    assert (job / "edits.json").read_bytes() == edits_before
    assert any(c["edit"]["text"] == corrected for c in build_document(job)["edit_conflicts"])


def test_cpu_old_task_defaults_do_not_rewrite_page_cache(tmp_path):
    job = tmp_path / "old"
    create_job(FIXTURES / "01_chinese_crosspage.pdf", job)
    run_job(job, RESOURCES)
    value = load_job(job)
    value["options"].pop("ocr_device")
    value["options"].pop("gpu_fallback")
    (job / "job.json").write_text(json.dumps(value), encoding="utf-8")
    times = {p.name: p.stat().st_mtime_ns for p in (job / "pages").glob("*.json")}
    events = []
    run_job(job, RESOURCES, progress=events.append)
    assert times == {p.name: p.stat().st_mtime_ns for p in (job / "pages").glob("*.json")}
    assert len([e for e in events if e.get("stage") == "cache"]) == 2


def test_cancel_before_gpu_probe_does_not_fallback(tmp_path):
    job = tmp_path / "cancel"
    create_job(FIXTURES / "02_mixed_bad_layer.pdf", job, {"ocr_device": "cuda"})
    events = []
    result = run_job(job, RESOURCES, gpu_runtime_dir=tmp_path / "missing", cancel=lambda: True, progress=events.append)
    assert result["status"] == "cancelled"
    assert all(p["status"] == "pending" for p in result["pages"])
    assert not any(e.get("fallback_reason") for e in events)
    assert not list(job.glob(".cuda-*"))


def test_unassigned_job_cleanup_terminates_only_its_real_child(tmp_path):
    from local_epub.gpu_execution import CudaExecutor
    class UnassignedJob:
        # Mirrors a Job handle whose AssignProcessToJobObject was refused:
        # closing that handle cannot terminate the separately started child.
        def close(self):
            pass
    executor = CudaExecutor(RESOURCES, tmp_path)
    child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", "import time; time.sleep(30)"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8")
    executor.process, executor.job_handle = child, UnassignedJob()
    try:
        executor.close()
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="The real Windows CUDA protocol is exercised by target-host acceptance")
def test_standalone_worker_rejects_other_platform_without_cloud(tmp_path):
    worker = ROOT / "src/local_epub/gpu_worker.py"
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(worker)],
                            input='{"action":"init","id":1,"protocol":1}\n{"action":"close"}\n',
                            text=True, encoding="utf-8", capture_output=True, timeout=15, cwd=tmp_path)
    response = json.loads(result.stdout)
    assert response["id"] == 1 and response["ok"] is False
    assert "Windows" in response["error"]
    assert result.returncode == 0
