"""Persistent single-job orchestrator. EPUB export never initializes OCR."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import shutil
import time
import uuid
from .storage import atomic_json, digest, job_lock, read_json

STAGE_VERSION = "parse-3-structure-2"
# Document assembly can evolve without invalidating expensive page OCR caches.
DOCUMENT_STAGE_VERSION = "document-scan-layout-5"


def load_job(job_dir):
    job = read_json(Path(job_dir) / "job.json")
    if not job or job.get("schema_version") != 1:
        raise ValueError("任务不存在或版本不兼容")
    return job


def _normalise_options(options=None):
    opts = {"mode": "auto", "dpi": 180, "ocr_device": "cpu", "gpu_fallback": True, **(options or {})}
    if opts["mode"] not in ("auto", "text", "ocr") or not 100 <= int(opts["dpi"]) <= 400:
        raise ValueError("模式或 DPI 无效（100–400）")
    if opts["ocr_device"] not in ("cpu", "directml", "cuda") or not isinstance(opts["gpu_fallback"], bool):
        raise ValueError("OCR 设备必须为 cpu、directml 或 cuda，CPU 回退选项必须为布尔值")
    return opts


def update_job_device(job_dir, ocr_device=None, gpu_fallback=None):
    """Change requested execution only; cache validation happens at resume."""
    root = Path(job_dir)
    with job_lock(root):
        job = load_job(root)
        options = _normalise_options(job["options"])
        if ocr_device is not None:
            options["ocr_device"] = ocr_device
        if gpu_fallback is not None:
            options["gpu_fallback"] = gpu_fallback
        job["options"] = _normalise_options(options)
        atomic_json(root / "job.json", job)
        return job


def create_job(source, job_dir, options=None, password=None):
    from .backend import inspect_pdf
    source, root = Path(source).resolve(), Path(job_dir).resolve()
    if not source.is_file():
        raise ValueError("找不到输入 PDF")
    signature = digest(source)
    opts = _normalise_options(options)
    if (root / "job.json").exists():
        job = load_job(root)
        if job["source"]["sha256"] != signature:
            raise ValueError("任务目录属于另一份 PDF（内容摘要不同）；请选择新目录。")
        if _normalise_options(job["options"]) != opts:
            with job_lock(root):
                job["options"] = opts
                for page in job["pages"]:
                    if page["status"] != "excluded":
                        page["status"] = "pending"
                job["status"] = "pending"
                atomic_json(root / "job.json", job)
        return job
    if root.exists() and any(root.iterdir()):
        raise ValueError("新任务目录必须为空，避免覆盖现有文件。")
    info = inspect_pdf(str(source), password=password)
    root.mkdir(parents=True, exist_ok=True)
    for name in ("pages", "raw", "assets", "previews", "history"):
        (root / name).mkdir(exist_ok=True)
    shutil.copy2(source, root / "source.pdf")
    if digest(root / "source.pdf") != signature:
        raise ValueError("复制期间源文件发生变化，请重新导入")
    job = {"schema_version": 1, "id": str(uuid.uuid4()), "created_at": time.time(), "source": {"path": str(source), "snapshot": "source.pdf", "sha256": signature}, "options": opts, "metadata": info.get("metadata", {}), "toc": info.get("toc", []), "permissions": info.get("permissions"), "pages": [{"number": n, "status": "pending", "error": None, "cache_key": None} for n in range(1, info["page_count"] + 1)], "status": "pending", "issues": [], "device": "CPU / ONNX Runtime CPUExecutionProvider"}
    atomic_json(root / "job.json", job)
    atomic_json(root / "edits.json", {"version": 1, "blocks": {}, "history": []})
    return job


def _read_valid_page(root, number):
    try:
        page = read_json(Path(root) / "pages" / f"{number:04d}.json")
        if not isinstance(page, dict) or page.get("number") != number or page.get("status") != "success" or not isinstance(page.get("blocks"), list):
            return None
        for block in page["blocks"]:
            if not isinstance(block, dict) or not isinstance(block.get("id"), str) or block.get("kind") not in ("paragraph", "heading", "footnote", "image", "table") or not isinstance(block.get("sources"), list):
                return None
        return page
    except (ValueError, OSError, TypeError):
        return None


def _cache_key(job, resources, execution_identity=None):
    from .backend import engine_versions
    options = dict(job["options"])
    if options.get("ocr_device", "cpu") == "cpu":
        # Keep the exact pre-CUDA CPU fingerprint. Installing an optional
        # accelerator must not invalidate existing CPU OCR checkpoints.
        options.pop("ocr_device", None)
        options.pop("gpu_fallback", None)
    payload = {"source": job["source"]["sha256"], "options": options, "engines": engine_versions(), "models": resources.get("manifest"), "stage": STAGE_VERSION}
    if job["options"].get("ocr_device", "cpu") in ("directml", "cuda"):
        payload["execution"] = execution_identity
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _execution_summary(job):
    counts = {"native": 0, "cpu": 0, "directml": 0, "cuda": 0}
    fallback_pages, identities = [], []
    for page in job["pages"]:
        if page["status"] != "success":
            continue
        execution = page.get("execution", {})
        identity = execution.get("identity", {})
        kind = identity.get("kind", "native" if page.get("method") == "text" else "cpu")
        counts[kind] = counts.get(kind, 0) + 1
        if execution.get("fallback_reason"):
            fallback_pages.append({"page": page["number"], "reason": execution["fallback_reason"]})
        if identity and identity not in identities:
            identities.append(identity)
    labels = [f"DirectML OCR {counts['directml']} 页" if counts["directml"] else "",
              f"CUDA OCR {counts['cuda']} 页" if counts["cuda"] else "",
              f"CPU OCR {counts['cpu']} 页" if counts["cpu"] else "",
              f"CPU 文字层 {counts['native']} 页" if counts["native"] else ""]
    job["device"] = " + ".join(x for x in labels if x) or "尚无完成页面；未确认执行设备"
    job["execution_summary"] = {"requested_device": job["options"].get("ocr_device", "cpu"),
                                "counts": counts, "fallback_pages": fallback_pages,
                                "identities": identities}


def run_job(job_dir, resource_dir, password=None, retry_pages=None, progress=None, cancel=None, gpu_runtime_dir=None):
    from .backend import extract_page
    from .gpu_execution import OnnxExecutor, OCRCancelled
    from .resources import check_resources
    root = Path(job_dir)
    emit = progress or (lambda event: None)
    with job_lock(root):
        job = load_job(root)
        job["options"] = _normalise_options(job["options"])
        if digest(root / "source.pdf") != job["source"]["sha256"]:
            raise ValueError("任务内源 PDF 摘要不匹配，拒绝复用缓存。")
        resource_state = check_resources(resource_dir)
        retries = set(retry_pages or [])
        if not retries.issubset({p["number"] for p in job["pages"]}):
            raise ValueError("重试页码超出范围")
        job["status"] = "running"
        job["resources"] = {"path": str(Path(resource_dir).resolve()), "manifest": resource_state["manifest"]}
        if job["options"]["ocr_device"] in ("directml", "cuda"):
            from .gpu_resources import default_gpu_runtime_dir
            backend = job["options"]["ocr_device"]
            prior_runtime = job.get("gpu_runtime", {})
            prior_path = prior_runtime.get("path") if prior_runtime.get("backend", "cuda") == backend else None
            gpu_runtime_dir = gpu_runtime_dir or prior_path or default_gpu_runtime_dir(backend)
            job["gpu_runtime"] = {"path": str(Path(gpu_runtime_dir).expanduser().resolve()), "backend": backend}
        for page in job["pages"]:
            if page["status"] == "processing":
                page["status"] = "pending"
        atomic_json(root / "job.json", job)
        total = len(job["pages"])
        executor = (OnnxExecutor(resource_dir, root, gpu_runtime_dir, backend=job["options"]["ocr_device"],
                                  fallback=job["options"]["gpu_fallback"], progress=emit, cancel=cancel)
                    if job["options"]["ocr_device"] in ("directml", "cuda") else None)
        def expected_key(state, stored=None):
            if executor is None:
                return _cache_key(job, resource_state)
            stored = stored or _read_valid_page(root, state["number"])
            previous_identity = (stored or {}).get("execution", {}).get("identity", {})
            planned = {"kind": "native"} if previous_identity.get("kind") == "native" else executor.plan_identity()
            return _cache_key(job, resource_state, planned)
        try:
            if executor:
                executor.prepare()
                if executor.identity:
                    job["gpu_runtime"]["manifest_sha256"] = executor.identity["runtime_manifest_sha256"]
            # Invalidate all affected pages, even for a selected-page retry.
            for page in job["pages"]:
                if page["status"] == "success" and page.get("cache_key") != expected_key(page):
                    page["status"] = "pending"
            atomic_json(root / "job.json", job)
            for page in job["pages"]:
                n = page["number"]
                if cancel and cancel():
                    raise OCRCancelled("任务已取消")
                if page["status"] == "excluded" and n not in retries:
                    continue
                if retry_pages is not None and n not in retries:
                    continue
                dest = root / "pages" / f"{n:04d}.json"
                raw_dest = root / "raw" / f"{n:04d}.json"
                stored = _read_valid_page(root, n)
                key = expected_key(page, stored)
                if page["status"] == "success" and page.get("cache_key") == key and stored is not None and n not in retries:
                    if stored.get("execution"):
                        page["execution"] = stored["execution"]
                    elif stored.get("device") == "CPUExecutionProvider":
                        # Older CPU-only pages can have method='text' after an
                        # OCR comparison found no extra lines. Preserve the
                        # recorded fact that OCR really ran, without rewriting
                        # their page/raw files or invalidating their cache.
                        page["execution"] = {"identity": {"kind": "cpu"}, "verified": True,
                                             "evidence": "legacy-CPU-only-backend-record"}
                    emit({"event": "progress", "stage": "cache", "page": n, "total": total, "message": f"第 {n} 页复用校验通过的缓存"})
                    continue
                page.update(status="processing", error=None)
                atomic_json(root / "job.json", job)
                plan = (f"{executor.label} OCR 计划；完成后核实实际设备" if executor and executor.identity
                        else f"{executor.label} 不可用，严格模式" if executor and not executor.fallback else "CPU")
                emit({"event": "progress", "stage": "parse", "page": n, "total": total, "message": f"正在处理第 {n}/{total} 页（{plan}）"})
                try:
                    result = extract_page(str(root / "source.pdf"), n, str(root), job["options"], str(resource_dir), password=password, ocr_executor=executor)
                    stamp = f"{n:04d}-{time.time_ns()}"
                    for old in (dest, raw_dest):
                        if old.exists():
                            shutil.copy2(old, root / "history" / f"{old.parent.name}-{stamp}.json")
                    atomic_json(raw_dest, result.get("raw", {}))
                    result.pop("raw", None)
                    key = _cache_key(job, resource_state, result.get("execution", {}).get("identity"))
                    result["cache_key"] = key
                    atomic_json(dest, result)
                    page.update(status="success", error=None, cache_key=key, method=result.get("method"), execution=result.get("execution"))
                    if executor:
                        execution = result["execution"]
                        emit({"event": "progress", "stage": "device", "page": n, "total": total,
                              "message": f"第 {n} 页实际完成：{result['device']}", "requested_device": executor.backend,
                              "actual_device": execution["identity"]["kind"], "fallback_reason": execution.get("fallback_reason")})
                except OCRCancelled:
                    page.update(status="pending", error=None)
                    raise
                except Exception as e:
                    page.update(status="failed", error=f"{type(e).__name__}: {e}")
                    emit({"event": "progress", "stage": "page_error", "page": n, "total": total, "message": page["error"]})
                atomic_json(root / "job.json", job)
        except OCRCancelled:
            job["status"] = "cancelled"
        finally:
            if executor:
                executor.close()
        if job["status"] != "cancelled":
            job["status"] = "complete" if all(p["status"] == "success" for p in job["pages"]) else "incomplete"
        job["updated_at"] = time.time()
        _execution_summary(job)
        atomic_json(root / "job.json", job)
        document = build_document(root)
        atomic_json(root / "document.json", document)
        emit({"event": "progress", "stage": "structure", "page": sum(p["status"] == "success" for p in job["pages"]), "total": total, "message": "中间结果已保存；" + ("所有页面已处理" if job["status"] == "complete" else "存在未完成或排除页面")})
        return job


def _rebuild_one(root, job, state, password=None):
    """Re-run geometry/graphics only; never resolve or initialize OCR resources."""
    from .backend import extract_page, PAGE_LAYOUT_VERSION
    number = state["number"]
    previous = _read_valid_page(root, number)
    raw_path = root / "raw" / f"{number:04d}.json"
    raw = read_json(raw_path)
    if previous is None or not isinstance(raw, dict) or not isinstance(raw.get("ocr_lines"), list):
        raise ValueError(f"第 {number} 页缺少可用的原始识别检查点，不能只整理版面；请重新识别该页。")
    # Preserve the original recognition DPI/mode. A different desired device or
    # DPI belongs to a real retry, not to a cached-layout operation.
    options = dict(job["options"])
    if raw.get("render_dpi"):
        options["dpi"] = raw["render_dpi"]
    options["toc_titles"] = [t.get("title", "") for t in job.get("toc", []) if t.get("page") == number]
    result = extract_page(str(root / "source.pdf"), number, str(root), options, None,
                          password=password, cached_ocr=raw)
    result.pop("raw", None)
    result["cache_key"] = previous.get("cache_key", state.get("cache_key"))
    result["layout_version"] = PAGE_LAYOUT_VERSION
    result["layout_rebuilt_from_raw_sha256"] = digest(raw_path)
    return result


def rebuild_layout(job_dir, password=None, pages=None, progress=None, cancel=None):
    """Checkpoint a selected layout rebuild without changing raw OCR or edits."""
    from .gpu_execution import OCRCancelled
    root = Path(job_dir)
    emit = progress or (lambda event: None)
    with job_lock(root):
        job = load_job(root)
        if digest(root / "source.pdf") != job["source"]["sha256"]:
            raise ValueError("任务内源 PDF 摘要不匹配，拒绝复用识别结果。")
        valid = {p["number"] for p in job["pages"]}
        requested = set(pages) if pages is not None else {p["number"] for p in job["pages"] if p["status"] == "success" or p.get("layout_rebuild_failed")}
        if not requested or not requested.issubset(valid):
            raise ValueError("没有可重新整理的页面，或页码超出范围。")
        for state in job["pages"]:
            if state["number"] in requested and state["status"] != "success" and not state.get("layout_rebuild_failed"):
                raise ValueError(f"第 {state['number']} 页没有成功识别结果；请先重新识别，不能仅整理版面。")
        job["status"] = "running"
        atomic_json(root / "job.json", job)
        rebuilt = []
        try:
            for state in job["pages"]:
                if state["number"] not in requested:
                    continue
                if cancel and cancel():
                    raise OCRCancelled("版面整理已取消")
                number = state["number"]
                emit({"event": "progress", "stage": "layout", "page": number, "total": len(job["pages"]),
                      "message": f"第 {number} 页：复用原始识别，重新整理段落、图片和页边（不运行 OCR）"})
                try:
                    result = _rebuild_one(root, job, state, password)
                    if cancel and cancel():
                        raise OCRCancelled("版面整理已取消")
                    dest = root / "pages" / f"{number:04d}.json"
                    history = root / "history"
                    history.mkdir(exist_ok=True)
                    shutil.copy2(dest, history / f"pages-layout-{number:04d}-{time.time_ns()}.json")
                    atomic_json(dest, result)
                    state.update(status="success", error=None, method=result["method"],
                                 execution=result["execution"], layout_version=result["layout_version"])
                    state.pop("layout_rebuild_failed", None)
                    rebuilt.append(number)
                except OCRCancelled:
                    raise
                except Exception as exc:
                    state.update(status="failed", layout_rebuild_failed=True,
                                 error=f"版面整理未完成，原始识别与旧页面仍保留，可重新整理此页：{type(exc).__name__}: {exc}")
                    emit({"event": "progress", "stage": "page_error", "page": number, "message": state["error"]})
                atomic_json(root / "job.json", job)
        except OCRCancelled:
            job["status"] = "cancelled"
        if job["status"] != "cancelled":
            job["status"] = "complete" if all(p["status"] == "success" for p in job["pages"]) else "incomplete"
        job["layout_rebuild"] = {"requested_pages": sorted(requested), "rebuilt_pages": rebuilt,
                                 "raw_ocr_unchanged": True, "ocr_initialized": False, "updated_at": time.time()}
        job["updated_at"] = time.time()
        _execution_summary(job)
        atomic_json(root / "job.json", job)
        atomic_json(root / "document.json", build_document(root))
        return job


def _signature(block):
    data = {k: block.get(k) for k in ("text", "sources", "kind", "rows", "asset")}
    # Added geometry evidence must not invalidate an unchanged older edit.
    # Source identity remains the original page and exact original bbox.
    if isinstance(data["sources"], list):
        data["sources"] = [{"page": v.get("page"), "bbox": v.get("bbox")} for v in data["sources"]]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _attach_source_spans(block):
    from .page_margins import source_spans_for_block, text_digest
    spans = source_spans_for_block(block)
    block["source_spans"] = spans
    if spans is None:
        block.pop("source_spans_text_sha256", None)
    else:
        block["source_spans_text_sha256"] = text_digest(block.get("text", ""))
    return block


def _joined_source_spans(first, second, separator):
    """Join known ranges; the inserted separator belongs to the left range.

    This is a deterministic structure operation, not text alignment by guesses.
    An already ambiguous operand makes the combined range map ambiguous too.
    """
    from .page_margins import source_spans_for_block
    left, right = source_spans_for_block(first), source_spans_for_block(second)
    if left is None or right is None:
        return None
    offset = len(first.get("text", "")) + len(separator)
    left[-1]["end"] += len(separator)
    if separator:
        left[-1]["join_separator_after"] = separator
    for span in right:
        span["start"] += offset
        span["end"] += offset
    return left + right


def _assemble(root, job):
    blocks, pages, issues = [], [], []
    for state in job["pages"]:
        if state["status"] != "success":
            continue
        page = _read_valid_page(root, state["number"])
        if not page:
            continue
        pages.append(page)
        for block in deepcopy(page.get("blocks", [])):
            block.setdefault("sources", [{"page": state["number"], "bbox": None}])
            block.setdefault("confidence", None)
            block.setdefault("issues", [])
            _attach_source_spans(block)
            blocks.append(block)
        issues.extend(page.get("issues", []))
    # Bookmarks are authoritative labels, inserted without replacing extracted text.
    for index, entry in reversed(list(enumerate(job.get("toc", [])))):
        page_n = entry.get("page")
        matching = [i for i, b in enumerate(blocks) if b["sources"] and b["sources"][0]["page"] == page_n]
        if not matching:
            continue
        title = entry.get("title", "").strip()
        if not title:
            continue
        exact = next((blocks[i] for i in matching if blocks[i].get("text", "").replace(" ", "") == title.replace(" ", "")), None)
        if exact:
            exact.update(kind="heading", level=max(1, min(6, entry.get("level", 1))))
        else:
            blocks.insert(matching[0], {"id": f"bookmark-{index}", "kind": "heading", "text": title, "level": max(1, min(6, entry.get("level", 1))), "sources": [{"page": page_n, "bbox": None}], "confidence": None, "origin": "pdf_bookmark", "issues": []})
            issues.append({"code": "bookmark_position_unknown", "message": "书签标题未匹配到正文位置，保留在来源页开头；同页书签不据此强制分章，请核对标题位置。", "page": page_n, "bbox": None, "type": "heuristic", "severity": "warning"})
    # Human structure/text choices have priority over a new layout heuristic.
    edits = read_json(root / "edits.json", {})
    protected_pages = {source.get("page") for group in (edits.get("blocks", {}), edits.get("visibility", {}))
                       for record in group.values() for source in record.get("sources", [])}
    import re
    for merge in edits.get("merges", []):
        for field in ("first", "second"):
            match = re.match(r"p(\d+)-", merge.get(field, ""))
            if match:
                protected_pages.add(int(match.group(1)))
    from .cross_page_paragraphs import join_cross_page_paragraphs
    blocks, joins = join_cross_page_paragraphs(blocks, pages, protected_pages)
    issues.extend(joins)
    # Boundaries without sufficient geometry stay separate and are review items.
    previous = None
    for b in blocks:
        if any(isinstance(i, dict) and i.get("code") == "margin_content" for i in b.get("issues", [])):
            continue
        if b.get("kind") == "paragraph" and previous and previous.get("kind") == "paragraph":
            p, n = previous["sources"][-1]["page"], b["sources"][0]["page"]
            text = previous.get("text", "").rstrip()
            if n == p + 1 and text and text[-1] not in "。！？.!?：:；;”’\"'":
                issue = {"code": "cross_page_candidate", "message": "可能是跨页段落；保留原文，请校对后合并。", "pages": [p, n], "blocks": [previous["id"], b["id"]]}
                issues.append(issue)
                previous["issues"].append(issue)
        previous = b
    edits = read_json(root / "edits.json", {})
    for merge in edits.get("merges", []):
        first = next((b for b in blocks if b["id"] == merge["first"]), None)
        second = next((b for b in blocks if b["id"] == merge["second"]), None)
        if not first or not second or _signature(first) != merge["first_signature"] or _signature(second) != merge["second_signature"]:
            issues.append({"code": "merge_conflict", "message": "原块变化，人工合并未应用；结构操作与修订均保留。", "blocks": [merge["first"], merge["second"]]})
            continue
        spans = _joined_source_spans(first, second, merge["separator"])
        from .inline_graphics import joined_text_metadata
        first.update(joined_text_metadata(first, second, merge["separator"]))
        first["text"] = first.get("text", "") + merge["separator"] + second.get("text", "")
        first["sources"] += second["sources"]
        first["source_block_ids"] = first.get("source_block_ids", [first["id"]]) + second.get("source_block_ids", [second["id"]])
        first["confidence"] = None  # no fabricated aggregate confidence
        first["issues"].extend(second.get("issues", []))
        from .page_margins import text_digest
        first["source_spans"] = spans
        first["source_spans_text_sha256"] = text_digest(first["text"]) if spans is not None else None
        blocks.remove(second)
    for block in blocks:
        _attach_source_spans(block)
    return blocks, pages, issues


def build_document(job_dir, apply_edits=True):
    root = Path(job_dir)
    job = load_job(root)
    blocks, pages, issues = _assemble(root, job)
    from .backend import PAGE_LAYOUT_VERSION
    layout_upgrade_pages = [p["number"] for p in pages if p.get("layout_version") != PAGE_LAYOUT_VERSION]
    if layout_upgrade_pages:
        issues.append({"code": "layout_upgrade_available", "message": "这些页仍使用旧版段落与图片布局。可在校对页点击「重新整理版面（复用识别）」升级，无需重跑 OCR。",
                       "pages": layout_upgrade_pages, "type": "processing", "severity": "info"})
    edits = read_json(root / "edits.json", {"blocks": {}})
    conflicts, matched = [], set()
    if apply_edits:
        saved_edits = edits.get("blocks", {})
        for b in blocks:
            edit = saved_edits.get(b["id"])
            if edit and edit["base_signature"] == _signature(b):
                matched.add(b["id"])
                b["automatic_text"] = b.get("text", "")
                b.update({k: edit[k] for k in ("text", "kind", "level") if edit.get(k) is not None})
                b["edited"] = True
                if "source_spans" in edit:
                    b["source_spans"] = deepcopy(edit["source_spans"])
                    b["source_spans_text_sha256"] = edit.get("source_spans_text_sha256")
                _attach_source_spans(b)
        from .layout_edits import rebase_layout_edits
        rebased, notes = rebase_layout_edits(blocks, saved_edits, matched)
        matched.update(rebased)
        issues.extend(notes)
        # A saved manual merge's exact resulting text AND all original sources
        # now inside one new paragraph prove that grouping is already present.
        # The saved operation is retained in edits.json, not silently discarded.
        for issue in issues[:]:
            if issue.get("code") == "merge_conflict" and issue.get("blocks", [None])[0] in rebased:
                first = issue["blocks"][0]
                edit = saved_edits[first]
                proof = {"text": edit["original_text"], "sources": edit["sources"], "kind": edit.get("kind"), "rows": None, "asset": None}
                if edit.get("base_signature") == _signature(proof):
                    issues.remove(issue)
                    issues.append({"code": "layout_merge_preserved", "message": "旧人工合并的全部原行与文字已在新段落中连续保留，原合并记录仍保留。", "blocks": issue["blocks"], "type": "processing", "severity": "info"})
        for bid, edit in saved_edits.items():
            if bid not in matched:
                conflicts.append({"block_id": bid, "reason": "重新识别或版面整理后，未能唯一、完整匹配原文与来源；修订仍保留，需人工重新确认", "edit": edit})
    from .inline_graphics import text_sha
    for block in blocks:
        if any(image.get("text_sha256") != text_sha(block.get("text", "")) for image in block.get("inline_images", [])):
            issue = {"code": "inline_graphic_anchor_conflict", "message": "正文经过人工修订，行内图标的原字符位置不再可靠；原图保留在该段末尾，请校对位置。", "blocks": [block["id"]], "type": "heuristic", "severity": "warning"}
            block.setdefault("issues", []).append(issue)
            issues.append(issue)
    from .margins import annotate_margins
    annotate_margins(blocks, pages)
    visibility = edits.get("visibility", {}) if apply_edits else {}
    export_conflicts, visibility_matched = [], set()
    # A text edit's signature refers to the automatic draft, not the edited text.
    automatic_signatures = {}
    if visibility:
        automatic_blocks, _, _ = _assemble(root, job)
        automatic_signatures = {b["id"]: _signature(b) for b in automatic_blocks}
    for block in blocks:
        block["export_override"] = "auto"
        block["exclude_from_export"] = False
        choice = visibility.get(block["id"])
        if not choice:
            continue
        visibility_matched.add(block["id"])
        if choice.get("decision") in ("keep", "exclude") and choice.get("base_signature") == automatic_signatures.get(block["id"]):
            block["export_override"] = choice["decision"]
            block["exclude_from_export"] = choice["decision"] == "exclude"
        else:
            # Changed OCR/layout must never inherit an old exclusion by ID alone.
            block["exclude_from_export"] = False
            block["export_conflict"] = True
            export_conflicts.append({"block_id": block["id"], "reason": "原文或来源变化，旧的正文保留/排除决定未应用；当前内容保留，请重新确认。", **choice})
    for bid, choice in visibility.items():
        if bid not in visibility_matched:
            export_conflicts.append({"block_id": bid, "reason": "原块暂不存在；正文保留/排除决定仍保留，尚未应用。", **choice})
    missing = []
    valid_numbers = {p["number"] for p in pages}
    for p in job["pages"]:
        if p["status"] != "success" or p["number"] not in valid_numbers:
            broken = p["status"] == "success"
            missing.append({"number": p["number"], "status": "failed" if broken else p["status"], "error": "页面检查点缺失或结构损坏；请继续任务或重试此页。" if broken else p.get("error")})
    document = {"schema_version": 1, "document_stage_version": DOCUMENT_STAGE_VERSION, "id": job["id"], "metadata": job["metadata"], "toc": job["toc"], "pages": pages, "blocks": blocks, "issues": issues, "missing_pages": missing, "edit_conflicts": conflicts, "export_conflicts": export_conflicts, "device": job["device"], "execution_summary": job.get("execution_summary"), "source_sha256": job["source"]["sha256"]}
    document["layout_stage_version"] = PAGE_LAYOUT_VERSION
    document["layout_upgrade_pages"] = layout_upgrade_pages
    from .page_margins import annotate_page_margins
    annotate_page_margins(document)
    # Old per-block margin choices remain in edits.json as history. They must
    # not override the new global page-edge output policy or erase corrections.
    for block in blocks:
        if block.get("page_margin_role"):
            if block.get("export_override") != "auto":
                block["legacy_margin_choice"] = block["export_override"]
            block["exclude_from_export"] = False
    from .page_number_repairs import attach_repair_fingerprints, apply_repairs
    repairs = edits.get("page_number_repairs", {}) if apply_edits else {}
    if repairs:
        attach_repair_fingerprints(document, root)
    apply_repairs(document, repairs)
    return document


def set_export_override(job_dir, block_id, value):
    """Reversible, source-bound visibility decisions; never delete original text."""
    if value not in ("auto", "keep", "exclude"):
        raise ValueError("正文处理必须为自动、保留或排除")
    root = Path(job_dir)
    with job_lock(root):
        automatic = build_document(root, apply_edits=False)
        block = next((b for b in automatic["blocks"] if b["id"] == block_id), None)
        if block is None:
            raise ValueError("内容块已不存在；原有决定仍保留，请重新打开任务核对")
        edits = read_json(root / "edits.json", {"version": 1, "blocks": {}, "history": []})
        choices = edits.setdefault("visibility", {})
        if block_id in choices:
            edits.setdefault("visibility_history", []).append({"block_id": block_id, **choices[block_id]})
        if value == "auto":
            choices.pop(block_id, None)
        else:
            choices[block_id] = {"decision": value, "base_signature": _signature(block), "original_text": block.get("text", ""), "sources": deepcopy(block["sources"]), "saved_at": time.time()}
        atomic_json(root / "edits.json", edits)
        atomic_json(root / "document.json", build_document(root))


def save_edit(job_dir, block_id, text, kind=None, level=None):
    root = Path(job_dir)
    with job_lock(root):
        document = build_document(root, apply_edits=False)
        block = next((b for b in document["blocks"] if b["id"] == block_id), None)
        if block is None:
            raise ValueError("内容块已不存在，原修订保留在 edits.json")
        if kind and kind not in ("paragraph", "heading", "footnote", "image", "table"):
            raise ValueError("无效块类型")
        # A kind/level-only save must keep a previous deterministic merge map.
        # Arbitrary edits to a multi-page string have no reliable new boundary.
        visible = next((b for b in build_document(root)["blocks"] if b["id"] == block_id), None)
        mapped = deepcopy(visible if visible and visible.get("text") == text else block)
        mapped.update(text=text, edited=True)
        _attach_source_spans(mapped)
        edits = read_json(root / "edits.json", {"version": 1, "blocks": {}, "history": []})
        if block_id in edits["blocks"]:
            edits["history"].append({"block_id": block_id, **edits["blocks"][block_id]})
        edits["blocks"][block_id] = {"base_signature": _signature(block), "original_text": block.get("text", ""), "sources": block["sources"], "text": text, "kind": kind, "level": max(1, min(6, int(level))) if level else None, "saved_at": time.time()}
        edits["blocks"][block_id].update(source_spans=mapped["source_spans"],
                                         source_spans_text_sha256=mapped.get("source_spans_text_sha256"))
        atomic_json(root / "edits.json", edits)
        atomic_json(root / "document.json", build_document(root))


def exclude_pages(job_dir, pages):
    root = Path(job_dir)
    with job_lock(root):
        job = load_job(root)
        valid = {p["number"] for p in job["pages"]}
        if not set(pages).issubset(valid):
            raise ValueError("排除页码超出范围")
        for p in job["pages"]:
            if p["number"] in pages:
                p.update(status="excluded", error="用户主动排除；导出时附缺失说明")
        job["status"] = "incomplete"
        atomic_json(root / "job.json", job)


def export_job(job_dir, output, title=None, author=None, language="zh-CN", allow_partial=False, font_size=1.0, line_height=1.8, epubcheck=None, retain_page_margins=False, include_page_info=None):
    from .epub import write_epub
    from .page_margins import export_document, require_margin_support
    if include_page_info:
        raise ValueError("原页信息附录已移除；不再支持 --include-page-info。真正页眉页脚保留尚无已验证的阅读器方案。")
    require_margin_support(retain_page_margins)
    root = Path(job_dir)
    with job_lock(root):
        job = load_job(root)
        if digest(root / "source.pdf") != job["source"]["sha256"]:
            raise ValueError("任务内源 PDF 摘要不匹配，拒绝导出；请重新导入原文件。")
        document = build_document(root)
        if document["missing_pages"] and not allow_partial:
            raise ValueError("存在失败、排除或未处理页；请重试，或明确选择允许导出部分结果。")
        if not any(not b.get("exclude_from_export") for b in export_document(document)["blocks"]):
            raise ValueError("没有可导出的正文内容块；请检查原页与历史人工修订。")
        if title is not None:
            document["metadata"]["title"] = title
        if author is not None:
            document["metadata"]["author"] = author
        document["metadata"]["language"] = language
        job["metadata"] = deepcopy(document["metadata"])
        if "include_page_info" in job.get("export_options", {}):
            job.setdefault("export_options_history", []).append(deepcopy(job["export_options"]))
        job["export_options"] = {"schema_version": 2, "font_size": font_size, "line_height": line_height, "language": language, "retain_page_margins": bool(retain_page_margins)}
        atomic_json(root / "job.json", job)
        atomic_json(root / "document.json", document)
        report = write_epub(document, root, output, font_size, line_height, epubcheck, retain_page_margins=retain_page_margins)
        atomic_json(root / "last-export.json", report)
        return report


def merge_blocks(job_dir, first_id, second_id):
    """Explicit human structure correction with all sources and prior revisions retained."""
    root = Path(job_dir)
    with job_lock(root):
        automatic = build_document(root, apply_edits=False)
        visible = build_document(root)
        blocks = automatic["blocks"]
        first = next((b for b in blocks if b["id"] == first_id), None)
        second = next((b for b in blocks if b["id"] == second_id), None)
        if not first or not second or first["kind"] != "paragraph" or second["kind"] != "paragraph":
            raise ValueError("仅可合并存在的两个正文段落")
        a, b = blocks.index(first), blocks.index(second)
        if b <= a:
            raise ValueError("第二段必须位于第一段之后")
        between = blocks[a+1:b]
        if any(not any(isinstance(i, dict) and i.get("code") == "margin_content" for i in block.get("issues", [])) for block in between):
            raise ValueError("两段之间存在标题、插图或其他正文，请先核对阅读顺序")
        v1 = next(b for b in visible["blocks"] if b["id"] == first_id)
        v2 = next(b for b in visible["blocks"] if b["id"] == second_id)
        sep = " " if first.get("text", "")[-1:].isascii() and second.get("text", "")[:1].isascii() else ""
        edits = read_json(root / "edits.json")
        edits.setdefault("merges", []).append({"first": first_id, "second": second_id, "first_signature": _signature(first), "second_signature": _signature(second), "separator": sep, "saved_at": time.time()})
        for bid in (first_id, second_id):
            if bid in edits["blocks"]:
                edits["history"].append({"block_id": bid, **edits["blocks"].pop(bid)})
        merged = deepcopy(first)
        merged["text"] += sep + second.get("text", "")
        merged["sources"] += second["sources"]
        edits["blocks"][first_id] = {"base_signature": _signature(merged), "original_text": merged["text"], "text": v1.get("text", "") + sep + v2.get("text", ""), "sources": merged["sources"], "kind": "paragraph", "level": None, "saved_at": time.time()}
        from .page_margins import text_digest
        spans = _joined_source_spans(v1, v2, sep)
        edits["blocks"][first_id].update(source_spans=spans,
                                         source_spans_text_sha256=text_digest(edits["blocks"][first_id]["text"]) if spans is not None else None)
        atomic_json(root / "edits.json", edits)
        atomic_json(root / "document.json", build_document(root))
