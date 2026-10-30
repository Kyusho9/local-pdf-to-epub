"""Page-edge output policy against real PDFs, plus conservative-rule negatives.

The shared mixed job is parsed once with local OCR, then copied for independent
checks. Constructed geometry below tests only the rule's rejection boundary;
it is not evidence of OCR accuracy.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile

import pytest

from local_epub.margins import annotate_margins
from local_epub.page_margins import export_document
from local_epub.pipeline import (
    build_document, create_job, exclude_pages, export_job, run_job, save_edit,
    set_export_override,
)
from local_epub.resources import check_resources


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
RESOURCES = ROOT / "resources"
XHTML = "{http://www.w3.org/1999/xhtml}"
EPUB = "{http://www.idpf.org/2007/ops}"


def normalized(text):
    return re.sub(r"\s+", "", text)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def chapter_roots(path):
    with zipfile.ZipFile(path) as archive:
        return {name: ET.fromstring(archive.read(name)) for name in archive.namelist()
                if re.fullmatch(r"EPUB/chapter-\d+\.xhtml", name)}


def chapter_text(path):
    return "\n".join("".join(root.itertext()) for root in chapter_roots(path).values())


def cache_snapshot(job):
    return {str(path.relative_to(job)): (path.stat().st_mtime_ns,
                                       hashlib.sha256(path.read_bytes()).hexdigest())
            for folder in ("pages", "raw") for path in (job / folder).glob("*.json")}


def block_by_id(job, block_id):
    return next(block for block in build_document(job)["blocks"] if block["id"] == block_id)


def assert_truth_in_book(path, fixture):
    text = normalized(chapter_text(path))
    position = 0
    for page in read_json(FIXTURES / (fixture + ".truth.json"))["pages"]:
        for fragment in page["ordered_fragments"]:
            fragment = normalized(fragment)
            found = text.find(fragment, position)
            assert found >= 0, (page["page"], fragment)
            position = found + len(fragment)


@pytest.fixture(scope="module")
def parsed_mixed(tmp_path_factory):
    if not check_resources(RESOURCES)["ok"]:
        pytest.skip("Real mixed-PDF margin tests require the prepared local OCR pack")
    job = tmp_path_factory.mktemp("margin-source") / "job"
    create_job(FIXTURES / "02_mixed_bad_layer.pdf", job)
    result = run_job(job, RESOURCES)
    assert result["status"] == "complete"
    assert [page["method"] for page in result["pages"]] == ["text", "ocr", "ocr", "mixed"]
    return job


@pytest.fixture
def mixed_job(parsed_mixed, tmp_path):
    return Path(shutil.copytree(parsed_mixed, tmp_path / "mixed-job"))


def test_real_mixed_filters_only_export_and_groups_readable_report(mixed_job, tmp_path):
    snapshot = cache_snapshot(mixed_job)
    assert len(snapshot) == 8  # All four raw and automatic page records remain.
    document = build_document(mixed_job)
    assert all(not block["exclude_from_export"] for block in document["blocks"])
    margins = [block for block in document["blocks"] if block.get("page_margin_role")]
    assert Counter(block["page_margin_role"] for block in margins) == {
        "header": 4, "page_number": 4,
    }
    assert sorted((block["sources"][0]["page"], block["text"].strip())
                  for block in margins if block["page_margin_role"] == "page_number") == [
        (1, "1"), (2, "2"), (3, "3"), (4, "4"),
    ]
    original = deepcopy(document)
    output_document = export_document(document, False)
    assert document == original  # Only the output copy omits page-edge matter.
    assert {b["id"] for b in output_document["blocks"] if b["exclude_from_export"]} == {
        b["id"] for b in margins}
    stored = {block["id"]: block for path in (mixed_job / "pages").glob("*.json")
              for block in read_json(path)["blocks"]}
    for block in margins:
        assert stored[block["id"]]["text"] == block["text"]
        assert stored[block["id"]]["sources"] == block["sources"]
        assert block["export_override"] == "auto"

    output = tmp_path / "default.epub"
    report = export_job(mixed_job, output)
    paragraphs = {normalized("".join(element.itertext()))
                  for root in chapter_roots(output).values()
                  for element in root.iter(XHTML + "p")}
    assert all(normalized(block["text"]) not in paragraphs for block in margins)
    assert_truth_in_book(output, "02_mixed_bad_layer")
    assert cache_snapshot(mixed_job) == snapshot
    assert {block["id"] for block in build_document(mixed_job)["blocks"]} == {
        block["id"] for block in document["blocks"]}

    content = report["content"]
    assert content["excluded_blocks"] == []  # No automatic body-deduplication list.
    assert content["margin_omitted_count"] == 8
    assert content["source_block_count"] - content["block_count"] == 8
    grouped_scan = [item for item in content["review"] if item["code"] == "scan_layout_review"]
    assert len(grouped_scan) == 1
    assert grouped_scan[0]["pages"] == [2, 3] and grouped_scan[0]["count"] == 2
    processing = {item["code"]: item for item in content["processing"]}
    assert processing["text_layer_replaced"]["pages"] == [3]
    assert processing["image_text_reflow"]["pages"] == [4]
    assert not {"margin_content", *processing} & {item["code"] for item in content["review"]}
    machine = read_json(output.with_suffix(".report.json"))
    assert machine["content"]["source_issues"] == content["source_issues"]
    assert any(issue.get("code") == "margin_content" and issue.get("bbox")
               for issue in content["source_issues"] if isinstance(issue, dict))
    with zipfile.ZipFile(output) as archive:
        explanation = "".join(ET.fromstring(archive.read("EPUB/report.xhtml")).itertext())
    for token in ('"code"', '"bbox"', "scan_layout_review", "text_layer_replaced"):
        assert token not in explanation
    assert "第 2、3 页" in explanation
    assert report["format"]["internal"]["status"] == "passed"
    assert content["status"] == "requires_human_review"


def test_manual_visibility_and_text_survive_export_and_real_retry(mixed_job, tmp_path):
    document = build_document(mixed_job)
    header = next(block for block in document["blocks"] if block.get("page_margin_role") == "header")
    paragraph = next(block for block in document["blocks"]
                     if block["kind"] == "paragraph" and not block.get("page_margin_role")
                     and block["sources"][0]["page"] == 1)
    block_id = paragraph["id"]
    snapshot = cache_snapshot(mixed_job)
    output = tmp_path / "choices.epub"
    set_export_override(mixed_job, block_id, "keep")
    assert not block_by_id(mixed_job, block_id)["exclude_from_export"]
    assert block_by_id(mixed_job, block_id)["export_override"] == "keep"
    export_job(mixed_job, output)
    assert normalized(paragraph["text"]) in normalized(chapter_text(output))
    set_export_override(mixed_job, block_id, "auto")
    assert not block_by_id(mixed_job, block_id)["exclude_from_export"]

    # Legacy manual body choices remain source-bound and reversible.
    manual = paragraph["text"] + "人工补充必须保留"
    save_edit(mixed_job, block_id, manual)
    assert not block_by_id(mixed_job, block_id)["exclude_from_export"]
    export_job(mixed_job, output, font_size=1.3, line_height=2.0)
    assert manual in chapter_text(output)
    set_export_override(mixed_job, block_id, "exclude")
    assert block_by_id(mixed_job, block_id)["exclude_from_export"]
    export_job(mixed_job, output)
    assert manual not in chapter_text(output)
    edits = read_json(mixed_job / "edits.json")
    assert edits["blocks"][block_id]["text"] == manual
    assert edits["visibility"][block_id]["decision"] == "exclude"

    # A corrected page header is saved, including historical visibility choices,
    # but cannot bypass the global choice to omit page-edge content from EPUB.
    corrected_header = "人工校对后的页眉仍须保存"
    save_edit(mixed_job, header["id"], corrected_header)
    for decision in ("keep", "exclude", "auto"):
        set_export_override(mixed_job, header["id"], decision)
        corrected = block_by_id(mixed_job, header["id"])
        assert corrected["text"] == corrected_header and corrected["edited"]
        assert corrected["page_margin_role"] == "header"
        assert not corrected["exclude_from_export"]
        export_job(mixed_job, output, retain_page_margins=False)
        assert corrected_header not in chapter_text(output)
    assert read_json(mixed_job / "edits.json")["blocks"][header["id"]]["text"] == corrected_header
    assert cache_snapshot(mixed_job) == snapshot  # No OCR/cache rewriting for choices or style.

    assert run_job(mixed_job, RESOURCES, retry_pages=[1])["status"] == "complete"
    retried = block_by_id(mixed_job, block_id)
    assert retried["text"] == manual and retried["export_override"] == "exclude"
    assert retried["exclude_from_export"]
    set_export_override(mixed_job, block_id, "auto")
    assert not block_by_id(mixed_job, block_id)["exclude_from_export"]
    export_job(mixed_job, output)
    assert manual in chapter_text(output)
    assert block_by_id(mixed_job, header["id"])["text"] == corrected_header
    assert corrected_header not in chapter_text(output)


def test_changed_reextraction_cannot_reuse_an_old_exclusion(mixed_job, tmp_path):
    paragraph = next(block for block in build_document(mixed_job)["blocks"]
                     if block["kind"] == "paragraph" and not block.get("decoration")
                     and block["sources"][0]["page"] == 1)
    block_id = paragraph["id"]
    set_export_override(mixed_job, block_id, "exclude")
    old_decision = read_json(mixed_job / "edits.json")["visibility"][block_id]
    # A real switch from the native text layer to OCR changes source geometry.
    # Do not fake confidence or OCR text to exercise the signature boundary.
    create_job(FIXTURES / "02_mixed_bad_layer.pdf", mixed_job, {"mode": "ocr"})
    result = run_job(mixed_job, RESOURCES)
    assert result["status"] == "complete" and result["pages"][0]["method"] == "ocr"
    document = build_document(mixed_job)
    conflict = next(item for item in document["export_conflicts"] if item["block_id"] == block_id)
    assert conflict["decision"] == "exclude"
    changed = next(block for block in document["blocks"] if block["id"] == block_id)
    assert changed["sources"] != old_decision["sources"]
    assert not changed["exclude_from_export"]
    assert read_json(mixed_job / "edits.json")["visibility"][block_id] == old_decision
    output = tmp_path / "changed.epub"
    report = export_job(mixed_job, output)
    assert normalized(changed["text"]) in normalized(chapter_text(output))
    assert any(item["block_id"] == block_id for item in report["content"]["export_conflicts"])


def test_footnotes_tables_images_and_partial_notice_survive_filtering(tmp_path):
    job = tmp_path / "complex"
    create_job(FIXTURES / "03_columns_figures_notes.pdf", job)
    assert run_job(job, RESOURCES)["status"] == "complete"
    document = build_document(job)
    assert all(not block["exclude_from_export"] for block in document["blocks"]
               if block["kind"] in ("footnote", "table", "image"))
    output = tmp_path / "complex.epub"
    report = export_job(job, output)
    assert_truth_in_book(output, "03_columns_figures_notes")
    roots = chapter_roots(output)
    tables = [[["".join(cell.itertext()) for cell in row.findall(XHTML + "td")]
               for row in table.iter(XHTML + "tr")]
              for root in roots.values() for table in root.iter(XHTML + "table")]
    truth = read_json(FIXTURES / "03_columns_figures_notes.truth.json")
    assert truth["pages"][1]["table"] in tables
    assert report["content"]["image_count"] >= 3
    assert report["content"]["linked_footnotes"] == 1
    note_refs = [element for root in roots.values() for element in root.iter(XHTML + "a")
                 if element.get(EPUB + "type") == "noteref"]
    assert len(note_refs) == 1
    filename, anchor = note_refs[0].attrib["href"].split("#")
    note = next(element for element in roots["EPUB/" + filename].iter()
                if element.get("id") == anchor)
    assert note.tag == XHTML + "aside" and note.find(".//" + XHTML + "a") is not None

    exclude_pages(job, [2])
    partial = export_job(job, output, allow_partial=True)
    assert not partial["complete"] and partial["content"]["missing_pages"][0]["number"] == 2
    with zipfile.ZipFile(output) as archive:
        explanation = "".join(ET.fromstring(archive.read("EPUB/report.xhtml")).itertext())
    assert "部分结果" in explanation and "第 2 页" in explanation
    assert '"code"' not in explanation and '"bbox"' not in explanation


def test_margin_rules_require_repeated_geometry_and_preserve_non_margins():
    """Pure rule negatives; constructed boxes do not stand in for OCR tests."""
    pages = [{"number": number, "width": 420, "height": 595} for number in (1, 2)]

    def block(number, text, bbox, kind="paragraph", **extra):
        return {"id": f"p{number}-{text}", "kind": kind, "text": text,
                "sources": [{"page": number, "bbox": bbox}], "confidence": None, **extra}

    body = [block(number, f"正文参照{line}", [40, y, 300, y + 13])
            for number in (1, 2) for line, y in enumerate((100, 125))]
    edge = [40, 20, 180, 29]
    cases = {
        "short_top_body_same_size": [block(n, "这是重复正文", [40, 20, 180, 33]) for n in (1, 2)],
        "different_top_sentences": [block(n, f"不同正文{n}", edge) for n in (1, 2)],
        "only_one_page": [block(1, "只有一处边缘文字", edge)],
        "footnotes": [block(n, "[1] 此处保留脚注", edge, kind="footnote") for n in (1, 2)],
        "footnote_as_paragraph": [block(n, "[1] 此处保留脚注", edge) for n in (1, 2)],
        "missing_bbox": [block(n, "没有坐标的文字", None) for n in (1, 2)],
        "numbers_in_body": [block(n, str(n), [200, 200, 210, 209]) for n in (1, 2)],
        "only_one_edge_number": [block(1, "1", [200, 565, 210, 574])],
        "circled_note_numbers": [block(n, digit, [200, 565, 210, 574]) for n, digit in ((1, "①"), (2, "②"))],
        "manual_edits": [block(n, "人工改过的页边文字", edge, edited=True) for n in (1, 2)],
    }
    for label, candidates in cases.items():
        blocks = deepcopy(body + candidates)
        original = deepcopy(blocks)
        annotate_margins(blocks, pages)
        assert all(block.get("decoration") is None for block in blocks), label
        assert [{key: value for key, value in block.items() if key != "decoration"}
                for block in blocks] == original, label
