"""Synthetic layout risks; user-provided books never enter public fixtures."""
from copy import deepcopy
import math
from pathlib import Path

import pytest

from local_epub.paragraphs import line_geometry, recover_paragraphs


def line(text, x, y, width, *, height=10, angle=2, method="ocr", kind=None):
    co, si = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    polygon = [[a * co - b * si, a * si + b * co]
               for a, b in ((x, y), (x + width, y), (x + width, y + height), (x, y + height))]
    box = [min(p[0] for p in polygon), min(p[1] for p in polygon),
           max(p[0] for p in polygon), max(p[1] for p in polygon)]
    result = {"text": text, "bbox": box, "size": box[3] - box[1], "polygon": polygon,
              "confidence": {"value": .98, "source": "synthetic engine fixture"} if method == "ocr" else None,
              "method": method}
    if kind:
        result["kind"] = kind
    return result


def sentence(char, count, punctuation):
    return char * (count - 1) + punctuation


def test_tilted_multisentence_paragraphs_use_indent_not_full_stops_and_preserve_sources():
    lines = [line(sentence("甲", 28, "。"), 70, 100, 280),
             line(sentence("乙", 30, "，"), 50, 118, 300),
             line(sentence("丙", 12, "。"), 50, 136, 120),
             line(sentence("丁", 28, "！"), 70, 154, 280),
             line(sentence("戊", 30, "。"), 50, 172, 300),
             line(sentence("己", 8, "。"), 50, 190, 80)]
    lines[1]["confidence"]["value"] = .7
    before = deepcopy(lines)
    blocks = recover_paragraphs(lines, 500, 700, 3)
    assert lines == before
    assert [b["text"] for b in blocks] == ["".join(l["text"] for l in lines[:3]), "".join(l["text"] for l in lines[3:])]
    assert all(b["kind"] == "paragraph" and b["confidence"] is None for b in blocks)
    assert blocks[1]["paragraph_recovery"]["start_reason"] == "first_line_indent"
    assert any(i["code"] == "ocr_low_confidence" for i in blocks[0]["issues"])
    for block in blocks:
        for span in block["line_spans"]:
            original = lines[span["ordered_line_index"]]
            assert block["text"][span["start"]:span["end"]] == original["text"]
            source = block["sources"][span["source_index"]]
            assert source["bbox"] == original["bbox"] and source["polygon"] == original["polygon"]
            assert source["text_height"] == pytest.approx(10)
    assert line_geometry(lines[1])["height"] == pytest.approx(10)
    assert lines[1]["size"] > 20  # Axis-aligned bbox would falsely inflate font size.


def test_native_bbox_only_layout_still_joins_full_line_sentences():
    lines = [line(sentence("甲", 28, "。"), 70, 100, 280, angle=0, method="text"),
             line(sentence("乙", 30, "。"), 50, 118, 300, angle=0, method="text"),
             line(sentence("丙", 10, "。"), 50, 136, 100, angle=0, method="text")]
    for item in lines:
        item.pop("polygon")
    blocks = recover_paragraphs(lines, 500, 700, 1)
    assert len(blocks) == 1 and len(blocks[0]["sources"]) == 3
    assert not line_geometry(lines[0])["has_polygon"]


def test_native_word_boundary_wrap_with_one_measured_gap_keeps_paragraph():
    # A native line can stop before the edge because the next whole word did
    # not fit. A separate paragraph after a blank gap must still stay separate.
    lines = [{"text": text, "bbox": [40, y, right, y + 13], "size": 13,
              "confidence": None, "method": "text"}
             for text, y, right in [("Native page first wrapped line", 95, 222),
                                    ("continues on the same source page.", 110, 249),
                                    ("Last independent paragraph.", 185, 228)]]
    blocks = recover_paragraphs(lines, 420, 595, 1)
    assert [b["text"] for b in blocks] == [lines[0]["text"] + " " + lines[1]["text"], lines[2]["text"]]
    assert len(blocks[0]["sources"]) == 2
    assert "native_word_wrap" in blocks[0]["paragraph_recovery"]["joins"]
    assert blocks[1]["paragraph_recovery"]["start_reason"] == "paragraph_spacing"
    for line in lines:
        line["method"] = "ocr"
    assert len(recover_paragraphs(lines, 420, 595, 1)) == 3


def test_titles_footnotes_images_and_explicit_region_never_get_crossed():
    lines = [line(sentence("甲", 30, "，"), 50, 100, 300, angle=0),
             line("第二章 标题", 50, 118, 150, angle=0, kind="heading"),
             line(sentence("乙", 30, "，"), 50, 136, 300, angle=0),
             line("图", 50, 154, 100, angle=0, kind="image"),
             line(sentence("丙", 30, "，"), 50, 172, 300, angle=0),
             line("[1] 保留的注释", 50, 190, 100, angle=0, kind="footnote"),
             line(sentence("丁", 30, "，"), 50, 208, 300, angle=0),
             line(sentence("戊", 30, "。"), 50, 226, 300, angle=0)]
    blocks = recover_paragraphs(lines, 500, 700, 1, barriers=[[40, 220, 360, 223]])
    assert len(blocks) == len(lines)
    assert [b["kind"] for b in blocks][1::2][:3] == ["heading", "image", "footnote"]
    assert blocks[-1]["paragraph_recovery"]["start_reason"] == "intervening_region"


def test_column_and_reading_order_boundaries_are_not_joined():
    lines = [line(sentence("甲", 19, "，"), 40, 100, 190, angle=0),
             line(sentence("乙", 19, "，"), 40, 118, 190, angle=0),
             line(sentence("丙", 19, "，"), 300, 100, 190, angle=0),
             line(sentence("丁", 19, "。"), 300, 118, 190, angle=0)]
    blocks = recover_paragraphs(lines, 550, 700, 1, dual=True)
    assert len(blocks) == 2
    assert all(len(b["sources"]) == 2 for b in blocks)
    assert blocks[1]["paragraph_recovery"]["start_reason"] == "reading_order_boundary"


def test_uncertain_short_line_and_missing_geometry_stay_visible_as_review():
    lines = [line(sentence("甲", 30, "，"), 50, 100, 300),
             line("不完整但很短", 50, 118, 70),
             line(sentence("乙", 30, "，"), 50, 136, 300),
             {"text": "坐标缺失的原文", "bbox": None, "confidence": None, "method": "ocr"},
             line(sentence("丙", 30, "。"), 50, 172, 300)]
    blocks = recover_paragraphs(lines, 500, 700, 1)
    assert "".join(b["text"] for b in blocks) == "".join(l["text"] for l in lines)
    assert len(blocks) == 4
    assert any(i["code"] == "paragraph_boundary_review" for b in blocks for i in b["issues"])
    assert next(b for b in blocks if "坐标缺失" in b["text"])["sources"][0]["bbox"] is None


def test_slightly_short_nonterminal_continuation_is_joined_with_review():
    lines = [line(sentence("甲", 28, "，"), 70, 100, 280),
             line("乙" * 27, 50, 118, 270),
             line(sentence("丙", 30, "，"), 50, 136, 300),
             line(sentence("丁", 10, "。"), 50, 154, 100)]
    blocks = recover_paragraphs(lines, 500, 700, 1)
    assert len(blocks) == 1
    assert any(i["code"] == "paragraph_join_review" for i in blocks[0]["issues"])


def test_list_items_and_blank_line_are_independent_even_without_terminal_stop():
    lines = [line("1. " + "甲" * 20, 50, 100, 240, angle=0),
             line("2. " + "乙" * 20, 50, 118, 240, angle=0),
             line("3. " + "丙" * 20, 50, 136, 240, angle=0),
             line("丁" * 24, 50, 180, 240, angle=0)]
    blocks = recover_paragraphs(lines, 500, 700, 1)
    assert len(blocks) == 4
    assert blocks[-1]["paragraph_recovery"]["start_reason"] == "paragraph_spacing"


def test_two_incompatible_font_sizes_have_no_invented_shared_body_profile():
    lines = [line("较小字号的一行", 50, 100, 150, height=12, angle=0),
             line("另一字号的一行", 50, 130, 220, height=22, angle=0)]
    blocks = recover_paragraphs(lines, 500, 700, 1)
    assert len(blocks) == 2
    assert all(not b["paragraph_recovery"]["first_line"]["usable"] for b in blocks)


def test_self_created_tilted_chinese_page_recovers_real_ocr_paragraphs(tmp_path):
    pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
    from local_epub.backend import _ocr_lines, _reading_order, _render
    from local_epub.resources import check_resources
    resources = Path(__file__).resolve().parents[1] / "resources"
    if not check_resources(resources)["ok"]:
        pytest.skip("Prepared local OCR models required")
    text = ["清晨的小镇十分安静，读者翻开新书。故事从这里开始。",
            "窗外的树叶轻轻摇动，阳光照在桌面，他继续阅读下一行。",
            "这一段到这里结束。",
            "午后的微风经过街道，孩子走进书店。故事仍然在继续。",
            "店里的老人整理书架，抬头看见一位朋友，两人一起聊天。",
            "第二段也完整保留。"]
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    pdf = tmp_path / "self-created-paragraphs.pdf"
    document = canvas.Canvas(str(pdf), pagesize=(612, 792))
    document.setFont("STSong-Light", 15)
    for index, value in enumerate(text):
        document.drawString(80 if index in (0, 3) else 50, 650 - index * 24, value)
    document.save()
    image = _render(str(pdf), 1, None, 180).rotate(-1.5, fillcolor="white")
    recognized, _ = _ocr_lines(image, resources, 180 / 72)
    ordered, dual = _reading_order(recognized, 612)
    before = deepcopy(ordered)
    blocks = recover_paragraphs(ordered, 612, 792, 1, dual=dual)
    assert len(ordered) == 6
    assert len(blocks) == 2 and [len(b["sources"]) for b in blocks] == [3, 3]
    assert "清晨" in blocks[0]["text"] and "午后" in blocks[1]["text"]
    assert ordered == before
    assert [b["text"][s["start"]:s["end"]] for b in blocks for s in b["line_spans"]] == [l["text"].strip() for l in ordered]
