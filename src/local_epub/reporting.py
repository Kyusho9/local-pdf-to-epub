"""Human-readable review summaries, while retaining detailed machine evidence."""
import json


PROCESSING_CODES = {"text_layer_replaced", "image_text_reflow", "gpu_cpu_fallback",
                    "cover_original_pixels", "layout_edit_rebased", "layout_merge_preserved",
                    "cross_page_join", "layout_upgrade_available"}


def _key(issue):
    return json.dumps(issue, ensure_ascii=False, sort_keys=True)


def collect_issues(document, include_excluded=False):
    blocks = document.get("blocks", [])
    resolved = set()
    if not include_excluded:
        for block in blocks:
            for issue in block.get("issues", []):
                if block.get("exclude_from_export") or (
                    block.get("export_override") == "keep" and isinstance(issue, dict)
                    and issue.get("code") == "margin_content"
                ):
                    resolved.add(_key(issue))
    result, seen = [], set()
    candidates = list(document.get("issues", []))
    candidates.extend(i for b in blocks for i in b.get("issues", []))
    for issue in candidates:
        key = _key(issue)
        if key not in seen and key not in resolved:
            result.append(issue)
            seen.add(key)
    return result


def summarize_issues(document):
    """Group repeated messages by code and text, never by an invented score."""
    issues = collect_issues(document)
    for conflict in document.get("export_conflicts", []):
        issues.append({"code": "export_visibility_conflict", "message": conflict.get("reason", "正文保留/排除决定需要重新确认。"),
                       "pages": sorted({s["page"] for s in conflict.get("sources", []) if s.get("page")}),
                       "block_id": conflict.get("block_id"), "type": "review", "severity": "warning"})
    groups = {"review": {}, "processing": {}}
    for issue in issues:
        data = issue if isinstance(issue, dict) else {"message": str(issue)}
        code, message = data.get("code", ""), data.get("message") or data.get("code", "需核对原页")
        category = "processing" if code in PROCESSING_CODES else "review"
        item = groups[category].setdefault((code, message), {"code": code, "message": message, "pages": [], "count": 0})
        item["count"] += 1
        pages = data.get("pages") or ([data["page"]] if data.get("page") else [])
        item["pages"] = sorted(set(item["pages"]) | {p for p in pages if isinstance(p, int) and p > 0})
    excluded = []
    margin_omitted_count = 0
    for block in document.get("blocks", []):
        if not block.get("exclude_from_export"):
            continue
        if block.get("omission_reason") == "page_margin_option":
            margin_omitted_count += 1
            continue
        if block.get("omission_reason") == "page_margin_layout":
            # Retained in running-margin metadata, not an excluded body edit.
            continue
        decoration = block.get("decoration") or {}
        explicit = block.get("export_override") == "exclude"
        excluded.append({"id": block["id"], "text": block.get("text", ""), "sources": block.get("sources", []),
                         "kind": decoration.get("kind", "manual"), "decision": "manual" if explicit else "automatic",
                         "reason": "用户选择从正文排除" if explicit else decoration.get("reason", "识别为重复页边内容")})
    return {"review": list(groups["review"].values()), "processing": list(groups["processing"].values()),
            "excluded": excluded, "margin_omitted_count": margin_omitted_count, "issues": issues}


def format_issue(item):
    pages = item.get("pages", [])
    prefix = "第 " + "、".join(map(str, pages)) + " 页：" if pages else ""
    count = item.get("count", 1)
    suffix = f"（共 {count} 处）" if count > max(1, len(pages)) else ""
    return prefix + item.get("message", "需核对原页") + suffix
