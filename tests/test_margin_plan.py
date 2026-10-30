"""Source-to-running-margin risks, using real native PDF parsing and edits."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from local_epub.page_info import build_page_info
from local_epub.page_margins import build_margin_plan, export_document, source_spans_for_block
from local_epub.page_number_repairs import save_page_number_repair
from local_epub.pipeline import build_document, create_job, merge_blocks, run_job, save_edit
from local_epub.storage import atomic_json, read_json


ROOT = Path(__file__).resolve().parents[1]


def snapshot(job):
    return {str(p.relative_to(job)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for group in ("raw", "pages") for p in (job / group).glob("*.json")}


@pytest.fixture
def native_job(tmp_path):
    source = tmp_path / "native-running-matter.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(420, 595))
    for page in range(1, 5):
        pdf.setFont("Helvetica", 9)
        pdf.drawString(40, 573, "Running A" if page <= 2 else "Running B")
        pdf.drawCentredString(210, 22, str(19 + page))
        pdf.setFont("Helvetica", 13)
        pdf.drawString(40, 490, f"Native page {page} first wrapped line")
        pdf.drawString(40, 475, "continues on the same source page.")
        pdf.drawString(40, 400, f"Last paragraph of source page {page}")
        pdf.showPage()
    pdf.save()
    job = tmp_path / "native-task"
    create_job(source, job, {"mode": "text"})
    assert run_job(job, tmp_path / "no-resources")["status"] == "complete"
    return job


def body(document, number):
    return [b for b in document["blocks"] if b["kind"] == "paragraph"
            and not b.get("page_margin_role") and {s["page"] for s in b["sources"]} == {number}]


def test_real_native_headers_numbers_and_automatic_line_joins_are_source_bound(native_job):
    document = build_document(native_job)
    before = deepcopy(document)
    cached = snapshot(native_job)
    plan = build_margin_plan(document)
    assert [p["header_text"] for p in plan["pages"]] == ["Running A", "Running A", "Running B", "Running B"]
    assert [p["page_number_text"] for p in plan["pages"]] == ["20", "21", "22", "23"]
    assert all(p["page_number"]["provenance"] == "recognized" for p in plan["pages"])
    wrapped = next(b for b in body(document, 1) if len(b["sources"]) == 2)
    assert wrapped["source_spans"][0]["end"] == len(wrapped["text"])
    assert plan["blocks"][wrapped["id"]]["events"] == [{"offset": 0, "page": 1}]
    enabled = export_document(document, True)
    assert enabled["margin_plan"]["blocks"] == plan["blocks"]
    assert [p["footer_text"] for p in enabled["margin_plan"]["pages"]] == [p["footer_text"] for p in plan["pages"]]
    assert all(b["exclude_from_export"] for b in enabled["blocks"] if b.get("page_margin_role"))
    assert "margin_plan" not in export_document(enabled, False)
    assert document == before and snapshot(native_job) == cached


def test_manual_crosspage_merge_uses_exact_corrected_unicode_ranges_and_survives_reopen(native_job):
    document = build_document(native_job)
    first, second = body(document, 1)[-1], body(document, 2)[0]
    corrected = first["text"] + " 校对😀"
    save_edit(native_job, first["id"], corrected)
    cached = snapshot(native_job)
    merge_blocks(native_job, first["id"], second["id"])
    document = build_document(native_job)
    merged = next(b for b in document["blocks"] if b["id"] == first["id"])
    spans = source_spans_for_block(merged)
    assert [s["page"] for s in spans] == [1, 2]
    assert spans[1]["start"] == len(merged["text"]) - len(second["text"])
    assert merged["text"][spans[1]["start"]:spans[1]["end"]] == second["text"]
    assert merged["text"][:spans[0]["end"]].rstrip() == corrected
    assert build_margin_plan(document)["blocks"][first["id"]]["events"] == [
        {"offset": 0, "page": 1}, {"offset": spans[1]["start"], "page": 2}]
    save_edit(native_job, first["id"], merged["text"], kind="paragraph")
    reopened = next(b for b in build_document(native_job)["blocks"] if b["id"] == first["id"])
    assert reopened["source_spans"] == spans
    assert snapshot(native_job) == cached


def test_arbitrary_multi_page_edit_clears_mapping_and_next_reliable_block_restores(native_job):
    document = build_document(native_job)
    first, second = body(document, 1)[-1], body(document, 2)[0]
    following = body(document, 2)[1]
    merge_blocks(native_job, first["id"], second["id"])
    merged = next(b for b in build_document(native_job)["blocks"] if b["id"] == first["id"])
    save_edit(native_job, first["id"], "无法定位的新开头。" + merged["text"])
    document = build_document(native_job)
    plan = build_margin_plan(document)
    assert plan["blocks"][first["id"]] == {"reliable": False, "spans": [], "events": [{"offset": 0, "page": None}]}
    assert plan["blocks"][following["id"]]["events"] == [{"offset": 0, "page": 2}]
    assert any(i["code"] == "margin_source_unresolved" and i["block_id"] == first["id"] for i in plan["issues"])
    exported = export_document(document, True)
    assert all(issue in exported["issues"] for issue in plan["issues"])
    assert export_document(exported, True)["issues"] == exported["issues"]
    # Same-length replacement must not accidentally pass a mere bounds check.
    copied = deepcopy(merged)
    copied["text"] = "X" + copied["text"][1:]
    assert source_spans_for_block(copied) is None


def test_chained_merges_preserve_every_original_boundary(native_job):
    document = build_document(native_job)
    first = body(document, 1)[-1]
    rest = body(document, 2) + body(document, 3)[:1]
    for block in rest:
        merge_blocks(native_job, first["id"], block["id"])
    merged = next(b for b in build_document(native_job)["blocks"] if b["id"] == first["id"])
    spans = source_spans_for_block(merged)
    assert [s["page"] for s in spans] == [1, 2, 2, 3]
    for span, source in zip(spans, [first] + rest):
        assert merged["text"][span["start"]:span["end"]].rstrip() == source["text"]
    plan = build_margin_plan(build_document(native_job))
    assert plan["blocks"][first["id"]]["events"] == [{"offset": s["start"], "page": s["page"]} for s in spans]


def test_legacy_unchanged_merge_recovers_offsets_but_unknown_sources_clear(native_job):
    document = build_document(native_job)
    first, second = body(document, 1)[-1], body(document, 2)[0]
    merge_blocks(native_job, first["id"], second["id"])
    edits = read_json(native_job / "edits.json")
    edits["blocks"][first["id"]].pop("source_spans")
    edits["blocks"][first["id"]].pop("source_spans_text_sha256")
    atomic_json(native_job / "edits.json", edits)
    document = build_document(native_job)
    assert build_margin_plan(document)["blocks"][first["id"]]["reliable"]
    unknown = body(document, 3)[0]
    unknown["sources"] = []
    assert build_margin_plan(document)["blocks"][unknown["id"]]["events"] == [{"offset": 0, "page": None}]
    document["missing_pages"].append({"number": 4, "status": "failed", "error": "source page unavailable"})
    page4 = body(document, 4)[0]
    plan = build_margin_plan(document)
    assert plan["blocks"][page4["id"]]["events"] == [{"offset": 0, "page": None}]
    assert plan["pages"][3]["header_text"] == plan["pages"][3]["footer_text"] == ""


def test_only_current_confirmed_repairs_supply_a_missing_label(tmp_path):
    job = tmp_path / "gap"
    create_job(ROOT / "tests/fixtures/04_page_number_gap.pdf", job, {"mode": "text"})
    assert run_job(job, tmp_path / "no-resources")["status"] == "complete"
    document = build_document(job)
    assert build_margin_plan(document)["pages"][2]["page_number"] is None
    record = save_page_number_repair(job, 3, "22")
    confirmed = build_document(job)
    planned = build_margin_plan(confirmed)["pages"][2]["page_number"]
    assert planned == record and planned["provenance"] == "user_confirmed_inference" and planned["confidence"] is None
    assert build_page_info(confirmed)[2]["page_numbers"] == []
    forged = deepcopy(confirmed)
    forged["page_number_repairs"][0]["label"] = "222"
    plan = build_margin_plan(forged)
    assert plan["pages"][2]["page_number"] is None
    assert any(i["code"] == "margin_page_number_repair_conflict" for i in plan["issues"])
    raw = job / "raw/0003.json"
    atomic_json(raw, {**read_json(raw), "reprocessed": True})
    stale = build_margin_plan(build_document(job))
    assert stale["pages"][2]["page_number"] is None


def test_number_ambiguity_never_selects_a_candidate_and_corrections_are_not_ocr(native_job):
    document = build_document(native_job)
    number = next(b for b in document["blocks"] if b.get("page_margin_role") == "page_number" and b["sources"][0]["page"] == 1)
    # Two independently positioned repeated number series are both recognized;
    # choosing the first would hide a real ambiguity.
    for page in document["pages"]:
        original = next(b for b in document["blocks"] if b.get("page_margin_role") == "page_number"
                        and b["sources"][0]["page"] == page["number"])
        duplicate = deepcopy(original)
        duplicate["id"] += "-duplicate"
        for source in duplicate["sources"]:
            source["bbox"][0] -= 130
            source["bbox"][2] -= 130
        page["blocks"].append(deepcopy(duplicate))
        document["blocks"].append(duplicate)
    plan = build_margin_plan(document)
    assert plan["pages"][0]["page_number"] is None
    assert any(i["code"] == "margin_page_number_ambiguous" for i in plan["issues"])
    save_edit(native_job, number["id"], "二十")
    corrected = build_margin_plan(build_document(native_job))["pages"][0]["page_number"]
    assert corrected["label"] == "二十" and corrected["provenance"] == "human_correction"
    assert corrected["confidence"] is None


def test_image_with_empty_caption_still_sets_source_and_empty_page_margin_resets(native_job):
    document = build_document(native_job)
    image = {"id": "empty-caption-image", "kind": "image", "text": "", "asset": "assets/example.png",
             "sources": [{"page": 2, "bbox": [40, 120, 200, 250]}], "issues": []}
    document["blocks"].append(image)
    plan = build_margin_plan(document)
    assert plan["blocks"][image["id"]]["events"] == [{"offset": 0, "page": 2}]
    assert plan["blocks"][image["id"]]["spans"][0]["start"] == plan["blocks"][image["id"]]["spans"][0]["end"] == 0
    margins = {b["id"] for b in document["blocks"] if b.get("page_margin_role") and b["sources"][0]["page"] == 2}
    document["blocks"] = [b for b in document["blocks"] if b["id"] not in margins]
    plan = build_margin_plan(document)
    assert plan["pages"][1]["header_text"] == plan["pages"][1]["footer_text"] == ""
    assert plan["blocks"][image["id"]]["events"] == [{"offset": 0, "page": 2}]
