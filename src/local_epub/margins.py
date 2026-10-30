"""Conservative, reversible annotations for repeated page decorations.

No text is removed or changed. ``decoration`` is the only field this module
writes. The caller decides whether annotated material should appear in an
export. Coordinates are PDF points with a top-left origin.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import math
import re
import statistics
import unicodedata

from .paragraphs import line_geometry


_MARGIN_FRACTION = 0.06
_MAX_TEXT_LENGTH = 40
_MAX_LINE_HEIGHT = 14.0
# These are relative page dimensions, not OCR text similarity thresholds.
_X_POSITION_TOLERANCE = 0.012
_Y_POSITION_TOLERANCE = 0.0035
_DIGITS = re.compile(r"(?:0|[1-9][0-9]{0,3})\Z")
_CHAPTER = re.compile(
    r"^(?:第[零〇一二三四五六七八九十百千万两0-9]+[章节篇部卷回]|"
    r"[一二三四五六七八九十]+[、．.]|(?:chapter|section|part)\s+[0-9ivxlcdm]+\b)",
    re.IGNORECASE,
)

_NOTE_MARKER = re.compile(
    r"^(?:[①-⑳❶-❿⁰¹²³⁴⁵⁶⁷⁸⁹※*†‡]|\[[0-9]{1,3}\]|"
    r"[（(][0-9]{1,3}[）)]|注(?:释)?\s*[：:0-9０-９]|"
    r"[0-9]{1,3}[.)、]\s*\S|[0-9]{1,3}\s+\D)"
)

# A promotional URL is evidence only together with repeated, isolated page-edge
# geometry. Never use domain blocklists or reinterpret ordinary in-body URLs.
_WEB_START = re.compile(r"(?:https?|htps?)[:/]+|www\.", re.IGNORECASE)
_WEB_ADDRESS = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,16}(?:[/?#][^\s<>]*)?\Z")
_PROMOTION = re.compile(r"^(?:seemore|formore|readmore|pleasevisit|visit|download|更多|请访问|请到|下载|在线阅读)")


@dataclass(frozen=True)
class _URLFooter:
    index: int
    page: int
    address: str
    prefix: str
    promotion: bool
    bbox: tuple[float, float, float, float]
    width: float
    height: float
    text_height: float
    body_height: float | None


@dataclass(frozen=True)
class _Candidate:
    index: int
    page: int
    side: str
    key: str
    bbox: tuple[float, float, float, float]
    width: float
    height: float
    number: int | None


def _positive_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value > 0 else None


def _page_number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _page_sizes(pages):
    sizes = {}
    duplicates = set()
    for page in pages:
        if not isinstance(page, dict):
            continue
        number = _page_number(page.get("number"))
        width, height = _positive_number(page.get("width")), _positive_number(page.get("height"))
        if number is None or width is None or height is None:
            continue
        if number in sizes:
            duplicates.add(number)
        sizes[number] = (width, height)
    # Ambiguous geometry is not evidence for hiding anything.
    return {number: size for number, size in sizes.items() if number not in duplicates}


def _source_boxes(block, sizes):
    sources = block.get("sources")
    if not isinstance(sources, list) or not sources:
        return None
    number = None
    boxes = []
    for source in sources:
        if not isinstance(source, dict):
            return None
        current = _page_number(source.get("page"))
        if current not in sizes or (number is not None and current != number):
            return None
        number = current
        box = source.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
            return None
        x0, y0, x1, y1 = map(float, box)
        width, height = sizes[number]
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            return None
        boxes.append((x0, y0, x1, y1))
    return number, boxes


def _body_line_heights(blocks, sizes):
    heights = defaultdict(list)
    for block in blocks:
        if not isinstance(block, dict) or block.get("kind") != "paragraph":
            continue
        geometry = _source_boxes(block, sizes)
        if geometry is None:
            continue
        number, boxes = geometry
        page_height = sizes[number][1]
        for x0, y0, x1, y1 in boxes:
            if page_height * .10 <= y0 < y1 <= page_height * .90:
                line_height = y1 - y0
                if 3 <= line_height <= min(36, page_height * .06):
                    heights[number].append(line_height)
    # A single body block is insufficient typography evidence.
    return {number: statistics.median(values) for number, values in heights.items() if len(values) >= 2}


def _normalize(text):
    # In the real fixture native extraction omits the middle dot, while OCR
    # preserves it. Do not remove other punctuation or use fuzzy OCR matching:
    # e.g. chapter 1.2 must not become chapter 12, and different letters stay so.
    value = unicodedata.normalize("NFKC", text)
    value = "".join(c for c in value if not c.isspace() and c not in "·•・")
    return value


def _candidate(index, block, sizes, body_heights):
    if not isinstance(block, dict) or block.get("kind") != "paragraph":
        return None
    if block.get("edited") or block.get("origin") == "pdf_bookmark":
        return None
    text = block.get("text")
    if not isinstance(text, str) or not text.strip() or len(text.strip().splitlines()) != 1:
        return None
    # A source-generated book heading is excluded even if its kind was changed
    # by a caller before this pass.
    if str(block.get("id", "")).startswith("bookmark-"):
        return None
    if _NOTE_MARKER.match(text.strip()):
        return None
    if _CHAPTER.match(unicodedata.normalize("NFKC", text).strip()):
        return None
    key = _normalize(text)
    if not 1 <= len(key) <= _MAX_TEXT_LENGTH or len(text.strip()) > 80:
        return None
    geometry = _source_boxes(block, sizes)
    if geometry is None:
        return None
    number, boxes = geometry
    if number not in body_heights:
        return None
    width, height = sizes[number]
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    line_height = y1 - y0
    if line_height > min(_MAX_LINE_HEIGHT, height * .026):
        return None
    if line_height > body_heights[number] * .88 + .5:
        return None
    # Multiple source fragments are allowed only when their baselines describe
    # one line; a merged paragraph cannot become a header.
    centers = [(b[1] + b[3]) / 2 for b in boxes]
    if max(centers) - min(centers) > max(1.5, line_height * .25):
        return None
    if y1 <= height * _MARGIN_FRACTION:
        side = "header"
    elif y0 >= height * (1 - _MARGIN_FRACTION):
        side = "footer"
    else:
        return None
    raw_number = unicodedata.normalize("NFKC", text).strip()
    strict_digits = re.fullmatch(r"[0-9０-９]{1,4}", text.strip())
    printed_number = int(raw_number) if strict_digits and _DIGITS.fullmatch(raw_number) else None
    # Numeric-looking text that does not meet the strict format is not handled
    # by the repeated-text rule as a fallback ("01", "1 2", "-1", "1.2").
    if printed_number is None:
        if not any(c.isalpha() for c in key) or len(key) < 2:
            return None
    return _Candidate(index, number, side, key, (x0, y0, x1, y1), width, height, printed_number)


def _same_position(a, b):
    if a.side != b.side:
        return False
    ay = (a.bbox[1] + a.bbox[3]) / (2 * a.height)
    by = (b.bbox[1] + b.bbox[3]) / (2 * b.height)
    if abs(ay - by) > _Y_POSITION_TOLERANCE:
        return False
    ah = (a.bbox[3] - a.bbox[1]) / a.height
    bh = (b.bbox[3] - b.bbox[1]) / b.height
    if max(ah, bh) > min(ah, bh) * 1.25:
        return False
    # Support left-, centre-, or right-aligned running matter. OCR may include
    # a punctuation glyph omitted by native extraction, changing its width.
    anchors_a = (a.bbox[0] / a.width, (a.bbox[0] + a.bbox[2]) / (2 * a.width), a.bbox[2] / a.width)
    anchors_b = (b.bbox[0] / b.width, (b.bbox[0] + b.bbox[2]) / (2 * b.width), b.bbox[2] / b.width)
    return any(abs(x - y) <= _X_POSITION_TOLERANCE for x, y in zip(anchors_a, anchors_b))


def _position_groups(candidates):
    groups = []
    for candidate in sorted(candidates, key=lambda c: (c.page, c.index)):
        # Compare with every member to avoid a chain of gradually drifting
        # body text being mistaken for a fixed page decoration.
        matching = next((g for g in groups if all(_same_position(candidate, old) for old in g)), None)
        if matching is None:
            groups.append([candidate])
        else:
            matching.append(candidate)
    return groups


def _footer_body_heights(blocks, sizes):
    """Use real perpendicular OCR height when available, without editing sources."""
    values = defaultdict(dict)
    for block in blocks:
        if not isinstance(block, dict) or block.get("kind") != "paragraph":
            continue
        source = _source_boxes(block, sizes)
        if source is None:
            continue
        number, boxes = source
        height = sizes[number][1]
        for item, box in zip(block["sources"], boxes):
            if not height * .10 <= box[1] < box[3] <= height * .90:
                continue
            geometry = line_geometry(item)
            if geometry and 3 <= geometry["height"] <= min(36, height * .06):
                values[number][box] = geometry["height"]
    return {number: statistics.median(lines.values())
            for number, lines in values.items() if len(lines) >= 2}


def _footer_url(text):
    value = "".join(unicodedata.normalize("NFKC", text).split())
    match = _WEB_START.search(value)
    if match is None:
        return None
    prefix = value[:match.start()].casefold().rstrip(":;,.-!：，。；！")
    # An ordinary URL, citation, or sentence mentioning a URL is insufficient.
    # A minor OCR error in a short prompt may later match a repeated template;
    # it cannot establish that template by itself.
    if not 6 <= len(prefix) <= 50:
        return None
    address = value[match.end():].lstrip(":/").rstrip(".,;。；，")
    if match.group().casefold().startswith("www."):
        address = "www." + address
    # Host names are case-insensitive; paths and queries need not be. A URL
    # differing only in its article path must not establish the same template.
    host = re.match(r"[^/?#]+", address)
    if host:
        address = host.group().casefold() + address[host.end():]
    if not _WEB_ADDRESS.fullmatch(address):
        return None
    return address.removeprefix("www."), prefix, bool(_PROMOTION.match(prefix))


def _url_footer_candidate(index, block, sizes, body_heights, text_boxes):
    if not isinstance(block, dict) or block.get("kind") not in ("paragraph", "footnote", "heading"):
        return None
    if (block.get("edited") or block.get("origin") == "pdf_bookmark"
            or str(block.get("id", "")).startswith("bookmark-")):
        return None
    text = block.get("text")
    if (not isinstance(text, str) or not 10 <= len(text.strip()) <= 180
            or len(text.strip().splitlines()) != 1
            or _NOTE_MARKER.match(text.strip()) or _CHAPTER.match(text.strip())):
        return None
    identity = _footer_url(text)
    source = _source_boxes(block, sizes)
    # Never omit a merged body paragraph merely because its last line is an ad.
    if identity is None or source is None or len(source[1]) != 1:
        return None
    number, boxes = source
    box = boxes[0]
    width, height = sizes[number]
    x0, y0, x1, y1 = box
    if y0 < height * .94 or (y0 + y1) / 2 < height * .965:
        return None
    item = block["sources"][0]
    geometry = line_geometry(item)
    if geometry is None:
        return None
    has_polygon = geometry["has_polygon"]
    if item.get("polygon") is not None and not has_polygon:
        return None
    text_height = geometry["height"]
    if has_polygon:
        # A bounding box is not an angle estimate. Only genuine OCR polygon
        # data may justify a long line whose axis-aligned box is unusually tall.
        if (abs(geometry["baseline_angle_degrees"]) > 8
                or not 3 <= text_height <= min(18, height * .03)
                or any(not x0 - 1 <= p[0] <= x1 + 1 or not y0 - 1 <= p[1] <= y1 + 1
                       for p in item["polygon"])):
            return None
    elif not 3 <= text_height <= min(24, height * .035):
        return None
    body_height = body_heights.get(number)
    if body_height is not None and text_height > body_height * .90 + .5:
        # Old OCR caches contain only inflated axis-aligned boxes. A long
        # sloping footer can look taller than short body lines. Such a page
        # may match an already-established template, but cannot establish it.
        body_height = None
    other_boxes = [other for other_index, other in text_boxes[number] if other_index != index]
    if any(other[1] < y1 and other[3] > y0 for other in other_boxes):
        return None
    above = [other[3] for other in other_boxes if other[3] <= y0]
    if above and y0 - max(above) < max(height * .025, text_height * 1.5):
        return None
    address, prefix, promotion = identity
    return _URLFooter(index, number, address, prefix, promotion, box, width, height,
                      text_height, body_height)


def _same_url_footer(a, b):
    if a.address != b.address:
        return False
    # Fuzzy matching is confined to a short repeated promotional prompt. The
    # address (host AND path/query) must remain identical after scheme/spacing
    # normalization; similar but different body URLs never form one template.
    if a.prefix != b.prefix and (min(len(a.prefix), len(b.prefix)) < 12
                               or SequenceMatcher(None, a.prefix, b.prefix).ratio() < .90):
        return False
    ay = (a.bbox[1] + a.bbox[3]) / (2 * a.height)
    by = (b.bbox[1] + b.bbox[3]) / (2 * b.height)
    if abs(ay - by) > .012:
        return False
    anchors_a = (a.bbox[0] / a.width, (a.bbox[0] + a.bbox[2]) / (2 * a.width), a.bbox[2] / a.width)
    anchors_b = (b.bbox[0] / b.width, (b.bbox[0] + b.bbox[2]) / (2 * b.width), b.bbox[2] / b.width)
    if not any(abs(x - y) <= .035 for x, y in zip(anchors_a, anchors_b)):
        return False
    ah, bh = a.text_height / a.height, b.text_height / b.height
    return max(ah, bh) <= min(ah, bh) * 1.4


def _annotate_url_footers(blocks, sizes):
    body_heights = _footer_body_heights(blocks, sizes)
    text_boxes = defaultdict(list)
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("kind") not in ("paragraph", "heading", "footnote"):
            continue
        source = _source_boxes(block, sizes)
        if source:
            text_boxes[source[0]].extend((index, box) for box in source[1])
    groups = []
    for index, block in enumerate(blocks):
        candidate = _url_footer_candidate(index, block, sizes, body_heights, text_boxes)
        if candidate is None:
            continue
        group = next((g for g in groups if all(_same_url_footer(candidate, other) for other in g)), None)
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)
    for group in groups:
        evidence = sorted({candidate.page for candidate in group})
        anchors = [candidate for candidate in group if candidate.promotion and candidate.body_height is not None]
        # Three independently measured text pages establish the template. A
        # matching sparse/blank end page may inherit this geometric evidence,
        # but cannot establish it or cause a short document to lose content.
        if len(evidence) != len(group) or len({candidate.page for candidate in anchors}) < 3:
            continue
        reference_height = statistics.median(candidate.text_height / candidate.height for candidate in anchors)
        accepted = [candidate for candidate in group
                    if candidate.body_height is not None or candidate.text_height / candidate.height <= reference_height * 1.15]
        evidence = sorted({candidate.page for candidate in accepted})
        for candidate in accepted:
            blocks[candidate.index]["decoration"] = {
                "kind": "footer", "rule": "repeated_url_footer", "evidence_pages": evidence,
                "reason": (f"至少 3 页以正文行高为参照，在独立底边小字区域重复出现相同网址和短提示语；"
                           f"本组共 {len(evidence)} 页，允许空白、大小写及常见协议识别差异，按重复页脚标记。"
                           "原始识别文本与来源保持不变。"),
            }


def annotate_margins(blocks, pages) -> None:
    """Annotate repeated page-edge matter, leaving original content untouched.

    Each input block receives ``decoration=None`` or a dictionary containing
    ``kind``, a Chinese explanation, and the actual ``evidence_pages``. Repeated
    calls recompute annotations. Missing geometry, edited blocks, images,
    tables, explicit note markers and cross-page blocks are never candidates.

    This is deliberately conservative: at least two distinct pages must provide
    a short single line in the outer 6%, with smaller geometry than body text,
    matching position, and identical normalized text. A bare Arabic page number
    also requires one fixed offset from the PDF page index. The function neither
    deletes text nor infers absent page numbers. A separate, stricter rule
    recognizes isolated promotional URL footers across at least three measured
    pages, including old OCR output whose heuristic kind was footnote/heading.
    """
    for block in blocks:
        if isinstance(block, dict):
            block["decoration"] = None
    sizes = _page_sizes(pages)
    body_heights = _body_line_heights(blocks, sizes)
    candidates = [candidate for index, block in enumerate(blocks)
                  if (candidate := _candidate(index, block, sizes, body_heights)) is not None]
    repeated = defaultdict(list)
    for candidate in candidates:
        if candidate.number is None:
            repeated[(candidate.side, "text", candidate.key)].append(candidate)
        else:
            repeated[(candidate.side, "number", candidate.number - candidate.page)].append(candidate)
    for key, compatible in repeated.items():
        for group in _position_groups(compatible):
            evidence_pages = sorted({candidate.page for candidate in group})
            # Multiple competing entries at the same position on one page are
            # ambiguous; no member of that group is automatically annotated.
            if len(evidence_pages) < 2 or len(evidence_pages) != len(group):
                continue
            side = group[0].side
            edge = "顶部" if side == "header" else "底部"
            if key[1] == "number":
                offset = key[2]
                relation = "与 PDF 页码相同" if offset == 0 else f"等于 PDF 页码 {'+' if offset > 0 else '-'} {abs(offset)}"
                kind = "page_number"
                reason = (f"在 {len(evidence_pages)} 页的{edge} 6% 边缘同位置出现短数字，均{relation}；"
                          "数字区域比正文行小，按连续编号规则标记为页码。")
            else:
                kind = side
                label = "页眉" if side == "header" else "页脚"
                reason = (f"在 {len(evidence_pages)} 页的{edge} 6% 边缘同位置重复，文字区域比正文行小；"
                          f"仅规范空白、中点和全角符号后文本相同，按重复边缘规则标记为{label}。")
            for candidate in group:
                blocks[candidate.index]["decoration"] = {
                    "kind": kind, "reason": reason, "evidence_pages": list(evidence_pages)}
    _annotate_url_footers(blocks, sizes)
