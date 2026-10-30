"""Inference rule/persistence safety; these tests are not OCR accuracy evidence."""
from copy import deepcopy
import hashlib
import json
import zipfile

import pytest
from reportlab.pdfgen import canvas

from local_epub.page_info import build_page_info
from local_epub.page_number_repairs import (
    apply_repairs, attach_repair_fingerprints, clear_page_number_repair,
    save_page_number_repair, suggest_document_page_number_repairs,
    suggest_page_number_repairs,
)
from local_epub.pipeline import build_document, create_job, run_job, save_edit, set_export_override, export_job


def _infos(left="1", right="4", last=4):
    pages = [{"number": n, "status": "success", "page_numbers": [], "unclassified": []}
             for n in range(1, last + 1)]
    for page, label in ((pages[0], left), (pages[-1], right)):
        page["page_numbers"] = [{"id": f"p{page['number']}-number", "text": label,
                                 "classification": "page_number", "edited": False,
                                 "sources": [{"page": page["number"], "bbox": [200, 565, 210, 574]}]}]
    return pages


def test_unique_bounded_interpolation_preserves_digit_format_and_input():
    for left, right, expected in (("1", "4", ["2", "3"]), ("009", "012", ["010", "011"]),
                                  ("９", "１２", ["１０", "１１"]), ("9", "12", ["10", "11"])):
        infos = _infos(left, right)
        before = deepcopy(infos)
        result = suggest_page_number_repairs(infos)
        assert [(item["page"], item["label"]) for item in result] == [(2, expected[0]), (3, expected[1])]
        assert result[0]["evidence"]["left"]["text"] == left
        assert result[0]["evidence"]["right"]["sources"] == infos[-1]["page_numbers"][0]["sources"]
        assert "原书可能故意留空" in result[0]["message"] and "主动确认" in result[0]["message"]
        assert infos == before
    assert len(suggest_page_number_repairs(_infos("1", "5", 5))) == 3
    assert not suggest_page_number_repairs(_infos("1", "6", 6))


def test_missing_failed_conflicting_ambiguous_or_untrusted_evidence_blocks_suggestion():
    cases = [_infos("1", "5"), _infos("01", "4"), _infos("１", "4"), _infos("I", "IV")]
    missing = _infos(); missing.pop(1); cases.append(missing)
    duplicate = _infos(); duplicate.append(deepcopy(duplicate[1])); cases.append(duplicate)
    for key, value in (("status", "failed"), ("status", "excluded"), ("status", "pending"),
                       ("conflict", True), ("unclassified", [{"text": "可能存在未判定页码"}])):
        pages = _infos(); pages[1][key] = value; cases.append(pages)
    for key, value in (("edited", True), ("provenance", "user_confirmed_inference"), ("inferred", True)):
        pages = _infos(); pages[0]["page_numbers"][0][key] = value; cases.append(pages)
    multi = _infos(); multi[-1]["page_numbers"].append(deepcopy(multi[-1]["page_numbers"][0])); cases.append(multi)
    location = _infos(); location[-1]["page_numbers"][0]["sources"][0]["page"] = 1; cases.append(location)
    for pages in cases:
        assert suggest_page_number_repairs(pages) == [], pages


def test_inferred_label_is_never_used_to_extend_the_evidence_chain():
    infos = _infos()
    inferred = deepcopy(infos[0]["page_numbers"][0])
    inferred.update(id="p2-repair", text="2", provenance="user_confirmed_inference")
    inferred["sources"][0]["page"] = 2
    infos[1]["page_numbers"] = [inferred]
    assert not suggest_page_number_repairs(infos)


@pytest.fixture
def job(tmp_path):
    source = tmp_path / "intentional-blank-page-labels.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(420, 595))
    for number in range(1, 5):
        pdf.setFont("Helvetica", 13)
        pdf.drawString(40, 490, f"Native text sample page {number} with readable body content.")
        pdf.drawString(40, 460, "Missing printed labels must never be silently invented.")
        if number in (1, 4):
            pdf.setFont("Helvetica", 9)
            pdf.drawCentredString(210, 23, str(number))
        pdf.showPage()
    pdf.save()
    root = tmp_path / "job"
    create_job(source, root, {"mode": "text", "dpi": 180})
    result = run_job(root, tmp_path / "no-models")
    assert result["status"] == "complete"
    assert [c["label"] for c in suggest_document_page_number_repairs(build_document(root))] == ["2", "3"]
    return root


def _snapshot(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for group in ("raw", "pages") for path in (root / group).glob("*.json")}


def _applied(root):
    document = build_document(root)
    attach_repair_fingerprints(document, root)
    edits = json.loads((root / "edits.json").read_text(encoding="utf-8"))
    return apply_repairs(document, edits.get("page_number_repairs", {}))


def test_real_pdf_confirmation_is_separate_and_clear_preserves_audit_history(job):
    body = next(b for b in build_document(job)["blocks"] if b["sources"][0]["page"] == 2)
    save_edit(job, body["id"], body["text"] + " Human correction.")
    set_export_override(job, body["id"], "keep")
    edits_before = json.loads((job / "edits.json").read_text(encoding="utf-8"))
    before = _snapshot(job)
    record = save_page_number_repair(job, 2, "2")
    assert record["provenance"] == "user_confirmed_inference" and record["confidence"] is None
    current = _applied(job)
    assert current["page_number_repairs"] == [record] and not current["page_number_repair_conflicts"]
    assert build_page_info(current)[1]["page_numbers"] == []
    edits = json.loads((job / "edits.json").read_text(encoding="utf-8"))
    assert edits["blocks"] == edits_before["blocks"] and edits["visibility"] == edits_before["visibility"]
    assert _snapshot(job) == before
    assert clear_page_number_repair(job, 2) == {"page": 2, "cleared": True}
    edits = json.loads((job / "edits.json").read_text(encoding="utf-8"))
    assert edits["page_number_repairs"] == {}
    assert edits["page_number_repair_history"][-1]["record"] == record
    assert not _applied(job)["page_number_repairs"] and _snapshot(job) == before


def test_source_evidence_and_target_checkpoint_changes_preserve_conflicts(job):
    record = save_page_number_repair(job, 2, "2")
    original = build_document(job)
    attach_repair_fingerprints(original, job)
    records = {"2": record}
    for change in ("source", "evidence", "automatic", "fingerprints_missing"):
        document = deepcopy(original)
        if change == "source":
            document["source_sha256"] = "0" * 64
        elif change == "evidence":
            b = next(b for b in document["blocks"] if b["text"] == "4")
            b["sources"][0]["bbox"][0] += 1
        elif change == "automatic":
            document["page_number_repair_fingerprints"]["2"]["automatic_sha256"] = "0" * 64
        else:
            document.pop("page_number_repair_fingerprints")
        apply_repairs(document, records)
        assert not document["page_number_repairs"], change
        assert document["page_number_repair_conflicts"][0]["record"] == record
    raw = job / "raw" / "0002.json"
    value = json.loads(raw.read_text(encoding="utf-8")); value["retry_marker"] = "raw changed without changing cleaned blocks"
    raw.write_text(json.dumps(value), encoding="utf-8")
    changed = _applied(job)
    assert not changed["page_number_repairs"] and changed["page_number_repair_conflicts"]
    assert clear_page_number_repair(job, 2)["cleared"]  # stale confirmations remain clearable


def test_unresolved_document_conflict_and_invalid_save_do_not_change_edits(job):
    document = build_document(job)
    body = next(b for b in document["blocks"] if b["sources"][0]["page"] == 2)
    document["edit_conflicts"] = [{"block_id": body["id"], "edit": {"sources": body["sources"]}}]
    assert not suggest_document_page_number_repairs(document)
    before = (job / "edits.json").read_bytes()
    for page, label in ((2, "7"), (1, "1"), (True, "2"), (2, 2)):
        with pytest.raises(ValueError):
            save_page_number_repair(job, page, label)
        assert (job / "edits.json").read_bytes() == before
    (job / "raw" / "0002.json").unlink()
    with pytest.raises(ValueError, match="摘要不完整"):
        save_page_number_repair(job, 2, "2")
    assert (job / "edits.json").read_bytes() == before


def test_real_pdf_export_reopen_clear_never_auto_fills_intentionally_blank_page(job, tmp_path):
    # The fixture deliberately printed no label on pages 2 and 3. The actual
    # PDF parser, task store, and EPUB exporter are used throughout this flow.
    before = _snapshot(job)
    original_source = (job / "source.pdf").read_bytes()
    first = export_job(job, tmp_path / "before-confirmation.epub")
    assert first["content"]["page_number_repairs"] == []
    assert [item["page"] for item in first["content"]["page_number_suggestions"]] == [2, 3]
    record = save_page_number_repair(job, 2, "2")
    reopened = build_document(job)
    assert reopened["page_number_repairs"] == [record]
    assert [item["page"] for item in reopened["page_number_suggestions"]] == [3]
    assert build_page_info(reopened)[1]["page_numbers"] == []
    report = export_job(job, tmp_path / "confirmed.epub")
    assert report["content"]["page_number_repairs"][0]["provenance"] == "user_confirmed_inference"
    with zipfile.ZipFile(tmp_path / "confirmed.epub") as epub:
        note = epub.read("EPUB/report.xhtml").decode("utf-8")
        assert "人工推算" in note and "非 OCR" in note
        assert "EPUB/page-info.xhtml" not in epub.namelist()
    clear_page_number_repair(job, 2)
    last = export_job(job, tmp_path / "cleared.epub")
    assert last["content"]["page_number_repairs"] == []
    assert [item["page"] for item in last["content"]["page_number_suggestions"]] == [2, 3]
    assert _snapshot(job) == before and (job / "source.pdf").read_bytes() == original_source


def test_replaced_source_pdf_refuses_confirmation_without_touching_existing_records(job):
    record = save_page_number_repair(job, 2, "2")
    previous = (job / "edits.json").read_bytes()
    with (job / "source.pdf").open("ab") as stream:
        stream.write(b"\n% changed source snapshot\n")
    with pytest.raises(ValueError, match="原 PDF 已变化"):
        save_page_number_repair(job, 3, "3")
    assert (job / "edits.json").read_bytes() == previous
    assert clear_page_number_repair(job, 2)["cleared"]
    history = json.loads((job / "edits.json").read_text(encoding="utf-8"))["page_number_repair_history"]
    assert history[-1]["record"] == record
