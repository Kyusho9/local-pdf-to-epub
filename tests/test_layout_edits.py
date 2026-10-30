"""Risk boundaries for old manual corrections after paragraph regrouping."""
from copy import deepcopy
from local_epub.inline_graphics import text_sha, joined_text_metadata
from local_epub.layout_edits import rebase_layout_edits


def paragraph():
    text = "原文第一行。原文第二行。原文第三行。"
    sources = [{"page": 3, "bbox": [40, y, 300, y+12], "polygon": [[40,y],[300,y],[300,y+12],[40,y+12]]}
               for y in (100, 120, 140)]
    return {"id": "p3-b1", "kind": "paragraph", "text": text, "sources": sources,
            "line_spans": [{"start": i*6, "end": (i+1)*6, "source_index": i} for i in range(3)],
            "line_spans_text_sha256": text_sha(text), "issues": []}


def correction(block, start=1, end=2, **kwargs):
    return {"original_text": block["text"][start*6:end*6], "text": "已校正的第二行。",
            "sources": [{k: s[k] for k in ("page", "bbox")} for s in block["sources"][start:end]],
            "kind": "paragraph", "level": None, **kwargs}


def test_old_line_edit_moves_to_new_paragraph_without_changing_saved_record():
    block = paragraph()
    edit = correction(block)
    before = deepcopy(edit)
    ids, issues = rebase_layout_edits([block], {"p3-b2": edit})
    assert ids == {"p3-b2"} and edit == before
    assert block["text"] == "原文第一行。已校正的第二行。原文第三行。"
    assert len(block["sources"]) == 3 and block["edited"]
    assert issues[0]["code"] == "layout_edit_rebased"
    assert block["source_spans"][0]["end"] == len(block["text"])
    assert block["line_spans_text_sha256"] != text_sha(block["text"])


def test_rebase_requires_unique_geometry_and_exact_text_not_matching_ids():
    block = paragraph(); edit = correction(block)
    changed = deepcopy(block); changed["sources"][1]["bbox"][0] += .1
    assert rebase_layout_edits([changed], {changed["id"]: edit})[0] == set()
    changed = deepcopy(block); changed["text"] = changed["text"].replace("第二行", "另二行")
    changed["line_spans_text_sha256"] = text_sha(changed["text"])
    assert rebase_layout_edits([changed], {changed["id"]: edit})[0] == set()
    assert rebase_layout_edits([block, deepcopy(block)], {"old": edit})[0] == set()


def test_overlap_and_kind_changes_remain_conflicts_but_disjoint_edits_work():
    block = paragraph()
    assert not rebase_layout_edits([block], {"a": correction(block, 0, 2), "b": correction(block)})[0]
    assert not rebase_layout_edits([block], {"a": correction(block, kind="heading")})[0]
    assert not rebase_layout_edits([block], {"a": correction(block, level=2)})[0]
    applied, _ = rebase_layout_edits([block], {"a": correction(block, 0, 1, text="甲"),
                                             "b": correction(block, 2, 3, text="丙")})
    assert applied == {"a", "b"} and block["text"] == "甲原文第二行。丙"


def test_merge_preserves_both_inline_images_and_exact_source_line_offsets():
    left, right = paragraph(), paragraph()
    left["inline_images"] = [{"asset": "a.png", "offset": 2, "text_sha256": text_sha(left["text"])}]
    right["inline_images"] = [{"asset": "b.png", "offset": 7, "text_sha256": text_sha(right["text"])}]
    result = joined_text_metadata(left, right, " ")
    assert [i["asset"] for i in result["inline_images"]] == ["a.png", "b.png"]
    assert [i["offset"] for i in result["inline_images"]] == [2, len(left["text"])+8]
    assert all(i["text_sha256"] == text_sha(left["text"]+" "+right["text"]) for i in result["inline_images"])
    assert result["line_spans"][3]["source_index"] == 3
    assert result["line_spans"][3]["start"] == len(left["text"])+1
    right["inline_images"][0]["text_sha256"] = "stale"
    ambiguous = joined_text_metadata(left, right, "")
    assert ambiguous["inline_images"][1]["asset"] == "b.png"
    assert ambiguous["inline_images"][1]["text_sha256"] == "stale"
