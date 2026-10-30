#!/usr/bin/env python3
"""Create small, hand-auditable Chinese PDFs without online assets.

The fixture PDF uses standard STSong-Light CID references; no font binary is
copied. Scanned pages are rendered by PDFium at 200 dpi before embedding.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.colors import HexColor, black
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures"
W, H = 420, 595
FONT = "STSong-Light"


def line(c, text, x=42, y=500, size=13):
    c.setFillColor(black)
    c.setFont(FONT, size)
    c.drawString(x, y, text)


def lines(c, text_lines, x=42, y=500, size=13, leading=23):
    for value in text_lines:
        line(c, value, x=x, y=y, size=size)
        y -= leading
    return y


def header(c, number):
    c.setFillColor(HexColor("#657385"))
    c.setFont(FONT, 9)
    c.drawString(42, 565, "本地转换验收样本 · 自制内容")
    c.drawCentredString(W / 2, 25, str(number))


def title(c, text, key, y=530):
    c.bookmarkPage(key)
    c.addOutlineEntry(text, key, level=0)
    line(c, text, y=y, size=20)


def write_truth(name, pages, **extra):
    payload = {
        "fixture": name + ".pdf", "origin": "本项目自制，无第三方书籍内容",
        "page_count": len(pages), "pages": pages, **extra,
    }
    (OUT / (name + ".truth.json")).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def make_single():
    name = "01_chinese_crosspage"
    c = canvas.Canvas(str(OUT / (name + ".pdf")), pagesize=(W, H), invariant=1)
    c.setTitle("山间观察笔记")
    c.setAuthor("本地转换测试")
    header(c, 1)
    title(c, "第一章 山间清晨", "morning")
    first = [
        "清晨六点，观察员沿着溪流走进山谷。",
        "雾气停在树梢，石桥下的水声清晰可闻。",
        "这份笔记只记录亲眼所见，不作额外推测。",
        "温度计显示 18.5°C，记录编号为 PDF-2026。",
        "中文与 English 混排仍应保留原来的顺序。",
        "第一处样点位于桥北，地面覆盖湿润落叶。",
        "第二处样点位于坡顶，风向由东转为西南。",
        "观察员每隔十分钟记录一次光照变化。",
        "记录表中的空白表示未测量，不等于零。",
        "所有数据均保存在本机，整理时不需联网。",
        "阅读电子书时，可以根据屏幕调整字号。",
        "段落应随屏幕宽度重排，图片保持比例。",
        "原始文档必须保留，修订内容另行记录。",
        "页眉和页码属于辅助信息，不应混进正文。",
        "不确定的内容应交给读者核对原始页面。",
        "跨页示例：我们沿着弯曲的小路继续前行，",
        "直到山谷尽头出现一座木屋，门前摆着",
    ]
    lines(c, first, y=487, leading=24)
    c.showPage()
    header(c, 2)
    continued = [
        "两把竹椅和一张旧桌子。桌上放着当天的记录。",
        "这一句从上一页延续，合并时应保留两个来源页。",
    ]
    lines(c, continued, y=528)
    title(c, "第二章 傍晚归途", "evening", y=430)
    last = [
        "傍晚返回时，山路上的雾已经散去。",
        "观察员核对了页码，并保留所有原始记录。",
        "最终结论：忠实保存内容，比自动补写更重要。",
    ]
    lines(c, last, y=390)
    c.save()
    write_truth(name, [
        {"page": 1, "expected_method": "text", "ordered_fragments": ["第一章 山间清晨", *first]},
        {"page": 2, "expected_method": "text", "ordered_fragments": [*continued, "第二章 傍晚归途", *last]},
    ], cross_page_join={"from_page": 1, "to_page": 2, "before": first[-1], "after": continued[0]},
       bookmarks=[{"title": "第一章 山间清晨", "page": 1}, {"title": "第二章 傍晚归途", "page": 2}])


def scan_image(title_text, body_lines, page_no):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H), invariant=1)
    header(c, page_no)
    line(c, title_text, y=530, size=20)
    lines(c, body_lines, y=485, leading=28)
    c.save()
    doc = pdfium.PdfDocument(buf.getvalue())
    page = doc[0]
    image = page.render(scale=200 / 72).to_pil().convert("RGB")
    page.close()
    doc.close()
    return image


def make_mixed():
    name = "02_mixed_bad_layer"
    c = canvas.Canvas(str(OUT / (name + ".pdf")), pagesize=(W, H), invariant=1)
    c.setTitle("混合页面识别测试")
    c.setAuthor("本地转换测试")
    p1 = ["本页包含可靠文字层，应直接提取。", "同一本 PDF 可以包含文字页与扫描页。", "检查数字：314159；英文：Local Only。"]
    header(c, 1)
    title(c, "一、可靠文字页", "text")
    lines(c, p1, y=485)
    c.showPage()
    p2 = ["这是一张只有图像的扫描页面。", "识别过程必须在本机完成。", "离线资源准备后，不需要账号和密钥。", "校对数字：2026，中文：春风又绿江南岸。"]
    c.drawImage(ImageReader(scan_image("二、扫描文字页", p2, 2)), 0, 0, W, H)
    c.bookmarkPage("scan")
    c.addOutlineEntry("二、扫描文字页", "scan", level=0)
    c.showPage()
    p3 = ["本页图像是真实内容，隐藏文字层是错误的。", "正确文字：落叶铺满小径，远处传来钟声。", "不要重复输出扫描图像中的正文。", "应报告文字层疑点，并使用本地 OCR。"]
    c.drawImage(ImageReader(scan_image("三、不可靠文字层", p3, 3)), 0, 0, W, H)
    hidden = c.beginText(42, 485)
    hidden.setFont("Helvetica", 12)
    hidden.setTextRenderMode(3)
    for _ in range(7):
        hidden.textLine("(cid:999) (cid:998) (cid:997) (cid:996)")
    c.drawText(hidden)
    c.showPage()
    p4 = ["上方是可靠文字，下方图像包含另一段文字。", "两个区域都应保留，不能只读上方文字。"]
    header(c, 4)
    title(c, "四、同页混合区域", "regions")
    lines(c, p4, y=485)
    region = scan_image("区域扫描文字", ["这里是图像中的补充段落。", "关键短语：青山不改，绿水长流。"], 4)
    # Crop away the source page header/footer and empty paper, leaving text pixels.
    scale = 200 / 72
    region = region.crop((int(30 * scale), int(45 * scale), int(395 * scale), int(165 * scale)))
    c.drawImage(ImageReader(region), 42, 250, width=336, height=111)
    c.save()
    write_truth(name, [
        {"page": 1, "expected_method": "text", "ordered_fragments": ["一、可靠文字页", *p1]},
        {"page": 2, "expected_method": "ocr", "ordered_fragments": ["二、扫描文字页", *p2]},
        {"page": 3, "expected_method": "ocr", "ordered_fragments": ["三、不可靠文字层", *p3], "must_not_emit": ["cid:999"]},
        {"page": 4, "expected_method": "mixed", "ordered_fragments": ["四、同页混合区域", *p4, "区域扫描文字", "这里是图像中的补充段落。", "关键短语：青山不改，绿水长流。"]},
    ])


def make_layout():
    name = "03_columns_figures_notes"
    c = canvas.Canvas(str(OUT / (name + ".pdf")), pagesize=(W, H), invariant=1)
    c.setTitle("双栏与图表测试")
    c.setAuthor("本地转换测试")
    header(c, 1)
    title(c, "第一节 双栏观察", "columns")
    left = ["左栏第一段：河岸种着柳树。", "先读完左栏，再进入右栏。", "左栏第二段：树影落在水面。", "观察地点见文末注释[1]。"]
    right = ["右栏第一段：远处有一座桥。", "右栏内容不能插入左栏句中。", "右栏第二段：桥头停着小船。", "本页插图位于两栏文字下方。"]
    lines(c, left, x=30, y=480, size=11, leading=25)
    lines(c, right, x=221, y=480, size=11, leading=25)
    diagram = Image.new("RGB", (800, 280), "#edf4f7")
    d = ImageDraw.Draw(diagram)
    d.rectangle((0, 170, 800, 280), fill="#72afc6")
    d.polygon([(80, 170), (180, 35), (280, 170)], fill="#597b6d")
    d.polygon([(230, 170), (370, 12), (510, 170)], fill="#7c9b87")
    d.line([(440, 180), (670, 180)], fill="#5e4f44", width=18)
    d.line([(470, 180), (470, 240)], fill="#5e4f44", width=12)
    d.line([(640, 180), (640, 240)], fill="#5e4f44", width=12)
    c.drawImage(ImageReader(diagram), 45, 215, width=330, height=115)
    line(c, "图一：河岸、山峰与木桥（自制示意图）", x=56, y=195, size=11)
    c.line(30, 93, 170, 93)
    line(c, "[1] 观察地点为虚构场景，用于验证脚注保留。", x=30, y=73, size=9)
    c.showPage()
    header(c, 2)
    title(c, "第二节 表格与公式", "tables")
    line(c, "表一记录三个样点的数据，空白表示未测量。", y=480, size=12)
    rows = [["样点", "温度", "备注"], ["桥北", "18.5", "晴"], ["坡顶", "17.2", "有风"], ["溪边", "", "未测量"]]
    x0, y0, widths, rh = 42, 440, [92, 92, 150], 32
    for row_i, row in enumerate(rows):
        y = y0 - row_i * rh
        x = x0
        for col_i, value in enumerate(row):
            c.rect(x, y - rh, widths[col_i], rh, fill=0, stroke=1)
            line(c, value, x=x + 12, y=y - 21, size=12)
            x += widths[col_i]
    line(c, "复杂公式应谨慎识别；不可靠时保留区域图像。", y=260, size=12)
    # Deliberately fragmented mathematical formula, to exercise conservative handling.
    line(c, "E", x=128, y=200, size=20)
    line(c, "= m c", x=150, y=200, size=20)
    line(c, "2", x=216, y=212, size=11)
    line(c, "公式含义不应由生成模型擅自改写。", y=150, size=12)
    c.setStrokeColor(HexColor("#507e83"))
    c.setLineWidth(2)
    c.circle(65, 94, 17, stroke=1, fill=0)
    c.line(83, 94, 142, 94)
    c.line(135, 99, 142, 94)
    c.line(135, 89, 142, 94)
    c.rect(145, 77, 42, 34, stroke=1, fill=0)
    line(c, "图二：矢量示意图", x=210, y=90, size=11)
    c.save()
    write_truth(name, [
        {"page": 1, "expected_method": "text", "ordered_fragments": ["第一节 双栏观察", *left, *right, "图一：河岸、山峰与木桥（自制示意图）", "[1] 观察地点为虚构场景，用于验证脚注保留。"], "image_count_min": 1, "footnote": "[1] 观察地点为虚构场景，用于验证脚注保留。"},
        {"page": 2, "expected_method": "text", "ordered_fragments": ["第二节 表格与公式", "表一记录三个样点的数据，空白表示未测量。", "复杂公式应谨慎识别；不可靠时保留区域图像。", "公式含义不应由生成模型擅自改写。", "图二：矢量示意图"], "table": rows, "formula_visual": "E = m c²", "vector_figure": "circle, arrow and rectangle"},
    ])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    make_single()
    make_mixed()
    make_layout()
    # Render every fixture page for visual quality review.
    preview = OUT / "previews"
    preview.mkdir(exist_ok=True)
    for source in sorted(OUT.glob("*.pdf")):
        doc = pdfium.PdfDocument(str(source))
        for i in range(len(doc)):
            page = doc[i]
            page.render(scale=1.5).to_pil().save(preview / f"{source.stem}-{i + 1}.png")
            page.close()
        doc.close()
        print(source.relative_to(ROOT))


if __name__ == "__main__":
    main()
