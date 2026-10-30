"""Source-page metadata tests; small IR fixtures are not OCR accuracy evidence."""
from copy import deepcopy

from local_epub.page_info import build_page_info


def _block(number, suffix, text, bbox, *, margin=False, **extra):
    return {"id": f"p{number}-{suffix}", "kind": "paragraph", "text": text,
            "sources": [{"page": number, "bbox": bbox}], "confidence": None,
            "issues": ([{"code": "margin_content", "message": "可能是页边内容，保留待核对。",
                         "page": number, "bbox": bbox}] if margin else []), **extra}


def _document():
    pages = []
    for number in (1, 2, 3):
        blocks = [
            _block(number, "header", "相同的源页眉", [40, 20, 150, 29], margin=True),
            _block(number, "body1", "正常正文第一段", [40, 100, 300, 113]),
            _block(number, "body2", "正常正文第二段", [40, 130, 300, 143]),
            _block(number, "footer", "源文档页脚", [40, 565, 120, 574], margin=True),
        ]
        # Deliberately no recognized printed page number on page 3.
        if number < 3:
            blocks.append(_block(number, "number", str(number), [200, 565, 210, 574], margin=True))
        pages.append({"number": number, "status": "success", "width": 420, "height": 595, "blocks": blocks})
    return {"pages": pages, "blocks": deepcopy([block for page in pages for block in page["blocks"]]),
            "missing_pages": []}


def _effective(document, suffix, number=1):
    return next(block for block in document["blocks"] if block["id"] == f"p{number}-{suffix}")


def test_classifies_source_matter_without_including_manual_body_exclusion():
    document = _document()
    _effective(document, "body1").update(exclude_from_export=True, export_override="exclude")
    original = deepcopy(document)
    info = build_page_info(document)
    assert document == original
    assert [page["number"] for page in info] == [1, 2, 3]
    assert info[0]["headers"][0]["text"] == "相同的源页眉"
    assert info[0]["footers"][0]["text"] == "源文档页脚"
    assert info[0]["page_numbers"][0]["text"] == "1"
    assert info[1]["page_numbers"][0]["text"] == "2"
    assert info[2]["page_numbers"] == []
    assert all(not page["unclassified"] for page in info)
    assert all(item["id"] != "p1-body1" for page in info
               for key in ("headers", "footers", "page_numbers", "unclassified") for item in page[key])


def test_edited_header_keeps_corrected_text_and_reuses_matching_source_classification():
    document = _document()
    header = _effective(document, "header")
    header.update(text="人工修正后的页眉", edited=True, decoration=None)
    original = deepcopy(document)
    item = build_page_info(document)[0]["headers"][0]
    assert item["text"] == "人工修正后的页眉"
    assert item["original_text"] == "相同的源页眉"
    assert item["classification_source"] == "source"
    assert item["edited"] and item["sources"] == header["sources"]
    assert item["evidence_pages"] == [1, 2, 3]
    assert document == original


def test_changed_or_cross_page_sources_cannot_reuse_old_header_classification():
    document = _document()
    _effective(document, "header").update(text="同ID但来源已变化", edited=True,
                                         sources=[{"page": 1, "bbox": [40, 180, 280, 193]}])
    other = _effective(document, "header", 2)
    other["edited"] = True
    other["sources"].append({"page": 3, "bbox": [40, 20, 150, 29]})
    info = build_page_info(document)
    assert not info[0]["headers"] and not info[0]["unclassified"]
    assert not info[1]["headers"] and not info[1]["unclassified"]


def test_unknown_margin_stays_unclassified_and_missing_page_statuses_are_preserved():
    document = _document()
    candidate = _block(1, "unknown", "只出现一次的页边文字", [250, 20, 390, 29], margin=True)
    document["pages"][0]["blocks"].append(deepcopy(candidate))
    document["blocks"].append(candidate)
    document["missing_pages"] = [
        {"number": 4, "status": "failed", "error": "OCR 失败"},
        {"number": 5, "status": "excluded", "error": "用户主动排除"},
        {"number": 6, "status": "pending", "error": None},
    ]
    info = build_page_info(document)
    assert info[0]["unclassified"][0]["text"] == candidate["text"]
    assert info[0]["unclassified"][0]["classification"] is None
    assert [page["status"] for page in info[-3:]] == ["failed", "excluded", "pending"]
    assert info[-3]["error"] == "OCR 失败"
    for page in info[-3:]:
        assert all(page[key] == [] for key in ("headers", "footers", "page_numbers", "unclassified"))
