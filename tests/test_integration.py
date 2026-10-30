"""Real small-PDF integration checks. No mocked parser or OCR results."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from local_epub.pipeline import create_job, run_job, export_job, load_job, build_document, save_edit, exclude_pages
from local_epub.resources import check_resources, export_pack, import_pack

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
RESOURCES = ROOT / "resources"


def test_text_roundtrip_edits_cache_and_partial(tmp_path):
    source = FIXTURES / "01_chinese_crosspage.pdf"
    before = source.read_bytes()
    job = tmp_path / "job"
    create_job(source, job)
    run_job(job, RESOURCES)
    doc = build_document(job)
    assert not doc["missing_pages"]
    assert any("阅读" in b.get("text", "") for b in doc["blocks"])
    assert all(b["sources"] and "confidence" in b for b in doc["blocks"])
    paragraph = next(b for b in doc["blocks"] if b["kind"] == "paragraph" and not b.get("page_margin_role"))
    manual = paragraph["text"] + "人工修订保留标记"
    save_edit(job, paragraph["id"], manual)
    times = {p.name: p.stat().st_mtime_ns for p in (job / "pages").glob("*.json")}
    out = tmp_path / "book.epub"
    report = export_job(job, out, title="测试书名", font_size=1.3)
    assert report["format"]["internal"]["status"] == "passed"
    assert report["content"]["status"] == "requires_human_review"
    assert times == {p.name: p.stat().st_mtime_ns for p in (job / "pages").glob("*.json")}
    run_job(job, RESOURCES, retry_pages=[1])
    assert any(b.get("text") == manual for b in build_document(job)["blocks"])
    export_job(job, out)
    with zipfile.ZipFile(out) as z:
        body = ''.join(z.read(n).decode() for n in z.namelist() if n.endswith('.xhtml'))
        assert "人工修订保留标记" in body
        assert z.infolist()[0].compress_type == zipfile.ZIP_STORED
    exclude_pages(job, [2])
    with pytest.raises(ValueError, match="部分"):
        export_job(job, out)
    assert export_job(job, out, allow_partial=True)["complete"] is False
    assert source.read_bytes() == before


def test_missing_models_failed_pages_resume(tmp_path):
    job = tmp_path / "job"
    create_job(FIXTURES / "02_mixed_bad_layer.pdf", job)
    result = run_job(job, tmp_path / "missing-models")
    assert result["status"] == "incomplete"
    assert result["pages"][0]["status"] == "success"
    assert all(p["status"] == "failed" and "资源" in p["error"] for p in result["pages"][1:])
    assert len(build_document(job)["missing_pages"]) == 3
    if not check_resources(RESOURCES)["ok"]:
        pytest.skip("local models not prepared; failure tracking assertions passed")
    result = run_job(job, RESOURCES, retry_pages=[2, 3, 4])
    # Model identity change invalidates previous page cache, so a full resume is required.
    result = run_job(job, RESOURCES)
    assert result["status"] == "complete"
    pages = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((job / "pages").glob("*.json"))]
    assert [p["method"] for p in pages] == ["text", "ocr", "ocr", "mixed"]
    text = ''.join(b.get('text', '') for p in pages for b in p['blocks'])
    assert 'cid:999' not in text
    assert '青山不改' in text
    assert '春风又绿江南岸' in text
    assert any(b.get("confidence") for p in pages[1:] for b in p["blocks"])


def test_cache_identity_and_conflicting_edits(tmp_path):
    job = tmp_path / "job"
    create_job(FIXTURES / "01_chinese_crosspage.pdf", job)
    run_job(job, RESOURCES)
    key = load_job(job)["pages"][0]["cache_key"]
    block = next(b for b in build_document(job)["blocks"] if b["kind"] == "paragraph")
    save_edit(job, block["id"], "持久修订")
    # A real new parsed block may differ after OCR/model/parameter changes. Change the stored
    # automatic draft to test the reconciliation boundary, not to fake OCR quality.
    path = job / "pages/0001.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    next(b for b in data["blocks"] if b["id"] == block["id"])["text"] += "新解析结果"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert build_document(job)["edit_conflicts"][0]["edit"]["text"] == "持久修订"
    create_job(FIXTURES / "01_chinese_crosspage.pdf", job, {"dpi": 190})
    run_job(job, RESOURCES)
    assert load_job(job)["pages"][0]["cache_key"] != key
    assert json.loads((job / "edits.json").read_text(encoding="utf-8"))["blocks"][block["id"]]["text"] == "持久修订"
    with pytest.raises(ValueError, match="摘要"):
        create_job(FIXTURES / "03_columns_figures_notes.pdf", job)
    with (job / "source.pdf").open("ab") as f:
        f.write(b"\n%changed")
    with pytest.raises(ValueError, match="摘要"):
        run_job(job, RESOURCES)


def test_cancel_restart_and_page_recovery(tmp_path):
    job = tmp_path / "job"
    cancel = tmp_path / "cancel"
    args = [sys.executable, "-m", "local_epub", "convert", str(FIXTURES / "02_mixed_bad_layer.pdf"), "--job", str(job), "--resources", str(RESOURCES), "--cancel-file", str(cancel)]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env={**__import__('os').environ, "PYTHONPATH": str(ROOT / 'src')})
    for line in proc.stdout:
        event = json.loads(line)
        if event.get("stage") == "parse":
            cancel.touch()
    assert proc.wait(timeout=120) == 130
    assert load_job(job)["status"] == "cancelled"
    assert any(p["status"] == "pending" for p in load_job(job)["pages"])
    cancel.unlink()
    if check_resources(RESOURCES)["ok"]:
        assert run_job(job, RESOURCES)["status"] == "complete"


def test_resource_integrity_and_unsafe_zip(tmp_path):
    if not check_resources(RESOURCES)["ok"]:
        pytest.skip("Models not prepared")
    pack = tmp_path / "pack.zip"
    export_pack(RESOURCES, pack)
    dest = tmp_path / "resources"
    assert import_pack(pack, dest)["ok"]
    with (dest / "det.onnx").open("ab") as f:
        f.write(b"bad")
    assert not check_resources(dest)["ok"]
    malicious = tmp_path / "bad.zip"
    with zipfile.ZipFile(malicious, "w") as z:
        z.writestr("../escape", "no")
    with pytest.raises(ValueError, match="路径"):
        import_pack(malicious, tmp_path / "unsafe-target")
    assert not (tmp_path / "escape").exists()


def test_password_input(tmp_path):
    from pypdf import PdfReader, PdfWriter
    from local_epub.backend import inspect_pdf
    reader = PdfReader(FIXTURES / "01_chinese_crosspage.pdf")
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("sample-password")
    path = tmp_path / "encrypted.pdf"
    writer.write(path)
    with pytest.raises(ValueError, match="密码"):
        inspect_pdf(path)
    with pytest.raises(ValueError, match="密码"):
        inspect_pdf(path, "wrong")
    assert inspect_pdf(path, "sample-password")["page_count"] == 2
    job = tmp_path / "encrypted-job"
    create_job(path, job, password="sample-password")
    assert run_job(job, RESOURCES, password="sample-password")["status"] == "complete"
    assert "sample-password" not in (job / "job.json").read_text(encoding="utf-8")


def test_fresh_cli_mixed_first_attempt(tmp_path):
    if not check_resources(RESOURCES)["ok"]:
        pytest.skip("Models not prepared")
    job = tmp_path / "中文任务"
    output = tmp_path / "混合书.epub"
    result = subprocess.run([sys.executable, "-m", "local_epub", "convert", str(FIXTURES / "02_mixed_bad_layer.pdf"), "--job", str(job), "--resources", str(RESOURCES), "--output", str(output)], capture_output=True, text=True, encoding="utf-8", timeout=180, env={**__import__('os').environ, "PYTHONPATH": str(ROOT / 'src'), "PYTHONIOENCODING": "ascii"})
    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1]["output"] == str(output)
    assert "正在处理" in result.stdout
    assert output.exists()
    assert all(p["status"] == "success" for p in load_job(job)["pages"])


def test_explicit_crosspage_merge_retains_sources_and_revisions(tmp_path):
    from local_epub.pipeline import merge_blocks
    job = tmp_path / "join"
    create_job(FIXTURES / "01_chinese_crosspage.pdf", job)
    run_job(job, RESOURCES)
    doc = build_document(job)
    candidate = next(i for i in doc["issues"] if i.get("code") == "cross_page_candidate")
    first, second = candidate["blocks"]
    before = next(b for b in doc["blocks"] if b["id"] == first)
    save_edit(job, first, before["text"] + "校对标记")
    merge_blocks(job, first, second)
    merged = next(b for b in build_document(job)["blocks"] if b["id"] == first)
    assert {s["page"] for s in merged["sources"]} == {1, 2}
    assert "校对标记" in merged["text"]
    assert not any(b["id"] == second for b in build_document(job)["blocks"])
    run_job(job, RESOURCES, retry_pages=[1, 2])
    merged = next(b for b in build_document(job)["blocks"] if b["id"] == first)
    assert "校对标记" in merged["text"]


def test_import_never_deletes_unrelated_destination(tmp_path):
    if not check_resources(RESOURCES)["ok"]:
        pytest.skip("Models not prepared")
    pack = tmp_path / "pack.zip"
    export_pack(RESOURCES, pack)
    dest = tmp_path / "documents"
    dest.mkdir()
    (dest / "important.txt").write_text("must survive")
    sibling = tmp_path / "documents.previous"
    sibling.mkdir()
    (sibling / "old.txt").write_text("also survive")
    with pytest.raises(ValueError, match="非空"):
        import_pack(pack, dest)
    assert (dest / "important.txt").read_text(encoding="utf-8") == "must survive"
    assert (sibling / "old.txt").read_text(encoding="utf-8") == "also survive"
    valid = tmp_path / "valid-resources"
    import_pack(pack, valid)
    (valid / "unrelated.txt").write_text("keep")
    with pytest.raises(ValueError, match="未声明"):
        import_pack(pack, valid)
    assert (valid / "unrelated.txt").read_text(encoding="utf-8") == "keep"


def test_broken_page_checkpoint_is_missing_and_retried(tmp_path):
    job = tmp_path / "corrupt"
    create_job(FIXTURES / "01_chinese_crosspage.pdf", job)
    run_job(job, RESOURCES)
    page_path = job / "pages/0002.json"
    page_path.write_text("{}")
    doc = build_document(job)
    assert doc["missing_pages"][0]["number"] == 2
    with pytest.raises(ValueError, match="部分"):
        export_job(job, tmp_path / "should-not-exist.epub")
    events = []
    run_job(job, RESOURCES, progress=events.append)
    assert not build_document(job)["missing_pages"]
    assert any(e.get("page") == 2 and e.get("stage") == "parse" for e in events)
