"""Source-page running matter, separate from a reader's dynamic pagination.

All classification happens on copies. Human-corrected text is retained, while
classification from the automatic draft can be reused only for the same block
identity and exactly the same source geometry. No missing page number is inferred.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math

from .margins import annotate_margins


_CATEGORIES = {"header": "headers", "footer": "footers", "page_number": "page_numbers"}


def _number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _source_page(block, known_pages):
    sources = block.get("sources")
    if not isinstance(sources, list) or not sources:
        return None
    number = None
    for source in sources:
        if not isinstance(source, dict):
            return None
        current = _number(source.get("page"))
        if current not in known_pages or (number is not None and current != number):
            return None
        number = current
        bbox = source.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox):
            return None
        if not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
            return None
    return number


def _margin_issue(block):
    """A stale issue at a different box is not evidence for the current block."""
    if block.get("kind") != "paragraph":
        return None
    for issue in block.get("issues", []):
        if not isinstance(issue, dict) or issue.get("code") != "margin_content":
            continue
        if any(issue.get("page") == source.get("page")
               and issue.get("bbox") is not None
               and issue.get("bbox") == source.get("bbox")
               for source in block.get("sources", [])):
            return issue
    return None


def _entry(block, decoration, origin, issue=None, original=None):
    value = {"id": block["id"], "text": block.get("text", ""),
             "sources": deepcopy(block["sources"]), "kind": block.get("kind"),
             "classification_source": origin, "edited": bool(block.get("edited")),
             "confidence": None if block.get("edited") else deepcopy(block.get("confidence")),
             "export_override": block.get("export_override", "auto"),
             "exclude_from_export": bool(block.get("exclude_from_export"))}
    if decoration:
        value.update(classification=decoration["kind"],
                     reason=decoration.get("reason", ""),
                     evidence_pages=deepcopy(decoration.get("evidence_pages", [])))
    else:
        value.update(classification=None,
                     reason="原解析记录了页边疑点，但证据不足以确定是页眉、页脚还是页码。",
                     evidence_pages=sorted({source["page"] for source in block["sources"]}))
        if issue:
            value["source_issue"] = deepcopy(issue)
    if original is not None and original.get("text") != block.get("text"):
        value["original_text"] = original.get("text", "")
    return value


def build_page_info(document):
    """Return source-page info without changing the document or guessing gaps.

    Each result has ``number``, ``status``, and lists ``headers``, ``footers``,
    ``page_numbers``, ``unclassified``. Source page numbers are physical PDF
    indexes. ``page_numbers`` contains only recognized (possibly human-corrected)
    printed labels; an empty list must not be replaced by the physical PDF index.
    Missing-page statuses and errors are retained, with empty content lists.
    """
    pages = [deepcopy(page) for page in document.get("pages", []) if isinstance(page, dict)]
    output = {}
    duplicates = set()
    for page in pages:
        number = _number(page.get("number"))
        if number is None:
            continue
        if number in output:
            duplicates.add(number)
        output[number] = {"number": number, "status": page.get("status", "success"),
                          "headers": [], "footers": [], "page_numbers": [], "unclassified": []}
        if page.get("error"):
            output[number]["error"] = deepcopy(page["error"])
    for missing in document.get("missing_pages", []):
        if not isinstance(missing, dict):
            continue
        number = _number(missing.get("number"))
        if number is None:
            continue
        output[number] = {"number": number, "status": missing.get("status", "pending"),
                          "headers": [], "footers": [], "page_numbers": [], "unclassified": []}
        if missing.get("error") is not None:
            output[number]["error"] = deepcopy(missing["error"])
    valid_numbers = {number for number, page in output.items()
                     if page["status"] == "success" and number not in duplicates}
    valid_pages = [page for page in pages if page.get("number") in valid_numbers]
    original_blocks = [deepcopy(block) for page in valid_pages for block in page.get("blocks", [])
                       if isinstance(block, dict)]
    effective_blocks = [deepcopy(block) for block in document.get("blocks", []) if isinstance(block, dict)]
    annotate_margins(original_blocks, valid_pages)
    annotate_margins(effective_blocks, valid_pages)
    original_ids = Counter(block.get("id") for block in original_blocks if isinstance(block.get("id"), str))
    originals = {block["id"]: block for block in original_blocks
                 if isinstance(block.get("id"), str) and original_ids[block["id"]] == 1}
    effective_ids = Counter(block.get("id") for block in effective_blocks if isinstance(block.get("id"), str))
    for block in effective_blocks:
        block_id = block.get("id")
        if not isinstance(block_id, str) or effective_ids[block_id] != 1:
            continue
        number = _source_page(block, valid_numbers)
        if number is None:
            continue
        original = originals.get(block_id)
        if original is not None and original.get("sources") != block.get("sources"):
            original = None
        decoration = block.get("decoration")
        origin = "effective"
        if not decoration and original is not None:
            decoration = original.get("decoration")
            origin = "source"
        if decoration and decoration.get("kind") in _CATEGORIES:
            output[number][_CATEGORIES[decoration["kind"]]].append(
                _entry(block, decoration, origin, original=original))
            continue
        issue = _margin_issue(block)
        if issue is None and original is not None:
            issue = _margin_issue(original)
        if issue:
            output[number]["unclassified"].append(
                _entry(block, None, "margin_candidate", issue=issue, original=original))
    return [output[number] for number in sorted(output)]
