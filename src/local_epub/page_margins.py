"""Source-bound page-edge data for the validated external reading profile.

The EPUB carries data, not unvalidated running-margin CSS. Actual page-edge
layout is supplied by the local official Vivliostyle reader. Character offsets
below are Python Unicode indexes into block.text, before XML/HTML escaping.
"""
from collections import Counter
from copy import deepcopy
import hashlib

from .page_info import build_page_info

MARGIN_PROFILE = "vivliostyle-2.45.2"


def require_margin_support(retain_page_margins=False):
    """Compatibility hook: retention now has a validated external profile."""
    if not isinstance(retain_page_margins, bool):
        raise ValueError("页边保留选项必须为布尔值")


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_spans_for_block(block):
    """Return validated exact text ranges, or None when mapping is ambiguous.

    One-page edits remain attributable to that page. Multi-page text needs a
    complete, content-bound range map; matching only the source page set is not
    enough. A zero-length image/table caption still has a source-page event.
    """
    text = block.get("text", "")
    sources = block.get("sources", [])
    if not isinstance(text, str) or not isinstance(sources, list) or not sources:
        return None
    if any(not isinstance(s, dict) or not isinstance(s.get("page"), int)
           or isinstance(s["page"], bool) or s["page"] <= 0 for s in sources):
        return None
    pages = {s["page"] for s in sources}
    if len(pages) == 1:
        return [{"start": 0, "end": len(text), "page": next(iter(pages)),
                 "sources": deepcopy(sources),
                 "source_block_ids": deepcopy(block.get("source_block_ids", [block.get("id")])),
                 "provenance": "human_correction" if block.get("edited") else "source_text"}]
    spans = block.get("source_spans")
    if (not isinstance(spans, list) or not spans
            or block.get("source_spans_text_sha256") != text_digest(text)):
        return None
    end, mapped_pages = 0, set()
    for span in spans:
        if not isinstance(span, dict):
            return None
        start, stop, page = span.get("start"), span.get("end"), span.get("page")
        if (any(not isinstance(v, int) or isinstance(v, bool) for v in (start, stop, page))
                or start != end or stop <= start or stop > len(text) or page not in pages):
            return None
        origins = span.get("sources")
        if (not isinstance(origins, list) or not origins
                or any(not isinstance(s, dict) or s.get("page") != page or s not in sources for s in origins)):
            return None
        mapped_pages.add(page)
        end = stop
    if end != len(text) or mapped_pages != pages:
        return None
    return deepcopy(spans)


def _issue(code, message, **location):
    return {"code": code, "message": message, "type": "heuristic", "severity": "warning", **location}


def _ordered(entries):
    def position(entry):
        boxes = [s["bbox"] for s in entry.get("sources", []) if s.get("bbox") is not None]
        return min((box[1], box[0]) for box in boxes) if boxes else (float("inf"), float("inf"))
    return sorted(deepcopy(entries), key=position)


def build_margin_plan(document):
    """Build a pure page/range plan without inferring or modifying source text.

    Every body block has an event at offset zero. page=None explicitly clears
    the reader's running state, so an uncertain block never inherits a previous
    header. Valid source-page events resume at the next reliable block.
    """
    from .page_number_repairs import apply_repairs
    issues, pages = [], []
    # Revalidate confirmations rather than trusting caller-supplied labels.
    validation = deepcopy(document)
    records = document.get("page_number_repairs", [])
    records = records if isinstance(records, list) else []
    counts = Counter(r.get("page") for r in records if isinstance(r, dict))
    record_map = {str(r["page"]): r for r in records
                  if isinstance(r, dict) and isinstance(r.get("page"), int) and counts[r["page"]] == 1}
    apply_repairs(validation, record_map)
    repairs = {r["page"]: r for r in validation["page_number_repairs"]}
    conflicts = list(document.get("page_number_repair_conflicts", [])) + validation["page_number_repair_conflicts"]
    for conflict in conflicts:
        issues.append(_issue("margin_page_number_repair_conflict", "人工推算页码的来源校验未通过，未放入页脚；原记录保留。",
                             page=conflict.get("page")))
    for number, count in counts.items():
        if count > 1:
            issues.append(_issue("margin_page_number_ambiguous", "同页有多条人工页码记录，未选择其中任何一条。", page=number))
    page_counts = Counter(p.get("number") for p in document.get("pages", []) if isinstance(p, dict))
    methods = {p.get("number"): p.get("method") for p in document.get("pages", []) if isinstance(p, dict)}
    for info in build_page_info(document):
        number = info["number"]
        page = deepcopy(info)
        if page_counts[number] > 1:
            page["status"] = "invalid"
        page["headers"], page["footers"] = _ordered(page["headers"]), _ordered(page["footers"])
        page["page_number"] = None
        if page["status"] == "success":
            observed = page["page_numbers"]
            if len(observed) == 1:
                item = deepcopy(observed[0])
                item["label"] = item["text"]
                item["provenance"] = "human_correction" if item.get("edited") else "recognized"
                item["recognition_method"] = methods.get(number)
                page["page_number"] = item
            elif len(observed) > 1:
                issues.append(_issue("margin_page_number_ambiguous", "原页有多处页码候选，未选择单一页码放入页脚。", page=number))
            elif number in repairs and counts[number] == 1:
                page["page_number"] = deepcopy(repairs[number])
            if page["unclassified"]:
                issues.append(_issue("margin_content_unclassified", "未确定的页边文字保持原有正文位置，未自动放入页眉页脚。", page=number))
        else:
            page["headers"], page["footers"], page["page_numbers"] = [], [], []
        page["header_text"] = "\n".join(e.get("text", "") for e in page["headers"])
        page["footer_body_text"] = "\n".join(e.get("text", "") for e in page["footers"])
        page["page_number_text"] = page["page_number"]["label"] if page["page_number"] else ""
        page["footer_text"] = "\n".join(t for t in (page["footer_body_text"], page["page_number_text"]) if t)
        pages.append(page)
    valid_pages = {p["number"] for p in pages if p["status"] == "success"}
    roles = {entry["id"] for p in pages for key in ("headers", "footers", "page_numbers") for entry in p[key]}
    blocks = {}
    for block in document.get("blocks", []):
        if block.get("id") in roles or block.get("exclude_from_export"):
            continue
        spans = source_spans_for_block(block)
        if spans is None or any(s["page"] not in valid_pages for s in spans):
            blocks[block["id"]] = {"reliable": False, "spans": [], "events": [{"offset": 0, "page": None}]}
            issues.append(_issue("margin_source_unresolved", "本块文字无法准确对应来源页，阅读此块时清空页眉页脚；下一个可靠来源块会恢复。",
                                 block_id=block["id"], pages=sorted({s["page"] for s in block.get("sources", []) if isinstance(s.get("page"), int)})))
            continue
        events = [{"offset": s["start"], "page": s["page"]} for s in spans]
        blocks[block["id"]] = {"reliable": True, "spans": spans, "events": events}
    unique_issues = []
    for issue in issues:
        if issue not in unique_issues:
            unique_issues.append(issue)
    return {"schema_version": 1, "profile": MARGIN_PROFILE,
            "source_sha256": document.get("source_sha256"), "pages": pages, "blocks": blocks, "issues": unique_issues}


def annotate_page_margins(document):
    """Classify without deleting or setting source-block visibility."""
    roles = {}
    for page in build_page_info(document):
        for key, role in (("headers", "header"), ("footers", "footer"), ("page_numbers", "page_number")):
            roles.update({entry["id"]: role for entry in page[key]})
    for block in document.get("blocks", []):
        block["page_margin_role"] = roles.get(block["id"])
    return document


def export_document(document, retain_page_margins=False):
    require_margin_support(retain_page_margins)
    result = annotate_page_margins(deepcopy(document))
    for block in result.get("blocks", []):
        if block.get("page_margin_role"):
            block["exclude_from_export"] = True
            block["omission_reason"] = "page_margin_layout" if retain_page_margins else "page_margin_option"
        elif block.get("export_override") not in ("exclude", "keep"):
            # Older automatic decoration flags are not body editing decisions.
            block["exclude_from_export"] = False
    result.pop("margin_plan", None)
    if retain_page_margins:
        result["margin_plan"] = build_margin_plan(result)
        for issue in result["margin_plan"]["issues"]:
            if issue not in result.setdefault("issues", []):
                result["issues"].append(deepcopy(issue))
    return result
