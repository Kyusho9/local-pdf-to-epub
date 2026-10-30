"""Local, replaceable PDF backend. No network operations are used by this module."""
from __future__ import annotations

import importlib.metadata
from copy import deepcopy
import hashlib
import json
from collections import Counter
from difflib import SequenceMatcher
import math
import re
import statistics
import unicodedata
from pathlib import Path

BACKEND_VERSION = "pdfplumber-rapidocr-v4"
PAGE_LAYOUT_VERSION = "scan-layout-2-paragraphs-graphics"
_OCR_ENGINES = {}


def engine_versions():
    import hashlib
    import importlib.util
    versions = {name: importlib.metadata.version(name) for name in
                ("pdfplumber", "pypdfium2", "rapidocr-onnxruntime", "onnxruntime", "opencv-python", "numpy")}
    versions["backend"] = BACKEND_VERSION
    cv_spec = importlib.util.find_spec("cv2")
    build = Path(cv_spec.origin).parent / "LOCAL_BUILD.json" if cv_spec and cv_spec.origin else None
    versions["opencv_build"] = hashlib.sha256(build.read_bytes()).hexdigest() if build and build.is_file() else "official-wheel"
    return versions


def _reader(path, password=None):
    from pypdf import PdfReader
    reader = PdfReader(str(path), strict=False)
    owner = False
    if reader.is_encrypted:
        if password is None:
            raise ValueError("PDF 已加密，请提供密码；原文件不会被修改。")
        result = reader.decrypt(password)
        if not result:
            raise ValueError("PDF 密码不正确。")
        owner = int(result) == 2
    permissions = None
    if reader.is_encrypted and reader.trailer.get("/Encrypt"):
        permissions = int(reader.trailer["/Encrypt"].get("/P", -1))
        if not owner and not permissions & 16:
            raise PermissionError("该 PDF 禁止内容提取，请使用拥有提取权限的文件或所有者密码。")
    return reader, permissions, owner


def inspect_pdf(path, password=None):
    reader, permissions, owner = _reader(path, password)
    if not len(reader.pages):
        raise ValueError("PDF 没有可处理的页面。")
    meta = reader.metadata or {}
    toc = []

    def walk(items, level=1):
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
            else:
                try:
                    number = reader.get_destination_page_number(item)
                    if number is not None and 0 <= number < len(reader.pages):
                        toc.append({"title": str(item.title), "level": min(level, 6), "page": number + 1})
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
    try:
        walk(reader.outline)
    except (ValueError, TypeError, KeyError):
        pass
    return {"page_count": len(reader.pages), "metadata": {
        "title": str(meta.get("/Title") or Path(path).stem),
        "author": str(meta.get("/Author") or "")}, "toc": toc,
        "permissions": {"encrypted": bool(reader.is_encrypted), "raw": permissions,
                        "extraction_allowed": owner or permissions is None or bool(permissions & 16), "owner_password": owner}}


def _render(path, page_number, password, dpi):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path), password=password)
    try:
        if not 1 <= page_number <= len(pdf):
            raise ValueError("原页回查页码超出 PDF 范围。")
        page = pdf[page_number - 1]
        try:
            bitmap = page.render(scale=dpi / 72)
            try:
                return bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        pdf.close()


def render_page(path, page_number, destination, password=None, dpi=100):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = _render(path, page_number, password, dpi)
    image.save(destination, "PNG")
    return str(destination)


def _issue(code, message, page, bbox=None):
    return {"code": code, "message": message, "page": page, "bbox": bbox,
            "type": "heuristic", "severity": "warning"}


def _box(words):
    return [min(w["x0"] for w in words), min(w["top"] for w in words),
            max(w["x1"] for w in words), max(w["bottom"] for w in words)]


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _area(box):
    return max(0., box[2] - box[0]) * max(0., box[3] - box[1])


def _intersection(a, b):
    return _area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def _overlap(a, b, threshold=.5):
    return _intersection(a, b) / max(1, min(_area(a), _area(b))) >= threshold


def _text_health(text):
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False, ["没有可提取文字"]
    bad = sum(ch == "\ufffd" or unicodedata.category(ch) in ("Co", "Cn") for ch in compact)
    reasons = []
    if "(cid:" in text or bad / len(compact) > .02:
        reasons.append("文字层包含乱码、未映射字符或私用区字符")
    if len(compact) >= 16 and max(Counter(compact).values()) / len(compact) > .6:
        reasons.append("文字层异常重复")
    if re.search(r"(.)\1{10,}", compact):
        reasons.append("文字层连续重复字符")
    controls = sum(unicodedata.category(ch) == "Cc" and ch not in "\n\t\r" for ch in text)
    if controls:
        reasons.append("文字层含控制字符")
    return not reasons, reasons


def _join_words(words):
    result = ""
    previous = None
    for word in sorted(words, key=lambda w: w["x0"]):
        value = word["text"]
        if previous and result and value:
            cjk = lambda c: "\u2e80" <= c <= "\uffef"
            if not cjk(result[-1]) and not cjk(value[0]):
                result += " "
        result += value
        previous = word
    return result


def _native_lines(words):
    """Cluster words into rows, then split large horizontal gaps (including gutters)."""
    rows = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        center = (word["top"] + word["bottom"]) / 2
        target = None
        for row in reversed(rows[-6:]):
            if abs(center - row["center"]) <= max(2.5, (word["bottom"] - word["top"]) * .35):
                target = row
                break
        if target is None:
            target = {"center": center, "words": []}
            rows.append(target)
        target["words"].append(word)
    lines = []
    for row in rows:
        ordered = sorted(row["words"], key=lambda w: w["x0"])
        groups = [[]]
        for word in ordered:
            if groups[-1]:
                prev = groups[-1][-1]
                if word["x0"] - prev["x1"] > max(28, float(word.get("size", 12)) * 2.2):
                    groups.append([])
            groups[-1].append(word)
        for group in groups:
            lines.append({"text": _join_words(group), "bbox": _box(group),
                          "size": statistics.median(float(w.get("size", w["bottom"]-w["top"])) for w in group),
                          "confidence": None, "method": "text"})
    return lines


def _ocr(resource_dir):
    resource_dir = Path(resource_dir).resolve()
    from .resources import require_resources
    require_resources(resource_dir)
    paths = [resource_dir / (n + ".onnx") for n in ("det", "rec", "cls")]
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError("缺少本地 OCR 模型：" + "、".join(missing) + "。请在资源设置导入离线资源包。")
    key = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    if key not in _OCR_ENGINES:
        import onnxruntime as ort
        ort.disable_telemetry_events()
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINES.clear()
        engine = RapidOCR(det_model_path=str(paths[0]), rec_model_path=str(paths[1]),
                                     cls_model_path=str(paths[2]), det_use_cuda=False,
                                     rec_use_cuda=False, cls_use_cuda=False,
                                     det_use_dml=False, rec_use_dml=False, cls_use_dml=False,
                                     intra_op_num_threads=2, inter_op_num_threads=1,
                                     print_verbose=False, text_score=.1)
        for session in (engine.text_det.infer.session, engine.text_rec.session.session, engine.text_cls.infer.session):
            if session.get_providers() != ["CPUExecutionProvider"]:
                raise RuntimeError("OCR 实际执行设备与 CPU 基线不一致，请检查本地运行包。")
        _OCR_ENGINES[key] = engine
    return _OCR_ENGINES[key]


def _ocr_lines(image, resource_dir, scale, engine=None):
    import numpy as np
    engine = engine or _ocr(resource_dir)
    result, elapsed = engine(np.asarray(image))
    lines = []
    for coords, text, confidence in result or []:
        box = [min(pt[0] for pt in coords)/scale, min(pt[1] for pt in coords)/scale,
               max(pt[0] for pt in coords)/scale, max(pt[1] for pt in coords)/scale]
        lines.append({"text": str(text), "bbox": box, "size": box[3] - box[1],
                      "confidence": {"value": float(confidence), "source": "RapidOCR recognition"},
                      "method": "ocr", "polygon": [[float(x)/scale, float(y)/scale] for x,y in coords]})
    recovered, margin_elapsed = _margin_ocr_lines(image, resource_dir, scale, lines, engine=engine)
    lines.extend(recovered)
    timings = [float(x) for x in elapsed] if elapsed is not None else None
    if margin_elapsed is not None:
        timings = [a + b for a, b in zip(timings or [0., 0., 0.], margin_elapsed)]
    return lines, timings


def _margin_ocr_lines(image, resource_dir, scale, existing_lines, engine=None):
    """Recover small, visible digits that whole-page detection missed.

    The same local detector/recognizer runs on narrow page-edge crops. Its
    normal local scaling can find glyphs whose whole-page detection probability
    is too low to produce a contour. No physical page index, adjacent page, or
    inferred numbering sequence is available here. This only adds OCR evidence;
    running-matter classification remains a separate conservative stage.
    """
    import numpy as np
    array = np.asarray(image.convert("RGB"))
    height, width = array.shape[:2]
    strip_height = max(1, int(math.ceil(height * .08)))
    accepted = []
    timings = None
    for top, bottom in ((0, strip_height), (height - strip_height, height)):
        crop = array[top:bottom]
        # Do not ask a recognizer to interpret empty or almost white paper.
        if np.count_nonzero(np.min(crop, axis=2) < 220) < 8:
            continue
        engine = engine or _ocr(resource_dir)
        result, elapsed = engine(crop)
        if elapsed is not None:
            timings = [a + float(b) for a, b in zip(timings or [0., 0., 0.], elapsed)]
        for coords, text, confidence in result or []:
            text = str(text).strip()
            score = float(confidence)
            if not re.fullmatch(r"[0-9]{1,4}", text) or not math.isfinite(score) or score < .95:
                continue
            polygon = [[float(x) / scale, (float(y) + top) / scale] for x, y in coords]
            box = [min(p[0] for p in polygon), min(p[1] for p in polygon),
                   max(p[0] for p in polygon), max(p[1] for p in polygon)]
            bw, bh = box[2] - box[0], box[3] - box[1]
            if not (0 <= box[0] < box[2] <= width / scale
                    and top / scale <= box[1] < box[3] <= bottom / scale
                    and 2 <= bh <= min(18, height / scale * .032)
                    and 1 <= bw <= min(70, width / scale * .2)):
                continue
            # A local crop must never create a second version of an existing
            # line, even when it recognizes different text at that location.
            expanded = [box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1]
            if any(_overlap(expanded, old["bbox"], .2) for old in existing_lines + accepted):
                continue
            pixels = array[max(0, int(box[1] * scale)):min(height, math.ceil(box[3] * scale)),
                           max(0, int(box[0] * scale)):min(width, math.ceil(box[2] * scale))]
            ink = np.min(pixels, axis=2) < 220
            if not ink.size or np.count_nonzero(ink) < 8 or not .015 <= float(ink.mean()) <= .75:
                continue
            accepted.append({"text": text, "bbox": box, "size": bh,
                             "confidence": {"value": score, "source": "RapidOCR recognition",
                                            "context": "page-margin crop"},
                             "method": "ocr", "polygon": polygon,
                             "recognition_context": {"scope": "page-margin crop",
                                                     "crop_bbox": [0., top / scale, width / scale, bottom / scale]}})
    return accepted, timings


def _recognize_page(image, resource_dir, scale, page_number, executor):
    """Commit an OCR page only after actual execution was audited.

    On CUDA failure rerun the complete page and edge crops on CPU, so partial
    GPU results can never silently mingle with a different CPU recognition.
    """
    from .gpu_execution import OCRCancelled, GPUExecutionError, cpu_identity
    fallback_reason = None
    if executor is not None:
        try:
            executor.begin_page(page_number)
            lines, elapsed = _ocr_lines(image, resource_dir, scale, engine=executor)
            execution = executor.end_page()
            return lines, elapsed, execution
        except OCRCancelled:
            executor.close()
            raise
        except Exception as exc:
            if not executor.failure_reason:
                executor.fail(exc)
            if not executor.fallback:
                raise GPUExecutionError(executor.failure_reason) from exc
            fallback_reason = executor.failure_reason
    lines, elapsed = _ocr_lines(image, resource_dir, scale)
    execution = {"identity": cpu_identity(), "verified": True,
                 "evidence": "CPUExecutionProvider-only-session",
                 "requested_device": executor.backend if executor else "cpu"}
    if fallback_reason:
        execution["fallback_reason"] = fallback_reason
    return lines, elapsed, execution


def _reading_order(lines, width):
    if not lines:
        return [], False
    left = [l for l in lines if l["bbox"][2] < width * .54 and l["bbox"][0] < width * .3]
    right = [l for l in lines if l["bbox"][0] > width * .46]
    dual = len(left) >= 3 and len(right) >= 3
    if not dual:
        return sorted(lines, key=lambda l: (round(l["bbox"][1]/3), l["bbox"][0])), False
    # Full-width headings delimit horizontal bands; inside each band read left column then right.
    spanning = sorted([l for l in lines if l["bbox"][0] < width * .46 and l["bbox"][2] > width * .54], key=lambda l:l["bbox"][1])
    rest = [l for l in lines if l not in spanning]
    result = []
    for divider in spanning + [None]:
        boundary = divider["bbox"][1] if divider else math.inf
        band = [l for l in rest if l["bbox"][1] < boundary]
        rest = [l for l in rest if l["bbox"][1] >= boundary]
        result.extend(sorted([l for l in band if l["bbox"][0] < width/2], key=lambda l:l["bbox"][1]))
        result.extend(sorted([l for l in band if l["bbox"][0] >= width/2], key=lambda l:l["bbox"][1]))
        if divider:
            result.append(divider)
    return result, True


def _paragraphs(lines, width, height, page_number, toc_titles=(), barriers=()):
    from .paragraphs import recover_paragraphs
    ordered, dual = _reading_order(lines, width)
    return recover_paragraphs(ordered, width, height, page_number, toc_titles,
                              barriers=barriers, dual=dual), dual


def _crop(image, box, scale, destination):
    b = [max(0, int(box[0]*scale)), max(0, int(box[1]*scale)),
         min(image.width, math.ceil(box[2]*scale)), min(image.height, math.ceil(box[3]*scale))]
    if b[2] <= b[0] or b[3] <= b[1]:
        return False
    image.crop(b).save(destination, "PNG")
    return True


def _scan_figures(image, lines, scale, width, height):
    """Conservative residual graphic detection; not a learned layout model."""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    mask = (gray < 205).astype("uint8") * 255
    for line in lines:
        x0,y0,x1,y1 = line["bbox"]
        pad = 3*scale
        cv2.rectangle(mask, (max(0,int(x0*scale-pad)),max(0,int(y0*scale-pad))),
                      (min(image.width,int(x1*scale+pad)),min(image.height,int(y1*scale+pad))), 0, -1)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3,int(8*scale)), max(3,int(8*scale))))
    merged = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x,y,w,h = cv2.boundingRect(contour)
        box = [max(0,x/scale-3),max(0,y/scale-3),min(width,(x+w)/scale+3),min(height,(y+h)/scale+3)]
        area = _area(box)
        if w/scale >= 35 and h/scale >= 24 and 500 < area < width*height*.65:
            if np.count_nonzero(mask[y:y+h,x:x+w]) >= 100*scale*scale:
                boxes.append(box)
    return boxes


def _text_only_region(image, box, lines, scale):
    import numpy as np
    crop = image.crop((max(0,int(box[0]*scale)),max(0,int(box[1]*scale)),
                       min(image.width,math.ceil(box[2]*scale)),min(image.height,math.ceil(box[3]*scale))))
    pixels = np.array(crop.convert("L"))
    matched = False
    for line in lines:
        if _overlap(line["bbox"],box,.7):
            matched = True
            x0,y0,x1,y1 = line["bbox"]
            a = max(0,int((x0-box[0]-3)*scale)); b=max(0,int((y0-box[1]-3)*scale))
            c = min(pixels.shape[1],math.ceil((x1-box[0]+3)*scale)); d=min(pixels.shape[0],math.ceil((y1-box[1]+3)*scale))
            pixels[b:d,a:c] = 255
    return matched and int(np.count_nonzero(pixels < 220)) < max(100,pixels.size*.001)


def _vector_regions(page, table_regions):
    boxes = []
    for item in page.curves + page.rects + page.lines:
        box = [float(item["x0"]), float(item["top"]), float(item["x1"]), float(item["bottom"])]
        if _area(box) > page.width * page.height * .8 or any(_overlap(box, b, .8) for b in table_regions):
            continue
        # Grow line bboxes slightly so strokes can be clustered into original figures.
        box = [box[0]-1, box[1]-1, box[2]+1, box[3]+1]
        boxes.append(box)
    groups = []
    while boxes:
        group = boxes.pop()
        changed = True
        while changed:
            changed = False
            for other in list(boxes):
                expanded = [group[0]-8,group[1]-8,group[2]+8,group[3]+8]
                if _intersection(expanded,other) > 0:
                    group = _union([group,other])
                    boxes.remove(other)
                    changed = True
        if any(_overlap(group,table,.7) for table in table_regions):
            continue
        if group[2]-group[0] > 25 and group[3]-group[1] > 20:
            groups.append([max(0,group[0]-2),max(0,group[1]-2),min(page.width,group[2]+2),min(page.height,group[3]+2)])
    return groups


def extract_page(path, page_number, output_dir, options, resource_dir, password=None, ocr_executor=None, cached_ocr=None):
    import pdfplumber
    mode = options.get("mode", "auto")
    if mode not in ("auto", "text", "ocr"):
        raise ValueError("识别模式必须为 auto、text 或 ocr。")
    dpi = min(400, max(100, int(options.get("dpi", 180))))
    output_dir = Path(output_dir)
    assets = output_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    issues = []
    with pdfplumber.open(str(path), password=password) as pdf:
        if not 1 <= page_number <= len(pdf.pages):
            raise ValueError(f"页码 {page_number} 超出 PDF 范围。")
        page = pdf.pages[page_number-1]
        width,height = float(page.width),float(page.height)
        native_text = page.extract_text() or ""
        words = page.extract_words(extra_attrs=["size"], keep_blank_chars=False)
        native_lines = _native_lines(words)
        healthy, reasons = _text_health(native_text)
        image_boxes = [[max(0,float(i["x0"])),max(0,float(i["top"])),min(width,float(i["x1"])),min(height,float(i["bottom"]))]
                       for i in page.images if float(i["x1"])-float(i["x0"]) > 12 and float(i["bottom"])-float(i["top"]) > 12]
        large_scan = any(_area(b) > width*height*.55 for b in image_boxes)
        if words and sum(float(w["size"]) < 3 for w in words) / len(words) > .5:
            healthy = False
            reasons.append("文字层字号异常小")
        if large_scan and len(re.sub(r"\s", "", native_text)) < 18:
            healthy = False
            reasons.append("整页扫描图仅带少量文字层")
        # Regions with embedded raster content may hold paragraphs absent from the native layer.
        regions = [b for b in image_boxes if _area(b) > 1200 and
                   sum(_intersection(b,l["bbox"]) for l in native_lines) < _area(b)*.12]
        use_ocr = mode == "ocr" or not healthy or not native_lines or bool(regions)
        if mode == "text" and native_lines and healthy:
            use_ocr = False
            if regions:
                issues.append(_issue("image_text_not_recognized", "优先文字层模式未识别图片内文字，图片原样保留。", page_number))
        rendered = None
        ocr_lines,elapsed = [],None
        execution = {"identity": {"kind": "native"}, "verified": True,
                     "evidence": "native-text-extraction-no-OCR"}
        if use_ocr:
            rendered = _render(path,page_number,password,dpi)
            if cached_ocr is None:
                ocr_lines,elapsed,execution = _recognize_page(rendered, resource_dir, dpi/72, page_number, ocr_executor)
            else:
                if not isinstance(cached_ocr.get("ocr_lines"), list) or not isinstance(cached_ocr.get("execution"), dict):
                    raise ValueError("缺少原始识别行或实际设备记录，不能仅重建版面；请重新识别此页。")
                ocr_lines = deepcopy(cached_ocr["ocr_lines"])
                elapsed = deepcopy(cached_ocr.get("ocr_elapsed_seconds"))
                execution = deepcopy(cached_ocr["execution"])
            if execution.get("fallback_reason"):
                issues.append(_issue("gpu_cpu_fallback", "本页 GPU 识别未完成，已整页重新使用 CPU：" + execution["fallback_reason"], page_number))
            if mode == "auto" and large_scan and healthy and native_lines:
                compared,disagreed = 0,0
                for native in native_lines:
                    nearby = [line for line in ocr_lines if _overlap(line["bbox"],native["bbox"],.3)
                              and line["confidence"]["value"] >= .85]
                    if nearby:
                        original = re.sub(r"\s+", "", native["text"])
                        visible = re.sub(r"\s+", "", "".join(line["text"] for line in nearby))
                        compared += len(original)
                        if SequenceMatcher(None,original,visible).ratio() < .45:
                            disagreed += len(original)
                if compared >= 12 and disagreed/compared > .5:
                    healthy = False
                    reasons.append("文字层与可见页面 OCR 明显不一致")
                else:
                    issues.append(_issue("scan_text_layer_review","扫描图带文字层，已优先保留可信文字；文字层语义准确性仍需原页核对。",page_number))
        if mode == "ocr" or not healthy or not native_lines:
            lines = ocr_lines
            method = "ocr"
            if native_lines:
                issues.append(_issue("text_layer_replaced", "文字层未采用，整页使用 OCR，避免重复。" + "；".join(reasons), page_number))
        else:
            lines = list(native_lines)
            added = []
            for line in ocr_lines:
                if any(_overlap(line["bbox"],region,.4) for region in regions) and not any(_overlap(line["bbox"],old["bbox"],.35) for old in native_lines):
                    added.append(line)
            lines.extend(added)
            method = "mixed" if added else "text"
        scan_graphics = {"regions": [], "issues": []}
        if large_scan:
            from .scan_graphics import analyze_scan_graphics
            if rendered is None:
                rendered = _render(path, page_number, password, dpi)
            scan_graphics = analyze_scan_graphics(rendered, ocr_lines or lines, dpi/72, width, height,
                                                 page_number=page_number)
            issues.extend(scan_graphics["issues"])
        if use_ocr and not ocr_lines:
            # An actually blank page may succeed; non-white page with no OCR must be visible as failure.
            import numpy as np
            pixels = np.asarray(rendered.convert("L"))
            if (pixels < 220).mean() > .001 and not lines and not scan_graphics["regions"]:
                raise ValueError(f"第 {page_number} 页有可见内容但未识别到文字，不能视为成功；请重试或明确排除该页。")
        table_regions = []
        table_blocks = []
        # Native line-ruled tables only; scanned/complex tables are retained as region images.
        if method == "text":
            try:
                for table in page.find_tables():
                    rows = table.extract()
                    if len(rows) >= 2 and max(map(len,rows),default=0) >= 2:
                        box = list(map(float,table.bbox))
                        rows = [[str(c or "") for c in row] for row in rows]
                        table_regions.append(box)
                        table_blocks.append({"kind":"table","text":"\n".join("\t".join(r) for r in rows),
                            "rows":rows,"asset":None,"sources":[{"page":page_number,"bbox":box}],
                            "level":1,"confidence":None,"issues":[_issue("table_review","按可见表格线提取，请核对单元格与合并单元格。",page_number,box)],"_bbox":box})
            except (ValueError,TypeError,KeyError) as exc:
                issues.append(_issue("table_detection_failed","表格检测未完成，原文字仍保留："+str(exc),page_number))
        formula_regions = []
        for candidate in lines:
            text = candidate["text"]
            if len(text) < 100 and re.search(r"[=∑∫√×÷]",text) and len(re.findall(r"[\u4e00-\u9fff]",text)) < 3:
                box = candidate["bbox"]
                size = candidate["size"]
                expanded = [box[0]-size*2,box[1]-size*1.2,box[2]+size*2,box[3]+size*.6]
                related = [line["bbox"] for line in lines if _intersection(expanded,line["bbox"]) > 0
                           and len(re.findall(r"[\u4e00-\u9fff]",line["text"])) < 3]
                region = _union(related or [box])
                formula_regions.append([max(0,region[0]-4),max(0,region[1]-4),min(width,region[2]+4),min(height,region[3]+4)])
        figures = []
        graphic_roles = {}
        if large_scan:
            for region in scan_graphics["regions"]:
                figures.append(region["bbox"])
                graphic_roles[tuple(region["bbox"])] = region
            issues.append(_issue("scan_layout_review", "扫描图形按原像素及文字区域分析保留；特殊版式仍请原页核对。", page_number))
        else:
            figures = [b for b in image_boxes if not any(_overlap(b,t,.8) for t in table_regions)]
            if ocr_lines and rendered is not None:
                text_only = [box for box in figures if _text_only_region(rendered,box,ocr_lines,dpi/72)]
                figures = [box for box in figures if box not in text_only]
                for box in text_only:
                    issues.append(_issue("image_text_reflow", "该图片区域仅检测到文字，已转为可重排正文；原图仍可在原页回查。", page_number,box))
        covers = [r["bbox"] for r in scan_graphics["regions"] if r["role"] == "cover"]
        content_lines = [l for l in lines if not any(_overlap(l["bbox"],b,.7) for b in table_regions+formula_regions+covers)]
        barriers = [b for b in figures if graphic_roles.get(tuple(b), {}).get("role") != "inline-symbol"]
        blocks,dual = _paragraphs(content_lines,width,height,page_number,options.get("toc_titles", []),
                                  barriers=barriers+table_regions+formula_regions)
        blocks.extend(table_blocks)
        if covers:
            issues.append(_issue("cover_original_pixels", "封面按原图保留，原始识别文字仍在任务中；图内烧录文字随图保留，不涂抹或重画。", page_number))
        if dual:
            issues.append(_issue("multi_column_review", "检测到双栏，采用左栏后右栏顺序，请核对跨栏内容。", page_number))
        figures.extend(formula_regions)
        if method != "ocr":
            for region in _vector_regions(page,table_regions):
                if not any(_overlap(region,box,.8) for box in figures):
                    figures.append(region)
        for n,box in enumerate(figures,1):
            if rendered is None:
                rendered = _render(path,page_number,password,dpi)
            region = graphic_roles.get(tuple(box), {})
            role = region.get("role", "figure")
            stamp = hashlib.sha256(json.dumps([box, dpi, role]).encode()).hexdigest()[:16]
            asset = f"assets/p{page_number}-{role}-{stamp}.png"
            if _crop(rendered,box,dpi/72,output_dir/asset):
                formula = box in formula_regions
                if role == "inline-symbol":
                    from .inline_graphics import attach_inline_graphic
                    if attach_inline_graphic(blocks, region, asset, page_number):
                        continue
                blocks.append({"kind":"image","text":"公式原区域" if formula else "原页图像区域", "level":1,
                    "sources":[{"page":page_number,"bbox":box}],"confidence":None,"asset":asset,"rows":None,
                    "image_role":role, "image_detection":region.get("evidence"),
                    "issues":[_issue("formula_image_fallback" if formula else "figure_review","公式保留原区域图片，避免上下标乱序；未转换为 MathML。" if formula else "原区域图像保留；其中表格、公式或文字请对照原页。",page_number,box)],"_bbox":box})
        # Attach figures/tables relative to paragraph source coordinates, retaining two-column order.
        if dual:
            full = [b for b in blocks if b["_bbox"][0] < width*.46 and b["_bbox"][2] > width*.54]
            ordinary = [b for b in blocks if b not in full]
            ordered = []
            for divider in sorted(full,key=lambda b:b["_bbox"][1])+[None]:
                boundary = divider["_bbox"][1] if divider else math.inf
                band = [b for b in ordinary if b["_bbox"][1]<boundary]
                ordinary = [b for b in ordinary if b["_bbox"][1]>=boundary]
                ordered.extend(sorted(band,key=lambda b:(b["_bbox"][0]>=width/2,b["_bbox"][1])))
                if divider:
                    ordered.append(divider)
            blocks = ordered
        else:
            blocks.sort(key=lambda b:(round(b["_bbox"][1]/3),b["_bbox"][0]))
        for n,block in enumerate(blocks,1):
            block["id"] = f"p{page_number}-b{n}"
            block.pop("_bbox",None)
            issues.extend(block["issues"])
        provider = {"cuda": "CUDAExecutionProvider", "directml": "DmlExecutionProvider"}.get(execution["identity"]["kind"])
        device = ((provider + ("（已验证模型含 CPU 辅助算子）" if execution.get("cpu_auxiliary_nodes") else ""))
                  if provider else "CPUExecutionProvider" if use_ocr else "CPU（文字层）")
        return {"number":page_number,"width":width,"height":height,"status":"success","method":method,
                "device":device, "execution":execution, "blocks":blocks,"issues":issues,
                "layout_version":PAGE_LAYOUT_VERSION, "layout": {"columns": 2 if dual else 1, "graphics": scan_graphics},
                "raw":{"text":native_text,"native_lines":native_lines,"ocr_lines":ocr_lines,
                       "ocr_elapsed_seconds":elapsed,"text_layer_healthy":healthy,"text_layer_reasons":reasons,
                       "images":image_boxes,"rotation":page.rotation,"backend":BACKEND_VERSION,
                       "render_dpi":dpi if rendered is not None else None, "coordinate_system":"PDF points, top-left origin",
                       "execution":execution}}
