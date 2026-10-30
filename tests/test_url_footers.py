"""Conservative URL footer rules using synthetic content and geometry only.

These are layout/export regressions, not an OCR accuracy claim. No user PDF,
its text, or its site/domain is copied into the repository's test fixtures.
"""
from copy import deepcopy
import math

from local_epub.margins import annotate_margins
from local_epub.page_margins import export_document


PROMPT = "See more, please visit: https://reading.example.invalid/catalog"


def source(page, bbox, polygon=None):
    value = {"page": page, "bbox": bbox}
    if polygon is not None:
        value["polygon"] = polygon
    return value


def block(page, suffix, text, bbox, kind="paragraph", **extra):
    return {"id": f"p{page}-{suffix}", "kind": kind, "text": text,
            "sources": [source(page, bbox)], "issues": [], "confidence": None, **extra}


def example(count=3, footer_kind="footnote", body_height=24):
    pages = [{"number": n, "width": 600, "height": 800, "status": "success"}
             for n in range(1, count + 1)]
    bodies = [block(n, f"body-{i}", f"Synthetic body paragraph {i}.", [40, y, 540, y + body_height])
              for n in range(1, count + 1) for i, y in enumerate((120, 160, 690))]
    footers = [block(n, "footer", PROMPT, [80, 772, 330, 790], kind=footer_kind)
               for n in range(1, count + 1)]
    return pages, bodies, footers


def decorations(blocks):
    return [b for b in blocks if b.get("decoration")]


def test_old_ocr_cache_kinds_and_protocol_variants_are_reversible_footers():
    pages, body, footers = example()
    footers[1]["kind"] = "paragraph"
    footers[1]["text"] = PROMPT.replace("https://", "htps://").replace(" ", "")
    footers[2]["kind"] = "heading"  # The old bbox heuristic could call this a title.
    blocks = body + footers
    before = deepcopy(blocks)
    annotate_margins(blocks, pages)
    assert decorations(blocks) == footers
    assert all(b["decoration"]["kind"] == "footer" for b in footers)
    assert all(b["decoration"]["rule"] == "repeated_url_footer" for b in footers)
    assert all(b["decoration"]["evidence_pages"] == [1, 2, 3] for b in footers)
    assert [{k: v for k, v in b.items() if k != "decoration"} for b in blocks] == before
    repeat = deepcopy(blocks)
    annotate_margins(blocks, pages)
    assert blocks == repeat


def test_true_polygon_height_handles_sloped_long_small_type():
    pages, body, footers = example(body_height=15)
    for b, degrees in zip(footers, (-4, 0, 3)):
        co, si = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
        polygon = [[200 + x * co - y * si, 778 + x * si + y * co]
                   for x, y in ((-120, -4.5), (120, -4.5), (120, 4.5), (-120, 4.5))]
        bbox = [min(p[0] for p in polygon), min(p[1] for p in polygon),
                max(p[0] for p in polygon), max(p[1] for p in polygon)]
        b["sources"] = [source(b["sources"][0]["page"], bbox, polygon)]
    # Axis height exceeds the legacy 14pt threshold; the actual type is 9pt.
    assert footers[0]["sources"][0]["bbox"][3] - footers[0]["sources"][0]["bbox"][1] > 20
    annotate_margins(body + footers, pages)
    assert all((b.get("decoration") or {}).get("rule") == "repeated_url_footer" for b in footers)


def test_inherited_template_requires_three_measured_pages_and_exact_url():
    pages, body, footers = example(count=5)
    body = [b for b in body if b["sources"][0]["page"] != 4]  # Sparse end page.
    footers[3]["text"] = PROMPT.replace("See more", "See m0re")
    footers[4]["text"] = PROMPT.replace("/catalog", "/different-article")
    annotate_margins(body + footers, pages)
    assert all(footers[i]["decoration"]["kind"] == "footer" for i in range(4))
    assert footers[4]["decoration"] is None  # Different paths are not OCR guesses.
    assert footers[0]["decoration"]["evidence_pages"] == [1, 2, 3, 4]

    pages, body, footers = example(count=2)
    annotate_margins(body + footers, pages)
    assert not decorations(body + footers)


def test_url_host_case_is_equivalent_but_article_path_case_is_not():
    pages, body, footers = example()
    footers[1]["text"] = PROMPT.replace("https://reading.example.invalid", "HTTPS://READING.EXAMPLE.INVALID")
    annotate_margins(body + footers, pages)
    assert len(decorations(body + footers)) == 3
    footers[2]["text"] = PROMPT.replace("/catalog", "/Catalog")
    annotate_margins(body + footers, pages)
    assert not decorations(body + footers)


def test_legitimate_urls_notes_edits_and_ambiguous_geometry_remain():
    cases = ("body_url", "bare_footer_url", "numbered_note", "same_size_text",
             "insufficient_gap", "edited", "bookmark", "multiline", "cross_page",
             "missing_bbox", "invalid_polygon", "two_entries_per_page")
    for case in cases:
        pages, body, footers = example()
        for b in footers:
            if case == "body_url":
                b["kind"] = "paragraph"
                b["sources"][0]["bbox"] = [40, 600, 540, 624]
            elif case == "bare_footer_url":
                b["text"] = "https://research.example.invalid/important-reference"
            elif case == "numbered_note":
                b["text"] = "[1] " + PROMPT
            elif case == "same_size_text":
                b["sources"][0]["bbox"] = [80, 766, 330, 790]
            elif case == "insufficient_gap":
                n = b["sources"][0]["page"]
                body.append(block(n, "nearby-body", "This is adjacent body text.", [40, 751, 540, 769]))
            elif case == "edited":
                b["edited"] = True
            elif case == "bookmark":
                b["origin"] = "pdf_bookmark"
            elif case == "multiline":
                b["sources"].append(source(b["sources"][0]["page"], [40, 730, 540, 748]))
            elif case == "cross_page":
                b["sources"].append(source(b["sources"][0]["page"] % 3 + 1, [80, 772, 330, 790]))
            elif case == "missing_bbox":
                b["sources"][0]["bbox"] = None
            elif case == "invalid_polygon":
                b["sources"][0]["polygon"] = [[0, float("nan")]] * 4
        if case == "two_entries_per_page":
            copies = deepcopy(footers)
            for b in copies:
                b["id"] += "-duplicate"
            footers += copies
        annotate_margins(body + footers, pages)
        assert not decorations(body + footers), case


def test_export_option_moves_footer_to_running_plan_without_deleting_source():
    pages, body, footers = example()
    blocks = body + footers
    for page in pages:
        page["blocks"] = deepcopy([b for b in blocks if b["sources"][0]["page"] == page["number"]])
    document = {"pages": pages, "blocks": blocks, "issues": [], "missing_pages": [],
                "source_sha256": "0" * 64}
    original = deepcopy(document)
    omitted = export_document(document, False)
    assert [b["id"] for b in omitted["blocks"] if b["exclude_from_export"]] == [b["id"] for b in footers]
    assert "margin_plan" not in omitted
    retained = export_document(document, True)
    assert all(page["footer_body_text"] == PROMPT for page in retained["margin_plan"]["pages"])
    assert all(b["page_margin_role"] == "footer" for b in retained["blocks"] if b["id"].endswith("footer"))
    assert document == original

    # Human corrections reuse classification only through the unchanged source
    # block in pages, as for other running matter; original content is retained.
    footer = document["blocks"][-1]
    footer["text"] = "Corrected publisher footer"
    footer["edited"] = True
    kept = export_document(document, True)
    assert kept["margin_plan"]["pages"][-1]["footer_body_text"] == footer["text"]
    removed = export_document(document, False)
    assert removed["blocks"][-1]["exclude_from_export"]
    assert document["blocks"][-1]["text"] == "Corrected publisher footer"
