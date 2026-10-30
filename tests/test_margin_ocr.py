"""Real local OCR evidence for small page-edge digits, without page-order guesses."""
from pathlib import Path
import hashlib
import json
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

from local_epub.backend import _margin_ocr_lines, _ocr, _render, extract_page
from local_epub.resources import check_resources


ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "tests/fixtures/02_mixed_bad_layer.pdf"
RESOURCES = ROOT / "resources"


@pytest.fixture(scope="module", autouse=True)
def prepared_models():
    if not check_resources(RESOURCES)["ok"]:
        pytest.skip("Real margin OCR tests require the prepared local model pack")


@pytest.mark.parametrize("number", [2, 3])
def test_scanned_page_number_is_recovered_from_original_pixels(number, tmp_path):
    source_hash = hashlib.sha256(PDF.read_bytes()).hexdigest()
    image = _render(PDF, number, None, 180)
    baseline, _ = _ocr(RESOURCES)(np.asarray(image))
    # Establish the actual failure: whole-page detection emits no footer box.
    assert not [row for row in baseline or [] if min(point[1] for point in row[0]) > image.height * .92]
    page = extract_page(PDF, number, tmp_path, {"mode": "auto", "dpi": 180}, RESOURCES)
    footer = [block for block in page["blocks"]
              if block["text"] == str(number) and block["sources"][0]["bbox"][1] > page["height"] * .92]
    assert len(footer) == 1
    block = footer[0]
    confidence = block["confidence"]
    assert .95 <= confidence["value"] <= 1
    assert confidence["source"] == "RapidOCR recognition"
    assert confidence["context"] == "page-margin crop"
    recovered = [line for line in page["raw"]["ocr_lines"] if line.get("recognition_context")]
    assert len(recovered) == 1 and recovered[0]["text"] == str(number)
    assert [{k: s[k] for k in ("page", "bbox")} for s in block["sources"]] == [{"page": number, "bbox": recovered[0]["bbox"]}]
    assert block["sources"][0]["polygon"] == recovered[0]["polygon"]
    assert block["sources"][0]["text_height"] > 0
    # The full-page results remain intact, including their real model scores.
    primary = [line for line in page["raw"]["ocr_lines"] if not line.get("recognition_context")]
    assert [(line["text"], line["confidence"]["value"]) for line in primary] == [
        (str(row[1]), float(row[2])) for row in baseline]
    truth = json.loads(PDF.with_suffix(".truth.json").read_text(encoding="utf-8"))["pages"][number - 1]
    body = re.sub(r"\s+", "", "".join(block["text"] for block in page["blocks"]))
    position = 0
    for fragment in truth["ordered_fragments"]:
        fragment = re.sub(r"\s+", "", fragment)
        found = body.find(fragment, position)
        assert found >= 0, fragment
        position = found + len(fragment)
    assert hashlib.sha256(PDF.read_bytes()).hexdigest() == source_hash


def digit_image(value, y=1418):
    image = Image.new("RGB", (1050, 1488), "white")
    draw = ImageDraw.Draw(image)
    draw.text((525, y), value, font=ImageFont.load_default(size=22), fill=(95, 95, 95), anchor="mm")
    return image


@pytest.mark.parametrize("value,y", [("9", 1418), ("17", 1418), ("88", 55)])
def test_literal_digits_are_read_without_any_pdf_page_index(value, y):
    image = digit_image(value, y)
    before = image.tobytes()
    lines, _ = _margin_ocr_lines(image, RESOURCES, 2.5, [])
    assert [line["text"] for line in lines] == [value]
    line = lines[0]
    assert line["confidence"]["value"] >= .95
    assert line["bbox"][1] <= y / 2.5 <= line["bbox"][3]
    assert line["recognition_context"]["scope"] == "page-margin crop"
    # An already detected line wins even if its current text differs. This
    # fallback must not add overlapping alternatives or rewrite source text.
    existing = [{"bbox": line["bbox"], "text": "existing original"}]
    assert _margin_ocr_lines(image, RESOURCES, 2.5, existing)[0] == []
    assert existing[0]["text"] == "existing original"
    assert image.tobytes() == before


def test_blank_noise_letters_and_body_digits_are_not_added_as_page_numbers():
    for value, y in [("", 1418), (".", 1418), ("-", 1418), ("I", 1418), ("O", 1418), ("17", 740)]:
        lines, _ = _margin_ocr_lines(digit_image(value, y), RESOURCES, 2.5, [])
        assert lines == [], (value, y, lines)
