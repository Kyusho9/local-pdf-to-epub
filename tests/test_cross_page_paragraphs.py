"""Self-authored geometry checks for conservative cross-page paragraph joins."""
from copy import deepcopy
import uuid
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image

from local_epub.cross_page_paragraphs import join_cross_page_paragraphs
from local_epub.page_margins import build_margin_plan, source_spans_for_block, text_digest
from local_epub.paragraphs import recover_paragraphs


def line(text, x, y, width):
    return {"text": text, "bbox": [x, y, x + width, y + 10], "size": 10, "method": "ocr",
            "polygon": [[x, y], [x + width, y], [x + width, y + 10], [x, y + 10]],
            "confidence": {"value": .97, "source": "self-authored geometry fixture"}}


def recovered(number, lines):
    blocks = recover_paragraphs(lines, 500, 700, number)
    for index, block in enumerate(blocks, 1):
        block["id"] = f"p{number}-b{index}"
    return blocks


def sample():
    left = recovered(1, [line("甲" * 28, 70, 550, 280),
                         line("乙" * 30, 50, 568, 300),
                         line("丙" * 29 + "😀", 50, 586, 300)])
    right = recovered(2, [line("前页未完的故事在这里结束。", 50, 60, 130),
                          line("丁" * 28, 70, 78, 280),
                          line("戊" * 10 + "。", 50, 96, 110)])
    pages = [{"number": n, "width": 500, "height": 700, "status": "success", "method": "ocr",
              "layout": {"columns": 1}, "blocks": deepcopy(blocks)} for n, blocks in ((1, left), (2, right))]
    return left + right, pages


def test_join_keeps_unicode_sources_line_ranges_images_and_epub_boundaries(tmp_path):
    from local_epub.epub import write_epub
    blocks, pages = sample()
    first, second = blocks[:2]
    for block, page in ((first, 1), (second, 2)):
        block["inline_images"] = [{"asset": f"assets/symbol-{page}.png", "offset": 2,
                                   "text_sha256": text_digest(block["text"]),
                                   "source": {"page": page, "bbox": [60, 70, 70, 80]}}]
    original, original_pages = deepcopy(blocks), deepcopy(pages)
    result, issues = join_cross_page_paragraphs(blocks, pages)
    assert blocks == original and pages == original_pages
    assert len(result) == len(blocks) - 1 and len(issues) == 1
    merged = result[0]
    assert merged["text"] == first["text"] + second["text"]
    assert merged["sources"] == first["sources"] + second["sources"]
    assert merged["confidence"] is None and "confidence" not in issues[0]
    assert issues[0]["type"] == "processing" and issues[0]["pages"] == [1, 2]
    assert [span["page"] for span in source_spans_for_block(merged)] == [1, 2]
    assert merged["line_spans_text_sha256"] == text_digest(merged["text"])
    assert [span["source_index"] for span in merged["line_spans"]] == list(range(4))
    for span in merged["line_spans"]:
        source = merged["sources"][span["source_index"]]
        original_block = first if source["page"] == 1 else second
        old = next(item for item in original_block["line_spans"]
                   if original_block["sources"][item["source_index"]] == source)
        assert merged["text"][span["start"]:span["end"]] == original_block["text"][old["start"]:old["end"]]
    assert [image["offset"] for image in merged["inline_images"]] == [2, len(first["text"]) + 2]
    assert all(image["text_sha256"] == text_digest(merged["text"]) for image in merged["inline_images"])
    doc = {"id": str(uuid.uuid4()), "metadata": {"title": "自制跨页来源检查", "language": "zh-CN"},
           "blocks": result, "pages": pages, "toc": [], "issues": issues, "missing_pages": [], "edit_conflicts": [],
           "source_sha256": "a" * 64}
    assert build_margin_plan(doc)["blocks"][merged["id"]]["events"] == [
        {"offset": 0, "page": 1}, {"offset": len(first["text"]), "page": 2}]
    (tmp_path / "assets").mkdir()
    for page in (1, 2):
        Image.new("RGB", (12, 12), "#777777").save(tmp_path / f"assets/symbol-{page}.png")
    output = tmp_path / "joined.epub"
    report = write_epub(doc, tmp_path, output, retain_page_margins=True)
    assert report["content"]["inline_image_count"] == 2
    with zipfile.ZipFile(output) as archive:
        chapter = ET.fromstring(archive.read("EPUB/chapter-0001.xhtml"))
        ns = "{http://www.w3.org/1999/xhtml}"
        paragraph = chapter.find(".//" + ns + 'p[@id="b-1-1"]')
        assert "".join(paragraph.itertext()) == merged["text"]
        assert len(list(paragraph.iter(ns + "img"))) == 2
        assert [node.get("data-source-page") for node in paragraph.iter(ns + "span")
                if node.get("data-source-page")] == ["1", "2"]


def test_margin_paragraph_can_be_skipped_but_all_nonbody_barriers_stay():
    blocks, pages = sample()
    footer = {"id": "footer", "kind": "paragraph", "text": "保留的原页页脚", "sources": [{"page": 1, "bbox": [50, 675, 200, 690]}],
              "issues": [{"code": "margin_content", "page": 1, "bbox": [50, 675, 200, 690]}]}
    stream = [blocks[0], footer] + blocks[1:]
    result, issues = join_cross_page_paragraphs(stream, pages)
    assert len(issues) == 1 and result[1] == footer
    for kind in ("heading", "image", "table", "footnote"):
        barrier = {**footer, "kind": kind}
        stream = [blocks[0], barrier] + blocks[1:]
        result, issues = join_cross_page_paragraphs(stream, pages)
        assert not issues and result == stream, kind
    stale = deepcopy(footer)
    stale["issues"][0]["bbox"][0] += 1
    assert not join_cross_page_paragraphs([blocks[0], stale] + blocks[1:], pages)[1]


def test_uncertain_or_stale_edges_never_join_and_manual_pages_are_protected():
    blocks, pages = sample()
    assert not join_cross_page_paragraphs(blocks, pages, {1})[1]
    assert not join_cross_page_paragraphs(blocks, pages, {2})[1]
    for change in ("terminal", "indent", "font", "short", "lower_start", "early_end", "edited", "text_digest", "missing_geometry"):
        copy = deepcopy(blocks)
        if change == "terminal":
            copy[0]["paragraph_recovery"]["last_line"]["terminal_punctuation"] = True
        elif change == "indent":
            copy[1]["paragraph_recovery"]["first_line"]["starts_indented"] = True
        elif change == "font":
            copy[1]["paragraph_recovery"]["first_line"]["font_height"] = 20
        elif change == "short":
            copy[0]["paragraph_recovery"]["last_line"]["near_right_margin"] = False
        elif change == "lower_start":
            copy[1]["sources"][0]["bbox"] = [50, 300, 180, 310]
        elif change == "early_end":
            copy[0]["sources"][-1]["bbox"] = [50, 300, 350, 310]
        elif change == "edited":
            copy[1]["edited"] = True
        elif change == "text_digest":
            copy[1]["text"] = "人工修改" + copy[1]["text"]
        else:
            copy[0]["sources"][-1]["bbox"] = None
        result, issues = join_cross_page_paragraphs(copy, pages)
        assert not issues and result == copy, change


def test_missing_failed_duplicate_legacy_and_multicolumn_pages_remain_separate():
    blocks, pages = sample()
    for change in ("missing", "failed", "duplicate", "legacy", "columns"):
        copy = deepcopy(pages)
        if change == "missing":
            copy.pop()
        elif change == "failed":
            copy[1]["status"] = "failed"
        elif change == "duplicate":
            copy.append(deepcopy(copy[1]))
        elif change == "legacy":
            copy[0].pop("layout")
        else:
            copy[1]["layout"]["columns"] = 2
        result, issues = join_cross_page_paragraphs(blocks, copy)
        assert not issues and result == blocks, change


def test_three_page_continuation_preserves_every_source_line_and_boundary():
    blocks, pages = sample()
    middle = recovered(2, [line("中" * 30, 50, 60 + index * 18, 300) for index in range(32)])
    end = deepcopy(blocks[1:])
    for block in end:
        block["id"] = block["id"].replace("p2-", "p3-")
        for source in block["sources"]:
            source["page"] = 3
    pages[1]["blocks"] = deepcopy(middle)
    pages.append({**deepcopy(pages[1]), "number": 3, "blocks": deepcopy(end)})
    original = [blocks[0]] + middle + end
    result, issues = join_cross_page_paragraphs(original, pages)
    assert len(issues) == 2
    merged = result[0]
    assert merged["text"] == "".join(block["text"] for block in original[:3])
    assert [span["page"] for span in source_spans_for_block(merged)] == [1, 2, 3]
    assert len(merged["line_spans"]) == len(merged["sources"]) == 36
    assert [span["source_index"] for span in merged["line_spans"]] == list(range(36))
    assert len(merged["cross_page_recovery"]["joins"]) == 2
    assert not join_cross_page_paragraphs(original, pages, {2})[1]
