"""Conservative scan graphics detection from original pixels and OCR geometry.

This module neither recognizes text nor edits images. The caller crops the
original render using returned PDF-point boxes. All evidence is heuristic;
there is deliberately no fabricated confidence/accuracy field.
"""
from __future__ import annotations

import math

SCAN_GRAPHICS_VERSION = "scan-graphics-1"


def _area(box):
    return max(0., box[2] - box[0]) * max(0., box[3] - box[1])


def _body_line_count(lines, width):
    # A first-page essay must remain reflowable even on colored/dark paper.
    # The count is evidence for a conservative cover decision, not OCR quality.
    return sum(1 for line in lines if len(''.join(str(line.get('text', '')).split())) >= 16
               and isinstance(line.get('bbox'), (list, tuple)) and len(line['bbox']) == 4
               and line['bbox'][2] - line['bbox'][0] > width * .42)


def _inline_symbols(gray, lines, work_scale, paper_level, width, height):
    """Locate only filled round badges with white interior, inside OCR lines.

    A rounded glyph part is insufficient: the outer contour must be almost
    convex/circular and carry substantial fill, unlike O/0/口 or a plain dot.
    This preserves pixels, without identifying the symbol or its reference.
    """
    import cv2
    import numpy as np

    if work_scale < 1.5:
        return []  # At low resolution ordinary rounded glyphs are ambiguous.
    threshold = max(45., min(190., paper_level-60.))
    mask = (gray < threshold).astype('uint8') * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    symbols = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (10 <= w/work_scale <= 22 and 10 <= h/work_scale <= 22 and .8 < w/h < 1.2):
            continue
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        circularity = 4*math.pi*area/max(1., perimeter**2)
        solidity = area/max(1., cv2.contourArea(cv2.convexHull(contour)))
        density = float(np.count_nonzero(mask[y:y+h, x:x+w])/(w*h))
        if circularity <= .82 or solidity <= .95 or not .48 < density < .75:
            continue
        # Ordinary bold O/e can have similar binary geometry. Require a gray
        # filled base and several separated light interior strokes; do not infer
        # a badge solely from a circular glyph's hole or the surrounding text.
        crop = gray[y:y+h, x:x+w]
        ink_values = crop[mask[y:y+h, x:x+w] > 0]
        gray_base = float(np.percentile(ink_values, 10))
        if gray_base < 60 or gray_base > threshold-20:
            continue
        outer = np.zeros((h, w), dtype='uint8')
        shifted = contour - np.array([[[x, y]]], dtype=contour.dtype)
        cv2.drawContours(outer, [shifted], -1, 255, -1)
        light = ((outer > 0) & (crop >= threshold)).astype('uint8')
        _, _, stats, _ = cv2.connectedComponentsWithStats(light, 8)
        light_parts = sum(int(area) >= max(3, round(work_scale**2)) for area in stats[1:, cv2.CC_STAT_AREA])
        if light_parts < 3:
            continue
        box = [x/work_scale, y/work_scale, (x+w)/work_scale, (y+h)/work_scale]
        cx, cy = (box[0]+box[2])/2, (box[1]+box[3])/2
        matching = []
        for line in lines:
            lb = line.get('bbox')
            if not isinstance(lb, (list, tuple)) or len(lb) != 4:
                continue
            try:
                if (all(math.isfinite(float(v)) for v in lb) and lb[0] <= cx <= lb[2]
                        and lb[1]-2 <= cy <= lb[3]+2 and lb[2]-lb[0] > (box[2]-box[0])*2
                        and .35 < (box[3]-box[1])/max(1., lb[3]-lb[1]) < 1.4):
                    matching.append(list(lb))
            except (TypeError, ValueError):
                continue
        if len(matching) != 1:
            continue
        padded = [max(0., box[0]-1), max(0., box[1]-1), min(float(width), box[2]+1), min(float(height), box[3]+1)]
        symbols.append({'bbox': padded, 'role': 'inline-symbol',
                        'reason': '正文行内检测到实底圆形小图标，保留原区域像素；未推断注解关系或替换OCR文字，位置请对照原页。',
                        'evidence': {'method': 'original-pixel filled-round-symbol geometry',
                                     'circularity': circularity, 'solidity': solidity,
                                     'foreground_density': density, 'gray_base_10_percentile': gray_base,
                                     'separated_light_parts': light_parts, 'ocr_line_bbox': matching[0]}})
    return sorted(symbols, key=lambda item: (item['bbox'][1], item['bbox'][0]))


def analyze_scan_graphics(image, lines, scale, width, height, *, page_number):
    """Return ``{version, regions, issues, evidence}`` without mutating inputs.

    ``image`` is the page's RGB PIL render; OCR line boxes and returned regions
    use PDF points, top-left origin. ``role=cover`` is reserved for original page
    1 with dominant non-text artwork and little body text. All other retained
    regions are ``figure``; small filled circular symbols within a text line
    use ``inline-symbol`` and never replace the recognized text. This is not a learned layout classifier: unknown
    text is never removed, and the caller must preserve its original OCR draft.
    """
    import cv2
    import numpy as np

    if not all(math.isfinite(float(value)) and float(value) > 0 for value in (scale, width, height)):
        raise ValueError('扫描图像比例与页面尺寸必须为正有限数。')
    if not isinstance(page_number, int) or page_number < 1:
        raise ValueError('来源页码必须为正整数。')
    rgb = np.asarray(image.convert('RGB'))
    if rgb.size == 0:
        raise ValueError('扫描图像为空。')
    # Work at at most 144 dpi; the eventual crop always uses original pixels.
    work_scale = min(float(scale), 2.)
    work_width = max(1, round(width * work_scale))
    work_height = max(1, round(height * work_scale))
    rgb = cv2.resize(rgb, (work_width, work_height), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    paper_level = float(np.percentile(gray, 95))
    # A fixed 205 threshold treats a gray scan background as an illustration.
    # Estimate its light level, while retaining flat dark artwork and pale color.
    dark_limit = max(45., min(220., paper_level - 32.))
    foreground = (gray < dark_limit) | ((hsv[:, :, 1] > 45) & (hsv[:, :, 2] > 50))
    mask = foreground.astype('uint8') * 255
    for line in lines:
        box = line.get('bbox')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            if not all(math.isfinite(float(value)) for value in box):
                continue
        except (TypeError, ValueError):
            continue
        # The source OCR may be slightly tilted. Its axis-aligned box plus a
        # small point-based pad removes glyph fringes, without changing pixels.
        x0, y0, x1, y1 = box
        pad = 2.5
        left, top = max(0, math.floor((x0-pad)*work_scale)), max(0, math.floor((y0-pad)*work_scale))
        right, bottom = min(work_width, math.ceil((x1+pad)*work_scale)), min(work_height, math.ceil((y1+pad)*work_scale))
        if right > left and bottom > top:
            mask[top:bottom, left:right] = 0
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, round(6*work_scale)), max(3, round(6*work_scale))))
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    page_area = width * height
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        ink = int(np.count_nonzero(mask[y:y+h, x:x+w]))
        box = [max(0., x/work_scale-3), max(0., y/work_scale-3),
               min(float(width), (x+w)/work_scale+3), min(float(height), (y+h)/work_scale+3)]
        area_ratio = _area(box) / page_area
        density = ink / max(1, w*h)
        # Tiny note icons and ruled separators are not standalone illustrations.
        if w/work_scale < 35 or h/work_scale < 24 or ink/(work_scale**2) < 100:
            continue
        # Do not mistake a rectangular scan edge for a nearly whole-page image.
        # This also rejects sparse OCR remnants connected around a page boundary.
        if area_ratio > .65 and density < .12:
            continue
        candidates.append({'bbox': box, 'area_ratio': area_ratio,
                           'residual_ink_ratio': ink/(work_width*work_height),
                           'residual_density': density})
    candidates.sort(key=lambda item: (item['bbox'][1], item['bbox'][0]))
    body_lines = _body_line_count(lines, width)
    evidence = {'method': 'OCR-masked original-pixel morphology',
                'paper_level_95_percentile': paper_level, 'dark_threshold': dark_limit,
                'residual_ink_ratio': float(np.count_nonzero(mask)/mask.size),
                'body_line_count': body_lines, 'analysis_dpi': work_scale*72}
    dominant = max(candidates, key=lambda item: item['area_ratio'], default=None)
    if (page_number == 1 and body_lines < 8 and dominant is not None
            and dominant['area_ratio'] > .45 and dominant['residual_ink_ratio'] > .10):
        reason = '原 PDF 首页存在占据主要版面的非文字图像，按原页完整保留为封面；不是将正文逐页图片化。'
        return {'version': SCAN_GRAPHICS_VERSION,
                'regions': [{'bbox': [0., 0., float(width), float(height)], 'role': 'cover',
                             'reason': reason, 'evidence': {**evidence, 'dominant_region': dominant}}],
                'issues': [{'code': 'scan_cover_preserved', 'message': reason, 'page': page_number,
                            'bbox': [0., 0., float(width), float(height)], 'type': 'heuristic', 'severity': 'info'}],
                'evidence': evidence}
    regions = []
    for item in candidates:
        # A large text page with poor OCR must not become a purported picture
        # result. Preserve only localized residual graphics in that situation.
        if item['area_ratio'] > .65 and body_lines >= 8:
            continue
        regions.append({'bbox': item['bbox'], 'role': 'figure',
                        'reason': '已识别文字区域之外仍有成片图形，保留原区域像素；请核对图内文字与裁切边界。',
                        'evidence': {key: value for key, value in item.items() if key != 'bbox'}})
    for symbol in _inline_symbols(gray, lines, work_scale, paper_level, width, height):
        sb = symbol['bbox']
        # Artwork already retained as an image must not also yield its details
        # as standalone badges, even when OCR found labels inside that artwork.
        if any(_area([max(sb[0], r['bbox'][0]), max(sb[1], r['bbox'][1]),
                      min(sb[2], r['bbox'][2]), min(sb[3], r['bbox'][3])]) > _area(sb)*.5 for r in regions):
            continue
        regions.append(symbol)
    regions.sort(key=lambda item: (item['bbox'][1], item['bbox'][0]))
    issues = [{'code': 'inline_symbol_position_review', 'message': region['reason'],
               'page': page_number, 'bbox': region['bbox'], 'type': 'heuristic', 'severity': 'warning'}
              for region in regions if region['role'] == 'inline-symbol']
    return {'version': SCAN_GRAPHICS_VERSION, 'regions': regions, 'issues': issues, 'evidence': evidence}
