"""Conservative line-to-paragraph recovery in source-page coordinates.

No image, OCR text, punctuation, confidence or input line is rewritten here.
The caller supplies reading order and intervening figure/table/formula regions.
Polygon geometry is used only to measure layout; original coordinates remain
attached to every line. This module deliberately does not join across pages.
"""
from copy import deepcopy
import hashlib
import math
import re
import statistics


VERSION = "paragraphs-1"
_CHAPTER = re.compile(r"^第[一二三四五六七八九十百千万零〇\d]+[章节篇部卷]\s*")
_NOTE = re.compile(r"^(?:[①②③④⑤⑥⑦⑧⑨⑩]|\[\d+\]\s|注[：:])")
_LIST = re.compile(r"^(?:[•●▪]\s*|[-*]\s+|\d{1,3}[、.)]\s*|[（(][一二三四五六七八九十\d]+[）)])")
_STOP = re.compile(r"[。！？!?；;：:][”’\"')）]*$")
_CJK = re.compile(r"[\u2e80-\uffef]")


def _points(value):
    try:
        result = [[float(p[0]), float(p[1])] for p in value]
        return result if len(result) == 4 and all(math.isfinite(c) for p in result for c in p) else None
    except (TypeError, ValueError, IndexError):
        return None


def line_geometry(line, rotation_degrees=0.0):
    """Measure a raw line or source; missing/invalid geometry returns None.

    `height` is perpendicular to the OCR polygon baseline, rather than the
    inflated axis-aligned bbox height of a sloping, long text line. Without a
    polygon it uses a valid native font size or bbox height and says so.
    `rectified_bbox` rotates coordinates only, never the original source image.
    """
    polygon = _points(line.get("polygon"))
    try:
        x0, y0, x1, y1 = map(float, line["bbox"])
        if not all(map(math.isfinite, (x0, y0, x1, y1))) or x1 <= x0 or y1 <= y0:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    if polygon:
        sides = sorted(polygon, key=lambda p: p[0])
        left = [sum(p[n] for p in sides[:2]) / 2 for n in (0, 1)]
        right = [sum(p[n] for p in sides[2:]) / 2 for n in (0, 1)]
        dx, dy = right[0] - left[0], right[1] - left[1]
        length = math.hypot(dx, dy)
        if length <= 0:
            return None
        heights = [abs((side[1][0] - side[0][0]) * dy - (side[1][1] - side[0][1]) * dx) / length
                   for side in (sides[:2], sides[2:])]
        text_height = statistics.mean(heights)
        if text_height <= 0:
            return None
        angle = math.degrees(math.atan2(dy, dx))
    else:
        polygon = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        left, right = [x0, (y0 + y1) / 2], [x1, (y0 + y1) / 2]
        text_height = line.get("text_height", line.get("size", y1 - y0))
        if not isinstance(text_height, (int, float)) or not math.isfinite(text_height) or text_height <= 0:
            text_height = y1 - y0
        angle = 0.0
    radians = math.radians(rotation_degrees)
    co, si = math.cos(radians), math.sin(radians)
    rotated = [(x * co + y * si, -x * si + y * co) for x, y in polygon]
    return {"height": float(text_height), "baseline_angle_degrees": angle,
            "baseline": [left, right], "has_polygon": bool(_points(line.get("polygon"))),
            "rectified_bbox": [min(p[0] for p in rotated), min(p[1] for p in rotated),
                               max(p[0] for p in rotated), max(p[1] for p in rotated)]}


def _quantile(values, fraction):
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def _issue(code, message, page, box):
    return {"code": code, "message": message, "page": page, "bbox": deepcopy(box),
            "type": "heuristic", "severity": "warning"}


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _separator(first, second):
    if re.search(r"[A-Za-z]-$", first) and re.match(r"^[a-z]", second):
        return ""
    return "" if _CJK.search(first[-1:]) or _CJK.match(second[:1]) else " "


def _column(item, width, dual):
    box = item["box"]
    if not dual:
        return 0
    if box[0] < width * .46 and box[2] > width * .54:
        return "spanning"
    return 0 if (box[0] + box[2]) / 2 < width / 2 else 1


def _profile(items):
    body = [i for i in items if i["kind"] == "paragraph" and not i["margin"] and i["geometry"]]
    if not body:
        return None
    height = statistics.median(i["geometry"]["height"] for i in body)
    main = [i for i in body if abs(i["geometry"]["height"] - height) <= height * .25]
    if not main:
        return None
    advances = []
    for item in main:
        # Text width supplies only a layout scale, never recognition accuracy.
        units = sum(1.0 if _CJK.match(c) else .55 for c in item["text"] if not c.isspace())
        if units >= 6:
            advances.append((item["box"][2] - item["box"][0]) / units)
    advance = statistics.median(advances) if advances else height
    advance = min(height * 1.8, max(height * .65, advance))
    left = _quantile([i["box"][0] for i in main], .15)
    right = _quantile([i["box"][2] for i in main], .85)
    gaps = []
    for first, second in zip(main, main[1:]):
        gap = second["center"] - first["center"]
        if height * .8 <= gap <= height * 3.0:
            gaps.append(gap)
    spacing = _quantile(gaps, .4) if gaps else height * 1.6
    return {"height": height, "advance": advance, "left": left, "right": right,
            "spacing": spacing, "count": len(main), "measured_spacing": len(gaps) >= 2}


def _blocked(first, second, barriers):
    left = max(first["box"][0], second["box"][0])
    right = min(first["box"][2], second["box"][2])
    return any(b[0] < right and b[2] > left and b[1] < second["center"] and b[3] > first["center"]
               for b in barriers)


def _decision(first, second, profile, barriers):
    """Return (join, reason, needs_review), with hard barriers checked first."""
    if first["kind"] != "paragraph" or second["kind"] != "paragraph":
        return False, "non_body_boundary", False
    if first["margin"] or second["margin"]:
        return False, "page_margin", False
    if not first["geometry"] or not second["geometry"] or not profile:
        return False, "missing_geometry", True
    if first["column"] != second["column"] or second["center"] <= first["center"]:
        return False, "reading_order_boundary", False
    if _blocked(first, second, barriers):
        return False, "intervening_region", False
    if _LIST.match(second["text"]):
        return False, "list_start", False
    if first["line"].get("method") != second["line"].get("method"):
        return False, "mixed_extraction_boundary", True
    h, char, spacing = profile["height"], profile["advance"], profile["spacing"]
    a, b = first["geometry"], second["geometry"]
    if abs(a["height"] - b["height"]) > max(1.5, h * .24):
        return False, "font_size_change", True
    if abs(a["baseline_angle_degrees"] - b["baseline_angle_degrees"]) > 3.0:
        return False, "baseline_direction_change", True
    step = second["center"] - first["center"]
    if step < max(h * .72, spacing * .63):
        return False, "overlapping_or_fragmented_lines", True
    if step > max(spacing * 1.43, h * 2.15):
        return False, "paragraph_spacing", False
    indent = second["box"][0] - profile["left"]
    if 1.2 * char <= indent <= 4.0 * char:
        return False, "first_line_indent", False
    if abs(indent) > char * .8:
        return False, "uncertain_alignment", True
    old_indent = first["box"][0] - profile["left"]
    if old_indent < -char or old_indent > char * 4.2:
        return False, "uncertain_alignment", True
    width = max(char, profile["right"] - profile["left"])
    remaining = profile["right"] - first["box"][2]
    short = remaining > max(char * 1.7, width * .08)
    stop = bool(_STOP.search(first["text"]))
    if short:
        # Native Latin text wraps at word boundaries, so a valid wrapped line
        # can end several character widths before the right edge. A tight,
        # exactly measured native baseline plus a lowercase continuation is
        # useful even when the page has only one pair from which to measure
        # line spacing. Do not extend this exception to noisy OCR geometry or
        # to a completed sentence / a new capitalized paragraph.
        native_word_wrap = (first["line"].get("method") == "text"
                            and not _CJK.search(first["text"] + second["text"])
                            and bool(re.search(r"[A-Za-z]$", first["text"]))
                            and bool(re.match(r"^[a-z]", second["text"]))
                            and profile["count"] >= 2 and step <= h * 1.35)
        if (not stop and remaining <= max(char * 3.8, width * .12)
                and (profile["measured_spacing"] or native_word_wrap) and step <= spacing * 1.2):
            return True, "native_word_wrap" if native_word_wrap else "slightly_short_continuation", True
        return False, "short_terminal_line" if stop else "short_unfinished_line", not stop
    if profile["count"] < 3 and stop and old_indent < char * 1.2:
        return False, "insufficient_layout_evidence", True
    # A full-width line ending in a full stop is routinely mid-paragraph.
    # Indentation, spacing, font and flow are the evidence; punctuation alone
    # must not turn every sentence into a separate paragraph.
    return True, "consistent_wrapped_line", False


def _edge_evidence(item, profile):
    if not item["geometry"] or not profile or item["margin"] or item["kind"] != "paragraph":
        return {"usable": False}
    char = profile["advance"]
    indent = item["box"][0] - profile["left"]
    remaining = profile["right"] - item["box"][2]
    return {"usable": profile["count"] >= 3 and profile["measured_spacing"],
            "column": item["column"], "font_height": item["geometry"]["height"],
            "body_left": profile["left"], "body_right": profile["right"],
            "character_advance": char, "line_spacing": profile["spacing"],
            "starts_indented": char * 1.2 <= indent <= char * 4,
            "aligned_to_body": abs(indent) <= char * .8,
            "near_right_margin": remaining <= max(char * 1.7, (profile["right"] - profile["left"]) * .08),
            "terminal_punctuation": bool(_STOP.search(item["text"]))}


def recover_paragraphs(ordered_lines, width, height, page_number, toc_titles=(), *, barriers=(), dual=False):
    """Return backend-compatible blocks from already ordered source lines.

    `barriers` contains figure/table/formula bboxes, excluding a scan's backing
    full-page image. The input is untouched. Joined blocks retain per-line
    Unicode offsets in `line_spans`, original `sources` (including polygons),
    and heuristic grouping metadata separately from text and OCR confidence.
    """
    measurements = [(index, line, line_geometry(line)) for index, line in enumerate(ordered_lines) if str(line.get("text", "")).strip()]
    angles = [g["baseline_angle_degrees"] for _, line, g in measurements if g and g["has_polygon"]
              and len(str(line["text"]).strip()) >= 6 and abs(g["baseline_angle_degrees"]) <= 12]
    rotation = statistics.median(angles) if len(angles) >= 2 else 0.0
    heights = [g["height"] for _, _, g in measurements if g]
    base = statistics.median(heights) if heights else 12.0
    items = []
    for index, line, measured in measurements:
        text = str(line["text"]).strip()
        geometry = line_geometry(line, rotation)
        original = deepcopy(line.get("bbox"))
        box = geometry["rectified_bbox"] if geometry else [0., index * base * 2, width, (index * 2 + 1) * base]
        size = geometry["height"] if geometry else base
        y0 = original[1] if measured else box[1]
        y1 = original[3] if measured else box[3]
        kind = line.get("kind")
        if kind not in ("heading", "footnote", "image", "table"):
            heading = len(text) < 90 and (text in toc_titles or size > base * 1.3 or _CHAPTER.match(text))
            note = ((y0 > height * .73 and size < base * .9 and not text.isdigit())
                    or (y0 > height * .7 and _NOTE.match(text)))
            kind = "heading" if heading else "footnote" if note else "paragraph"
        margin = bool(measured and (y0 < height * .055 or y1 > height * .95))
        item = {"line": line, "index": index, "text": text, "geometry": geometry, "box": box,
                "kind": kind, "margin": margin, "center": (box[1] + box[3]) / 2}
        item["column"] = _column(item, width, dual)
        items.append(item)
    profiles = {column: _profile([i for i in items if i["column"] == column]) for column in {i["column"] for i in items}}
    barrier_boxes = []
    for barrier in barriers:
        geometry = line_geometry({"bbox": barrier}, rotation)
        if geometry:
            barrier_boxes.append(geometry["rectified_bbox"])
    result, previous = [], None
    for item in items:
        line, geometry, text = item["line"], item["geometry"], item["text"]
        box = deepcopy(line.get("bbox")) if geometry else None
        source = {"page": page_number, "bbox": box}
        if geometry:
            source.update(text_height=geometry["height"], baseline_angle_degrees=geometry["baseline_angle_degrees"])
            if geometry["has_polygon"]:
                source["polygon"] = deepcopy(line["polygon"])
        issues = []
        if item["kind"] == "footnote":
            issues.append(_issue("footnote_unlinked", "疑似脚注，保留在原文顺序；引用关系尚未可靠匹配。", page_number, box))
        confidence = deepcopy(line.get("confidence"))
        if confidence and isinstance(confidence.get("value"), (int, float)) and confidence["value"] < .8:
            issues.append(_issue("ocr_low_confidence", "OCR 引擎对本行置信度较低，请对照原页。", page_number, box))
        if item["margin"]:
            issues.append(_issue("margin_content", "可能为页眉、页脚或页码；为避免误删已保留。", page_number, box))
        join, reason, review = _decision(previous, item, profiles[item["column"]], barrier_boxes) if previous else (False, "page_start", False)
        if review:
            issues.append(_issue("paragraph_join_review" if join else "paragraph_boundary_review",
                                 "前行略短，按一致行距及正文对齐连接；请结合原页核对段落边界。" if join else
                                 "段落边界的版式依据不足，暂保留分段；请结合原页核对后合并。", page_number, box))
        if join:
            block = result[-1]
            separator = _separator(block["text"], text)
            if re.search(r"[A-Za-z]-$", block["text"]) and re.match(r"^[a-z]", text):
                issues.append(_issue("hyphenation_review", "跨行连字符已保留，请核对是否为断词。", page_number, box))
            start = len(block["text"]) + len(separator)
            block["text"] += separator + text
            block["sources"].append(source)
            block["line_spans"].append({"start": start, "end": len(block["text"]), "source_index": len(block["sources"]) - 1,
                                        "ordered_line_index": item["index"], "separator_before": separator})
            block["_bbox"] = _union([block["_bbox"], box])
            block["confidence"] = None
            block["issues"].extend(issues)
            block["paragraph_recovery"]["joins"].append(reason)
            block["paragraph_recovery"]["last_line"] = _edge_evidence(item, profiles[item["column"]])
        else:
            result.append({"kind": item["kind"], "text": text, "level": 1, "sources": [source],
                           "confidence": confidence, "asset": line.get("asset"), "rows": line.get("rows"),
                           "issues": issues, "_bbox": box or item["box"],
                           "line_spans": [{"start": 0, "end": len(text), "source_index": 0,
                                           "ordered_line_index": item["index"], "separator_before": ""}],
                           "paragraph_recovery": {"version": VERSION, "rotation_degrees": rotation,
                                                  "start_reason": reason, "joins": [],
                                                  "first_line": _edge_evidence(item, profiles[item["column"]]),
                                                  "last_line": _edge_evidence(item, profiles[item["column"]])}})
        previous = item
    for block in result:
        # These offsets describe the automatic draft. A later manual text edit
        # or cross-page merge must not accidentally treat old line offsets as
        # a new alignment; callers can verify this digest before using them.
        block["line_spans_text_sha256"] = hashlib.sha256(block["text"].encode("utf-8")).hexdigest()
    return result
