"""Pixel/layout boundary checks plus one real OCR of a self-authored fixture.

No user PDF, user text, or user image is included in this test module.
"""
from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import pytest

from local_epub.scan_graphics import analyze_scan_graphics

ROOT = Path(__file__).resolve().parents[1]


def text_page(background='white', border=False):
    image = Image.new('RGB', (600, 800), background)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=15)
    lines = []
    for y in range(80, 700, 32):
        text = 'Self-authored paragraph for scan layout checks.'
        draw.text((65, y), text, font=font, fill='black')
        box = list(draw.textbbox((65, y), text, font=font))
        lines.append({'text': text, 'bbox': box})
    if border:
        draw.rectangle((1, 1, 598, 798), outline='black', width=2)
    return image, lines


def test_text_scan_gray_paper_and_border_do_not_turn_into_images():
    for background, border in [('white', False), ((198, 198, 198), False), ('white', True), ((240, 210, 180), False)]:
        image, lines = text_page(background, border)
        before_image, before_lines = image.tobytes(), deepcopy(lines)
        for number in (1, 8):
            result = analyze_scan_graphics(image, lines, 1, 600, 800, page_number=number)
            assert result['regions'] == [], (background, border, result)
        assert image.tobytes() == before_image and lines == before_lines


def test_large_art_is_preserved_but_only_first_source_page_can_be_cover():
    image = Image.new('RGB', (600, 800), (35, 35, 35))
    draw = ImageDraw.Draw(image)
    draw.polygon([(0, 0), (350, 0), (40, 750)], fill='white')
    draw.ellipse((160, 120, 560, 650), fill=(115, 115, 115))
    draw.polygon([(100, 500), (590, 300), (400, 780)], fill='black')
    lines = [{'text': 'SELF AUTHORED COVER', 'bbox': [80, 45, 500, 75]}]
    first = analyze_scan_graphics(image, lines, 1, 600, 800, page_number=1)
    later = analyze_scan_graphics(image, lines, 1, 600, 800, page_number=4)
    assert len(first['regions']) == 1
    assert first['regions'][0]['role'] == 'cover'
    assert first['regions'][0]['bbox'] == [0., 0., 600., 800.]
    assert later['regions'] and all(region['role'] == 'figure' for region in later['regions'])
    assert all('confidence' not in region for region in first['regions'] + later['regions'])


def test_pale_colored_figure_is_kept_without_imageifying_surrounding_text():
    image, lines = text_page()
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 260, 540, 465), fill='white')
    lines = [line for line in lines if line['bbox'][3] < 260 or line['bbox'][1] > 465]
    # Luminance exceeds the old global 205 threshold; color remains meaningful.
    draw.rectangle((100, 280, 500, 440), fill=(250, 205, 205))
    draw.polygon([(150, 420), (280, 300), (420, 420)], fill=(200, 235, 200))
    result = analyze_scan_graphics(image, lines, 1, 600, 800, page_number=2)
    assert len(result['regions']) == 1
    region = result['regions'][0]
    assert region['role'] == 'figure'
    x0, y0, x1, y1 = region['bbox']
    assert 90 <= x0 <= 105 and 270 <= y0 <= 285
    assert 495 <= x1 <= 510 and 435 <= y1 <= 450


def test_real_local_ocr_keeps_fixture_illustration_and_plain_scan_text():
    from local_epub.backend import _ocr_lines, _render
    from local_epub.resources import check_resources
    resources = ROOT / 'resources'
    if not check_resources(resources)['ok']:
        pytest.skip('Prepared local OCR models required for real fixture verification')
    examples = [('03_columns_figures_notes.pdf', 1, True), ('02_mixed_bad_layer.pdf', 2, False)]
    for filename, number, expect_picture in examples:
        image = _render(ROOT / 'tests/fixtures' / filename, number, None, 180)
        lines, _ = _ocr_lines(image, resources, 2.5)
        regions = analyze_scan_graphics(image, lines, 2.5, 420, 595, page_number=number)['regions']
        if expect_picture:
            pictures = [r for r in regions if r['bbox'][0] < 210 < r['bbox'][2]
                        and r['bbox'][1] < 320 < r['bbox'][3]]
            assert len(pictures) == 1 and pictures[0]['role'] == 'figure'
            assert pictures[0]['bbox'][1] > 240 and pictures[0]['bbox'][3] < 400
        else:
            assert regions == []


def test_inline_filled_badge_keeps_pixels_without_rewriting_ocr_text():
    scale = 3
    image = Image.new('RGB', (600*scale, 800*scale), 'white')
    draw = ImageDraw.Draw(image)
    draw.ellipse((300, 300, 339, 339), fill=(115, 115, 115))
    draw.text((313, 304), 'H', fill='white', font=ImageFont.load_default(size=25))
    for y in (309, 317, 325):
        draw.ellipse((305, y, 309, y+4), fill='white')
    # These are geometry inputs, not a mocked recognition-accuracy assertion.
    lines = [{'text': 'unchanged hypothetical OCR string', 'bbox': [60, 97, 500, 116]}]
    before = deepcopy(lines)
    result = analyze_scan_graphics(image, lines, scale, 600, 800, page_number=8)
    symbols = [r for r in result['regions'] if r['role'] == 'inline-symbol']
    assert len(symbols) == 1
    box = symbols[0]['bbox']
    assert box[0] < 106 < box[2] and box[1] < 106 < box[3]
    assert lines == before
    assert 'OCR' in symbols[0]['reason']
    assert any(issue['code'] == 'inline_symbol_position_review' for issue in result['issues'])


def test_plain_dot_hollow_zero_and_square_glyph_parts_are_not_inline_badges():
    scale = 3
    image = Image.new('RGB', (600*scale, 800*scale), 'white')
    draw = ImageDraw.Draw(image)
    # Filled punctuation dot, hollow round O/0, and square 口/目-like parts.
    draw.ellipse((300, 300, 339, 339), fill='black')
    draw.ellipse((450, 300, 489, 339), outline='black', width=4)
    draw.rectangle((600, 300, 639, 339), outline='black', width=5)
    draw.rectangle((750, 300, 789, 339), outline='black', width=5)
    draw.line((750, 319, 789, 319), fill='black', width=4)
    draw.text((900, 300), 'O0BQ', fill='black', font=ImageFont.load_default(size=39))
    lines = [{'text': 'ordinary punctuation and glyph parts', 'bbox': [60, 97, 500, 120]}]
    result = analyze_scan_graphics(image, lines, scale, 600, 800, page_number=8)
    assert not [r for r in result['regions'] if r['role'] == 'inline-symbol']
