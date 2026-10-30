"""Join only geometry-proven continuations across adjacent source pages.

The inputs and per-page recognition checkpoints remain untouched. This stage
does not align changed text, infer missing characters, delete hyphens, or cross
headings/figures/tables/notes. Callers protect pages touched by manual edits.
"""
from collections import Counter
from copy import deepcopy
import math
import re

from .inline_graphics import joined_text_metadata
from .page_margins import source_spans_for_block, text_digest
from .paragraphs import VERSION as PARAGRAPH_VERSION


VERSION = "cross-page-paragraphs-1"
_STOP = re.compile(r"[。！？.!?；;：:…“”‘’\"')）\]】]$")
_LIST = re.compile(r"^(?:[•●▪]\s*|[-*]\s+|\d{1,3}[、.)]\s*|[（(][一二三四五六七八九十\d]+[）)])")
_CJK = re.compile(r"[\u2e80-\uffef]")


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _line_ranges(block):
    """Require current, complete line/source anchors, not just a matching ID."""
    text, sources = block.get("text"), block.get("sources")
    spans = block.get("line_spans")
    if (not isinstance(text, str) or not text or not isinstance(sources, list) or not sources
            or not isinstance(spans, list) or len(spans) != len(sources)
            or block.get("line_spans_text_sha256") != text_digest(text)):
        return None
    end = 0
    for index, span in enumerate(spans):
        if not isinstance(span, dict):
            return None
        start, stop = span.get("start"), span.get("end")
        if (type(start) is not int or type(stop) is not int or not end <= start < stop <= len(text)
                or span.get("source_index") != index or (index == 0 and start != 0)):
            return None
        source = sources[index]
        if not isinstance(source, dict) or type(source.get("page")) is not int or source["page"] < 1:
            return None
        box = source.get("bbox")
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or box[2] <= box[0] or box[3] <= box[1]):
            return None
        end = stop
    return spans if end == len(text) else None


def _margin(block):
    # A merely suspicious footnote/title/image must still stop a join. Only
    # explicit margin paragraphs may be skipped, and they remain in the result.
    return block.get("kind") == "paragraph" and not block.get("edited") and any(
        isinstance(issue, dict) and issue.get("code") == "margin_content"
        and any(isinstance(source, dict) and source.get("bbox") is not None
                and issue.get("page") == source.get("page") and issue.get("bbox") == source["bbox"]
                for source in block.get("sources", []))
        for issue in block.get("issues", []))


def _continuation(first, second, page_map, protected):
    if any(block.get("kind") != "paragraph" or block.get("edited") or block.get("exclude_from_export")
           or not isinstance(block.get("id"), str) for block in (first, second)):
        return None
    if any(_line_ranges(block) is None for block in (first, second)):
        return None
    origins = {source["page"] for block in (first, second) for source in block["sources"]}
    if origins & protected:
        return None
    first_pages = {source["page"] for source in first["sources"]}
    second_pages = {source["page"] for source in second["sources"]}
    if len(second_pages) != 1 or (len(first_pages) > 1 and first.get("cross_page_recovery", {}).get("version") != VERSION):
        return None
    left, right = first["sources"][-1], second["sources"][0]
    p, n = left["page"], right["page"]
    if n != p + 1 or p not in page_map or n not in page_map:
        return None
    pages = [page_map[p], page_map[n]]
    if any(page.get("status") != "success" or page.get("layout", {}).get("columns") != 1
           or not _positive(page.get("height")) or not _positive(page.get("width")) for page in pages):
        return None
    metadata = [block.get("paragraph_recovery", {}) for block in (first, second)]
    if any(item.get("version") != PARAGRAPH_VERSION for item in metadata):
        return None
    a, b = metadata[0].get("last_line", {}), metadata[1].get("first_line", {})
    if (a.get("usable") is not True or b.get("usable") is not True
            or a.get("column") != 0 or b.get("column") != 0
            or a.get("near_right_margin") is not True or a.get("terminal_punctuation") is not False
            or b.get("aligned_to_body") is not True or b.get("starts_indented") is not False
            or _STOP.search(first["text"].rstrip()) or _LIST.match(second["text"].lstrip())):
        return None
    heights = [a.get("font_height"), b.get("font_height")]
    if (not all(_positive(h) for h in heights) or max(heights) / min(heights) > 1.2
            or left["bbox"][3] <= pages[0]["height"] * .68
            or right["bbox"][1] >= pages[1]["height"] * .25):
        return None
    spans = [source_spans_for_block(block) for block in (first, second)]
    if any(value is None for value in spans):
        return None
    return p, n, spans


def _join(first, second, evidence):
    p, n, (left, right) = evidence
    first_text, second_text = first["text"], second["text"]
    hyphen = bool(re.search(r"[A-Za-z]-$", first_text) and re.match(r"^[a-z]", second_text))
    separator = "" if hyphen or _CJK.search(first_text[-1:]) or _CJK.match(second_text[:1]) else " "
    result = deepcopy(first)
    result.update(joined_text_metadata(first, second, separator))
    result["text"] = first_text + separator + second_text
    result["sources"] = deepcopy(first["sources"] + second["sources"])
    result["source_block_ids"] = deepcopy(first.get("source_block_ids", [first["id"]]) + second.get("source_block_ids", [second["id"]]))
    result["confidence"] = None
    result.pop("_bbox", None)  # No single bbox can describe different physical pages.
    offset = len(first_text) + len(separator)
    left[-1]["end"] += len(separator)
    if separator:
        left[-1]["join_separator_after"] = separator
    for span in right:
        span["start"] += offset
        span["end"] += offset
    result["source_spans"] = left + right
    result["source_spans_text_sha256"] = text_digest(result["text"])
    result["paragraph_recovery"]["last_line"] = deepcopy(second["paragraph_recovery"]["last_line"])
    result["paragraph_recovery"]["joins"].append("source_page_continuation")
    history = result.setdefault("cross_page_recovery", {"version": VERSION, "joins": []})
    history["joins"].append({"from_page": p, "to_page": n, "separator": separator,
                             "reason": "adjacent_single_column_aligned_unfinished_continuation"})
    issue = {"code": "cross_page_join", "message": "相邻单栏原页的未完满行与下一页顶端非缩进正文，按一致字高及来源范围连接；原行与原页定位均保留。",
             "pages": [p, n], "blocks": [first["id"], second["id"]], "type": "processing", "severity": "info"}
    result["issues"] = deepcopy(first.get("issues", []) + second.get("issues", [])) + [issue]
    if hyphen:
        result["issues"].append({"code": "hyphenation_review", "message": "跨原页连字符按原文保留，请核对是否为断词。",
                                 "pages": [p, n], "type": "heuristic", "severity": "warning"})
    return result, issue


def join_cross_page_paragraphs(blocks, pages, protected_pages=frozenset()):
    """Return copied blocks and processing notes; uncertain boundaries stay put.

    A page must explicitly report layout.columns=1. Legacy caches without the
    new line evidence remain unchanged until the user rebuilds their layout.
    The caller supplies every page touched by an edit, exclusion or manual merge.
    """
    counts = Counter(page.get("number") for page in pages if isinstance(page, dict))
    page_map = {page["number"]: page for page in pages if isinstance(page, dict)
                and type(page.get("number")) is int and counts[page["number"]] == 1}
    result, issues, previous = [], [], None
    protected = set(protected_pages)
    for block in deepcopy(blocks):
        if _margin(block):
            result.append(block)
            continue
        evidence = _continuation(result[previous], block, page_map, protected) if previous is not None else None
        if evidence is not None:
            result[previous], issue = _join(result[previous], block, evidence)
            issues.append(issue)
        else:
            result.append(block)
            previous = len(result) - 1
    return result, issues
