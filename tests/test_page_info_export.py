"""Opt-in source-bound running margins without visible body/appendix substitutes."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import PurePosixPath
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

from test_margin_export import (
    ROOT, RESOURCES, FIXTURES, mixed_job, parsed_mixed, cache_snapshot, chapter_roots,
)
from local_epub.epub import write_epub
from local_epub.pipeline import build_document, export_job, load_job, save_edit

X = "{http://www.w3.org/1999/xhtml}"
E = "{http://www.idpf.org/2007/ops}"


def assert_links(archive):
    for filename in archive.namelist():
        if not filename.endswith(".xhtml"):
            continue
        for link in ET.fromstring(archive.read(filename)).iter(X + "a"):
            path, _, anchor = link.attrib["href"].partition("#")
            dest = str(PurePosixPath(filename).parent / path) if path else filename
            assert dest in archive.namelist(), (filename, dest)
            if anchor:
                assert any(node.get("id") == anchor for node in ET.fromstring(archive.read(dest)).iter())


def assert_no_page_information_substitute(output):
    with zipfile.ZipFile(output) as archive:
        assert "EPUB/page-info.xhtml" not in archive.namelist()
        for name in archive.namelist():
            if not name.endswith(".xhtml"):
                continue
            root = ET.fromstring(archive.read(name))
            assert all(not ({"page-list", "pagebreak"} & set(node.get(E + "type", "").split()))
                       for node in root.iter())
        assert_links(archive)


def test_body_only_default_reuses_real_cache_and_keeps_header_correction_in_job(mixed_job, tmp_path):
    snapshot = cache_snapshot(mixed_job)
    models = {path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
              for path in RESOURCES.rglob("*.onnx")}
    assert models
    document = build_document(mixed_job)
    header = next(b for b in document["blocks"] if b.get("page_margin_role") == "header")
    corrected = "人工校对后的原页页眉"
    save_edit(mixed_job, header["id"], corrected)

    # A reopened 0.1.2 task must migrate its old option without emitting an
    # appendix or silently treating it as a supported running-margin request.
    job = load_job(mixed_job)
    job["export_options"] = {"include_page_info": True, "font_size": 1.0}
    (mixed_job / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    bodies = []
    for index, options in enumerate(({}, {"retain_page_margins": False, "font_size": 1.4},
                                     {"include_page_info": False, "line_height": 2.0})):
        output = tmp_path / f"body-{index}.epub"
        report = export_job(mixed_job, output, **options)
        assert load_job(mixed_job)["export_options"]["retain_page_margins"] is False
        assert "include_page_info" not in load_job(mixed_job)["export_options"]
        assert report["export_options"]["retain_page_margins"] is False
        assert "source_page_info" not in report["content"]
        assert report["content"]["margin_omitted_count"] == 8
        bodies.append([("".join(node.itertext()), node.get("data-source-pages"))
                       for root in chapter_roots(output).values() for node in root.iter()
                       if node.get("data-source-pages") is not None])
        assert_no_page_information_substitute(output)
    assert bodies[0] == bodies[1] == bodies[2] and bodies[0]
    assert all(corrected not in text for text, _ in bodies[0])
    assert {page for _, pages in bodies[0] for page in pages.split(",")} == {"1", "2", "3", "4"}
    assert next(b for b in build_document(mixed_job)["blocks"] if b["id"] == header["id"])["text"] == corrected
    assert load_job(mixed_job)["export_options_history"][0]["include_page_info"] is True
    assert cache_snapshot(mixed_job) == snapshot
    assert models == {path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                      for path in models}


def test_running_margins_toggle_reuses_real_cache_and_exports_only_empty_source_markers(mixed_job, tmp_path):
    snapshot = cache_snapshot(mixed_job)
    document = build_document(mixed_job)
    header = next(b for b in document["blocks"] if b.get("page_margin_role") == "header")
    corrected = "人工校对页眉：真实页边专用"
    save_edit(mixed_job, header["id"], corrected)
    off = tmp_path / "without-margins.epub"
    on = tmp_path / "with-margins.epub"
    export_job(mixed_job, off)
    report = export_job(mixed_job, on, retain_page_margins=True, font_size=1.4)
    assert report["export_options"]["retain_page_margins"] is True
    assert load_job(mixed_job)["export_options"]["retain_page_margins"] is True
    assert report["content"]["running_margins"]["mapping"] == "first-source-on-reading-page"
    assert report["content"]["running_margins"]["profile"] == "vivliostyle-2.45.2"
    roots = chapter_roots(on)
    markers = [node for root in roots.values() for node in root.iter(X + "span")
               if node.get("data-srcpage") is not None]
    assert markers and all(not "".join(node.itertext()) for node in markers)
    assert corrected in {node.get("data-srchead") for node in markers}
    assert {node.get("data-srcpage") for node in markers} >= {"1", "2", "3", "4"}
    # Source data can drive a compatible reader's page boxes without inserting
    # visible text, changing chapter order or claiming identical PDF pagination.
    assert ["".join(root.itertext()) for root in roots.values()] == [
        "".join(root.itertext()) for root in chapter_roots(off).values()]
    with zipfile.ZipFile(on) as archive:
        assert "EPUB/page-info.xhtml" not in archive.namelist()
        assert "@page" not in archive.read("EPUB/style.css").decode("utf-8")
        nav = ET.fromstring(archive.read("EPUB/nav.xhtml"))
        page_list = next(node for node in nav.iter(X + "nav") if node.get(E + "type") == "page-list")
        assert page_list.get("hidden") == "hidden"
        assert [node.text for node in page_list.iter(X + "a")] == ["1", "2", "3", "4"]
        for name in ("EPUB/cover.xhtml", "EPUB/report.xhtml"):
            body = ET.fromstring(archive.read(name)).find(X + "body")
            assert {body.get(key) for key in ("data-srchead", "data-srcfoot", "data-srcpage")} == {""}
        assert_links(archive)
    export_job(mixed_job, off, retain_page_margins=False, line_height=2.1)
    assert_no_page_information_substitute(off)
    assert not any(node.get("data-srcpage") is not None for root in chapter_roots(off).values() for node in root.iter())
    assert cache_snapshot(mixed_job) == snapshot
    assert next(b for b in build_document(mixed_job)["blocks"] if b["id"] == header["id"])["text"] == corrected


def test_legacy_appendix_fails_before_overwriting_output_or_job(mixed_job, tmp_path):
    output = tmp_path / "existing.epub"
    export_job(mixed_job, output)
    watched = [output, output.with_suffix(".report.json"), mixed_job / "job.json",
               mixed_job / "document.json", mixed_job / "last-export.json"]
    before = {path: path.read_bytes() for path in watched}
    snapshot = cache_snapshot(mixed_job)
    document = build_document(mixed_job)
    for options in ({"include_page_info": True},):
        with pytest.raises(ValueError, match="页眉|附录"):
            export_job(mixed_job, output, title="不应被保存的标题", **options)
        with pytest.raises(ValueError, match="页眉|附录"):
            write_epub(document, mixed_job, output, **options)
        assert before == {path: path.read_bytes() for path in watched}
    assert cache_snapshot(mixed_job) == snapshot


def test_replaced_source_snapshot_cannot_overwrite_an_existing_export(mixed_job, tmp_path):
    output = tmp_path / "existing.epub"
    export_job(mixed_job, output)
    watched = [output, output.with_suffix(".report.json"), mixed_job / "job.json",
               mixed_job / "document.json", mixed_job / "edits.json", mixed_job / "last-export.json"]
    before = {path: path.read_bytes() for path in watched}
    (mixed_job / "source.pdf").write_bytes((FIXTURES / "01_chinese_crosspage.pdf").read_bytes())
    with pytest.raises(ValueError, match="源 PDF 摘要不匹配"):
        export_job(mixed_job, output, title="不应保存的元数据")
    assert before == {path: path.read_bytes() for path in watched}


def test_cli_accepts_explicit_running_margins_and_can_turn_them_off(mixed_job, tmp_path):
    snapshot = cache_snapshot(mixed_job)
    output = tmp_path / "中文兼容阅读器.epub"
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="ascii")
    for retain in (True, False):
        flag = "--retain-page-margins" if retain else "--no-retain-page-margins"
        result = subprocess.run([sys.executable, "-m", "local_epub", "export", str(mixed_job),
                                 "--output", str(output), flag], cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", timeout=30, env=env)
        assert result.returncode == 0, (result.stdout, result.stderr)
        event = json.loads(result.stdout.strip().splitlines()[-1])
        assert event["event"] == "done" and event["export_options"]["retain_page_margins"] is retain
        assert any(node.get("data-srcpage") is not None for root in chapter_roots(output).values()
                   for node in root.iter()) is retain
        if not retain:
            assert_no_page_information_substitute(output)
    assert cache_snapshot(mixed_job) == snapshot


def test_cli_rejects_legacy_appendix_without_mutation(mixed_job, tmp_path):
    flag = "--include-page-info"
    output = tmp_path / "existing.epub"
    export_job(mixed_job, output)
    before = {p: p.read_bytes() for p in (output, output.with_suffix(".report.json"), mixed_job / "job.json")}
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="ascii")
    result = subprocess.run([sys.executable, "-m", "local_epub", "export", str(mixed_job),
                             "--output", str(output), flag], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=30, env=env)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout.strip(), result.stderr
    event = json.loads(result.stdout.strip().splitlines()[-1])
    assert event["event"] == "error" and ("页眉" in event["message"] or "附录" in event["message"])
    assert before == {p: p.read_bytes() for p in before}
    fresh_job = tmp_path / "must-not-create"
    fresh_output = tmp_path / "must-not-export.epub"
    result = subprocess.run([sys.executable, "-m", "local_epub", "convert",
                             str(FIXTURES / "02_mixed_bad_layer.pdf"), "--job", str(fresh_job),
                             "--output", str(fresh_output), flag], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=30, env=env)
    assert result.returncode == 1
    assert not fresh_job.exists() and not fresh_output.exists()


def test_cross_page_provenance_and_missing_page_notice_remain_without_appendix(mixed_job, tmp_path):
    document = deepcopy(build_document(mixed_job))
    first = next(b for b in document["blocks"] if not b.get("page_margin_role")
                 and not b.get("exclude_from_export"))
    first["sources"].append({"page": 2, "bbox": None})
    document["missing_pages"].append({"number": 5, "status": "failed", "error": "验收缺页"})
    output = tmp_path / "partial.epub"
    report = write_epub(document, mixed_job, output)
    assert not report["complete"]
    assert report["content"]["missing_pages"][-1]["number"] == 5
    assert "source_page_info" not in report["content"]
    assert any(node.get("data-source-pages") == "1,2" for root in chapter_roots(output).values()
               for node in root.iter())
    assert_no_page_information_substitute(output)
    with zipfile.ZipFile(output) as archive:
        explanation = "".join(ET.fromstring(archive.read("EPUB/report.xhtml")).itertext())
    assert "部分结果" in explanation and "第 5 页" in explanation
    assert '"bbox"' not in explanation and '"code"' not in explanation
