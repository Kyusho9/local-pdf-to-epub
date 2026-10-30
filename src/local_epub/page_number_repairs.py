"""Explicitly confirmed page-number inference, separate from extraction results."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import time

from .storage import atomic_json, digest, job_lock, read_json


MAX_MISSING_PAGES = 3
PROVENANCE = "user_confirmed_inference"
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _number_format(text):
    if not isinstance(text, str):
        return None
    label = text.strip()
    if re.fullmatch(r"[0-9]{1,8}", label):
        style, digits = "ascii", label
    elif re.fullmatch(r"[０-９]{1,8}", label):
        style, digits = "fullwidth", label.translate(_FULLWIDTH)
    else:
        return None
    value = int(digits)
    if value <= 0:
        return None
    return value, (style, len(digits) if digits.startswith("0") else 0)


def _has_conflict(page):
    return any(page.get(key) for key in
               ("error", "conflict", "conflicts", "edit_conflicts", "export_conflicts", "page_number_repair_conflicts"))


def _evidence(page):
    values = page.get("page_numbers")
    if not isinstance(values, list) or len(values) != 1:
        return None
    item = values[0]
    if (not isinstance(item, dict) or item.get("edited") or item.get("inferred")
            or item.get("provenance") not in (None, "ocr", "native_text", "text_extraction")
            or item.get("classification") != "page_number" or _has_conflict(item)):
        return None
    parsed = _number_format(item.get("text"))
    sources = item.get("sources")
    if not parsed or not isinstance(sources, list) or not sources:
        return None
    for source in sources:
        if not isinstance(source, dict) or source.get("page") != page["number"]:
            return None
        bbox = source.get("bbox")
        if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
                or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in bbox)
                or not (bbox[0] < bbox[2] and bbox[1] < bbox[3])):
            return None
    if not isinstance(item.get("id"), str) or not item["id"]:
        return None
    return {"page": page["number"], "id": item["id"], "text": item["text"],
            "sources": deepcopy(sources)}, parsed


def suggest_page_number_repairs(page_infos):
    """Suggest only bounded interpolation between two unique actual labels.

    Page infos follow ``page_info.build_page_info``. The function is pure and
    never treats an edited or inferred label as evidence. Missing physical
    pages, unresolved page conflicts, and unclassified margin content are
    barriers, not permission to assume that a printed number exists.
    """
    if not isinstance(page_infos, list):
        return []
    pages = [p for p in page_infos if isinstance(p, dict) and _positive_int(p.get("number"))]
    if len(pages) != len(page_infos) or len({p["number"] for p in pages}) != len(pages):
        return []
    pages.sort(key=lambda p: p["number"])
    by_number = {p["number"]: p for p in pages}
    suggestions = []
    for left in pages:
        if left.get("status") != "success" or _has_conflict(left):
            continue
        first = _evidence(left)
        if first is None or left.get("unclassified"):
            continue
        for distance in range(1, MAX_MISSING_PAGES + 2):
            right = by_number.get(left["number"] + distance)
            if (right is None or right.get("status") != "success" or _has_conflict(right)
                    or right.get("unclassified") or not isinstance(right.get("page_numbers"), list)):
                break
            if not right["page_numbers"]:
                continue
            last = _evidence(right)
            if (distance > 1 and last is not None and first[1][1] == last[1][1]
                    and last[1][0] - first[1][0] == distance and first[0]["id"] != last[0]["id"]):
                style, padding = first[1][1]
                evidence = {"left": first[0], "right": last[0]}
                for offset in range(1, distance):
                    label = str(first[1][0] + offset).zfill(padding)
                    if style == "fullwidth":
                        label = label.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
                    page = left["number"] + offset
                    message = (f"根据原 PDF 第 {left['number']} 页实际识别的页码「{first[0]['text']}」"
                               f"与第 {right['number']} 页「{last[0]['text']}」，可建议本页为「{label}」。"
                               "原书可能故意留空；仅在你核对并主动确认后保存为人工推算，不标作 OCR 识别。")
                    suggestions.append({"page": page, "label": label, "evidence": deepcopy(evidence), "message": message})
            # Even an invalid, edited, or ambiguous observed label is a barrier.
            break
    counts = Counter(item["page"] for item in suggestions)
    return [item for item in suggestions if counts[item["page"]] == 1]


def suggest_document_page_number_repairs(document):
    """Document-aware wrapper, also rejecting unresolved source/edit conflicts."""
    from .page_info import build_page_info
    infos = build_page_info(document)
    affected = set()
    unknown_conflict = False
    conflicts = list(document.get("edit_conflicts", [])) + list(document.get("export_conflicts", []))
    conflicts += [issue for issue in document.get("issues", [])
                  if isinstance(issue, dict) and "conflict" in str(issue.get("code", ""))]
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            unknown_conflict = True
            continue
        sources = conflict.get("sources") or conflict.get("edit", {}).get("sources") or []
        numbers = {s.get("page") for s in sources if isinstance(s, dict) and _positive_int(s.get("page"))}
        numbers.update(p for p in conflict.get("pages", []) if _positive_int(p))
        if _positive_int(conflict.get("page")):
            numbers.add(conflict["page"])
        block_ids = set(conflict.get("blocks", [])) | {conflict.get("block_id")}
        for block in document.get("blocks", []):
            if block.get("id") in block_ids:
                numbers.update(s["page"] for s in block.get("sources", []) if _positive_int(s.get("page")))
        affected.update(numbers)
        unknown_conflict |= not bool(numbers)
    for page in infos:
        if unknown_conflict or page["number"] in affected:
            page["conflict"] = True
    return suggest_page_number_repairs(infos)


def attach_repair_fingerprints(document, job_dir):
    """Attach raw and automatic page fingerprints needed by ``apply_repairs``.

    This reads checkpoints only. No path is retained in the document, and a
    missing raw checkpoint cannot accidentally validate a saved inference.
    """
    root = Path(job_dir)
    pages = document.get("pages", [])
    counts = Counter(p.get("number") for p in pages if isinstance(p, dict))
    fingerprints = {}
    for page in pages:
        number = page.get("number")
        if not _positive_int(number) or counts[number] != 1:
            continue
        raw = root / "raw" / f"{number:04d}.json"
        try:
            raw_hash = digest(raw) if raw.is_file() else None
        except OSError:
            raw_hash = None
        fingerprints[str(number)] = {"raw_sha256": raw_hash, "automatic_sha256": _hash(page)}
    document["page_number_repair_fingerprints"] = fingerprints
    return document


def _binding(document, candidate):
    source_hash = document.get("source_sha256")
    fingerprints = document.get("page_number_repair_fingerprints", {}).get(str(candidate["page"]))
    if (not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
            or not isinstance(fingerprints, dict)
            or any(not isinstance(fingerprints.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprints[key])
                   for key in ("raw_sha256", "automatic_sha256"))):
        raise ValueError("源文件或页面检查点摘要不完整，无法确认页码推算；请先恢复有效任务。")
    return {"version": 1, "page": candidate["page"], "label": candidate["label"],
            "source_sha256": source_hash, "evidence": deepcopy(candidate["evidence"]),
            "target_fingerprints": deepcopy(fingerprints)}


def apply_repairs(document, records):
    """Expose matching confirmed records and preserve all unmatched conflicts.

    Mutates only the three derived repair/suggestion fields. Never inserts a
    guessed digit into OCR, body blocks, or ``page_info.page_numbers``.
    """
    candidates = suggest_document_page_number_repairs(document)
    document["page_number_suggestions"] = deepcopy(candidates)
    by_page = {item["page"]: item for item in candidates}
    repairs, conflicts = [], []
    if records is None:
        records = {}
    if not isinstance(records, dict):
        document["page_number_repairs"] = []
        document["page_number_repair_conflicts"] = [{"page": None, "reason": "页码推算记录格式不兼容，原记录保留。", "record": deepcopy(records)}]
        return document
    counts = Counter(record.get("page") for record in records.values()
                     if isinstance(record, dict) and _positive_int(record.get("page")))
    for key, record in records.items():
        page = record.get("page") if isinstance(record, dict) else None
        reason = None
        if (not isinstance(record, dict) or not _positive_int(page) or str(page) != key or counts[page] != 1
                or record.get("version") != 1 or record.get("provenance") != PROVENANCE
                or record.get("confidence") is not None):
            reason = "页码推算记录格式或人工确认身份不兼容，原记录保留。"
        elif page not in by_page:
            reason = "当前前后实际页码、页面状态或冲突已变化，原人工推算暂不应用，请重新核对。"
        else:
            try:
                binding = _binding(document, by_page[page])
                if any(record.get(k) != value for k, value in binding.items()) or record.get("signature") != _hash(binding):
                    reason = "源文件、前后页码证据或本页识别检查点已变化，原人工推算保留为冲突。"
            except (TypeError, ValueError) as exc:
                reason = str(exc)
        if reason:
            conflicts.append({"page": page, "reason": reason, "record": deepcopy(record)})
        else:
            repairs.append(deepcopy(record))
    document["page_number_repairs"] = sorted(repairs, key=lambda item: item["page"])
    document["page_number_repair_conflicts"] = conflicts
    confirmed_pages = {item["page"] for item in repairs}
    document["page_number_suggestions"] = [deepcopy(item) for item in candidates if item["page"] not in confirmed_pages]
    return document


def save_page_number_repair(job_dir, page, label):
    """Persist one explicitly chosen current suggestion under the job lock."""
    from .pipeline import build_document, load_job
    if not _positive_int(page) or not isinstance(label, str):
        raise ValueError("需要明确的原 PDF 页序和候选页码文字。")
    root = Path(job_dir)
    with job_lock(root):
        try:
            source_matches = digest(root / "source.pdf") == load_job(root)["source"]["sha256"]
        except OSError:
            source_matches = False
        if not source_matches:
            raise ValueError("任务中的原 PDF 已变化或丢失，不能确认页码推算；请重新导入并核对原文。")
        document = build_document(root)
        candidate = next((c for c in suggest_document_page_number_repairs(document)
                          if c["page"] == page and c["label"] == label), None)
        if candidate is None:
            raise ValueError("当前页没有与该值一致的可靠推算建议；原书可能故意留空，请先核对前后实际页码。")
        attach_repair_fingerprints(document, root)
        binding = _binding(document, candidate)
        record = {**binding, "signature": _hash(binding), "provenance": PROVENANCE,
                  "confidence": None, "confirmed_at": time.time(), "message": candidate["message"]}
        edits = read_json(root / "edits.json", {"version": 1, "blocks": {}, "history": []})
        repairs = edits.setdefault("page_number_repairs", {})
        if not isinstance(repairs, dict):
            raise ValueError("旧页码推算记录格式不兼容，已保留原文件，请先核对。")
        if str(page) in repairs:
            edits.setdefault("page_number_repair_history", []).append(
                {"page": page, "action": "replace", "record": deepcopy(repairs[str(page)]), "changed_at": record["confirmed_at"]})
        repairs[str(page)] = record
        atomic_json(root / "edits.json", edits)
        apply_repairs(document, repairs)
        atomic_json(root / "document.json", document)
        return deepcopy(record)


def clear_page_number_repair(job_dir, page):
    """Clear a confirmation, retaining history even when its evidence is stale."""
    from .pipeline import build_document
    if not _positive_int(page):
        raise ValueError("需要明确的原 PDF 页序。")
    root = Path(job_dir)
    with job_lock(root):
        document = build_document(root)
        edits = read_json(root / "edits.json", {"version": 1, "blocks": {}, "history": []})
        repairs = edits.get("page_number_repairs", {})
        if not isinstance(repairs, dict):
            raise ValueError("旧页码推算记录格式不兼容，已保留原文件，请先核对。")
        previous = repairs.pop(str(page), None)
        if previous is not None:
            edits.setdefault("page_number_repair_history", []).append(
                {"page": page, "action": "clear", "record": deepcopy(previous), "changed_at": time.time()})
            atomic_json(root / "edits.json", edits)
        attach_repair_fingerprints(document, root)
        apply_repairs(document, repairs)
        atomic_json(root / "document.json", document)
        return {"page": page, "cleared": previous is not None}
