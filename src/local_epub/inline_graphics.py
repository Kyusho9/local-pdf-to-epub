"""Keep small source graphics with conservative, auditable text anchors.

Glyph identity and note relationships are not inferred. The OCR string is
never replaced; estimated character positions are explicitly review items.
"""
from copy import deepcopy
import hashlib
import re


def text_sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def joined_text_metadata(first, second, separator):
    """Move proven anchors with an exact concatenation; retain ambiguous images."""
    left, right = first.get('text', ''), second.get('text', '')
    offset = len(left) + len(separator)
    joined_hash = text_sha(left + separator + right)
    images = []
    for block, shift in ((first, 0), (second, offset)):
        for original in block.get('inline_images', []):
            item = deepcopy(original)
            position = item.get('offset')
            if (item.get('text_sha256') == text_sha(block.get('text', ''))
                    and isinstance(position, int) and 0 <= position <= len(block.get('text', ''))):
                item.update(offset=position + shift, text_sha256=joined_hash)
            images.append(item)
    result = {'inline_images': images}
    if all(b.get('line_spans_text_sha256') == text_sha(b.get('text', '')) for b in (first, second)):
        spans = deepcopy(first.get('line_spans', []))
        for span in deepcopy(second.get('line_spans', [])):
            span['start'] += offset
            span['end'] += offset
            span['source_index'] += len(first.get('sources', []))
            spans.append(span)
        result.update(line_spans=spans, line_spans_text_sha256=joined_hash)
    else:
        result.update(line_spans=[], line_spans_text_sha256=None)
    return result


def _overlap(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    return intersection / max(1, (a[2]-a[0])*(a[3]-a[1]))


def attach_inline_graphic(blocks, region, asset, page):
    line_box = region.get('evidence', {}).get('ocr_line_bbox')
    if not line_box:
        return False
    candidates = []
    for block in blocks:
        if block.get('kind') not in ('paragraph', 'heading', 'footnote'):
            continue
        if block.get('line_spans_text_sha256') != text_sha(block.get('text', '')):
            continue
        for span in block.get('line_spans', []):
            source = block['sources'][span['source_index']]
            box = source.get('bbox')
            if box and _overlap(line_box, box) > .65 and _overlap(region['bbox'], box) > .7:
                candidates.append((block, span, source))
    if len(candidates) != 1:
        return False
    block, span, source = candidates[0]
    value = block['text'][span['start']:span['end']]
    if not value:
        return False
    box = region['bbox']; center = [(box[0]+box[2])/2, (box[1]+box[3])/2]
    polygon = source.get('polygon')
    if polygon and len(polygon) == 4:
        pts = sorted(polygon, key=lambda p: p[0])
        left = [sum(p[n] for p in pts[:2])/2 for n in (0, 1)]
        right = [sum(p[n] for p in pts[2:])/2 for n in (0, 1)]
    else:
        b = source['bbox']; left, right = [b[0], (b[1]+b[3])/2], [b[2], (b[1]+b[3])/2]
    dx, dy = right[0]-left[0], right[1]-left[1]
    fraction = ((center[0]-left[0])*dx+(center[1]-left[1])*dy)/max(1, dx*dx+dy*dy)
    widths = [1.0 if re.match(r'[\u2e80-\uffef]', c) else .55 for c in value]
    target = min(1., max(0., fraction))*sum(widths)
    position, cursor = 0, 0.
    distances = []
    for i, advance in enumerate(widths):
        distances.append((abs(cursor+advance/2-target), i)); cursor += advance
    position = min(distances)[1]
    block.setdefault('inline_images', []).append({
        'asset': asset, 'role': 'inline-symbol', 'offset': span['start']+position,
        'text_sha256': text_sha(block['text']), 'source': {'page': page, 'bbox': deepcopy(box)},
        'placement': 'estimated-character-position-from-original-line',
        'detection': deepcopy(region.get('evidence')), 'ocr_text_replaced': False,
    })
    block['issues'].append({'code': 'inline_graphic_review',
        'message': '行内图标原图已按原行的近似字符位置保留；相邻 OCR 字符未改写，尚未重建注解关系，请核对。',
        'page': page, 'bbox': deepcopy(box), 'type': 'heuristic', 'severity': 'warning'})
    return True
