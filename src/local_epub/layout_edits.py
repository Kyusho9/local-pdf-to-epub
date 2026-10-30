"""Rebase saved text only when the original line sources and text match exactly.

Layout grouping may change block IDs. This is not fuzzy alignment: ambiguous,
overlapping, changed-OCR or kind-changing fragment edits stay as conflicts.
"""
from copy import deepcopy
from .inline_graphics import text_sha


def _source_key(source):
    return (source.get("page"), tuple(source.get("bbox") or ()))


def _range_for_edit(block, edit):
    text = block.get("text", "")
    if block.get("line_spans_text_sha256") != text_sha(text):
        return []
    expected = [_source_key(s) for s in edit.get("sources", [])]
    if not expected or any(not s[1] for s in expected):
        return []
    sources = [_source_key(s) for s in block.get("sources", [])]
    spans = block.get("line_spans", [])
    found = []
    for i in range(len(sources)-len(expected)+1):
        if sources[i:i+len(expected)] != expected:
            continue
        selected = [s for s in spans if i <= s.get("source_index", -1) < i+len(expected)]
        if len(selected) != len(expected) or [s["source_index"] for s in selected] != list(range(i, i+len(expected))):
            continue
        start, end = selected[0]["start"], selected[-1]["end"]
        if text[start:end] != edit.get("original_text"):
            continue
        whole = start == 0 and end == len(text)
        if not whole and ((edit.get("kind") and edit["kind"] != block.get("kind")) or edit.get("level") is not None):
            continue
        found.append((start, end, whole))
    return found


def rebase_layout_edits(blocks, edits, already_applied=()):
    """Apply unique exact matches in-place; return IDs and explanatory records."""
    candidates = []
    for bid, edit in edits.items():
        if bid in already_applied:
            continue
        matches = [(block, bounds) for block in blocks if not block.get("edited")
                   for bounds in _range_for_edit(block, edit)]
        if len(matches) == 1:
            block, bounds = matches[0]
            candidates.append((bid, edit, block, bounds))
    accepted = []
    for record in candidates:
        bid, edit, block, (start, end, whole) = record
        overlap = any(other[0] != bid and other[2] is block and max(start, other[3][0]) < min(end, other[3][1])
                      for other in candidates)
        if not overlap:
            accepted.append(record)
    applied, issues = set(), []
    for block in blocks:
        pending = [r for r in accepted if r[2] is block]
        if not pending:
            continue
        original = block.get("text", "")
        value = original
        for bid, edit, _, (start, end, whole) in sorted(pending, key=lambda r: r[3][0], reverse=True):
            value = value[:start] + edit["text"] + value[end:]
            if whole:
                block.update({k: edit[k] for k in ("kind", "level") if edit.get(k) is not None})
            applied.add(bid)
        block.update(text=value, automatic_text=original, edited=True,
                     rebased_edit_ids=[r[0] for r in pending])
        # Old offsets stay bound to their old text digest, so consumers cannot
        # accidentally use them as positions inside a revised paragraph.
        from .page_margins import source_spans_for_block
        block["source_spans"] = source_spans_for_block(block)
        block["source_spans_text_sha256"] = text_sha(value) if block["source_spans"] is not None else None
        issues.append({"code": "layout_edit_rebased", "message": "版面整理改变了段落分组；人工修订已按完全一致的原行来源与原文字迁入新段落。",
                       "blocks": [block["id"]], "saved_edit_ids": block["rebased_edit_ids"], "type": "processing", "severity": "info"})
    return applied, issues
