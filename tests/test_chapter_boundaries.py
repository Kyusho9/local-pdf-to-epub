"""Chapter boundaries and heading navigation share real, once-parsed PDF content.

The multi-level case uses the same saved editing API as the GUI. The additional
bookmark case adds an actual PDF outline without changing or re-OCRing its pages.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile

import pytest

from local_epub.backend import inspect_pdf
from local_epub.epub import write_epub
from local_epub.pipeline import build_document, create_job, export_job, load_job, run_job, save_edit
from local_epub.resources import check_resources


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/fixtures/02_mixed_bad_layer.pdf"
RESOURCES = ROOT / "resources"
X = "{http://www.w3.org/1999/xhtml}"
O = "{http://www.idpf.org/2007/opf}"


def normal(text):
    return re.sub(r"\s+", "", text)


@pytest.fixture(scope="module")
def parsed_mixed(tmp_path_factory):
    if not check_resources(RESOURCES)["ok"]:
        pytest.skip("Chapter integration tests require the prepared local OCR pack")
    job = tmp_path_factory.mktemp("chapter-source") / "job"
    create_job(SOURCE, job)
    result = run_job(job, RESOURCES)
    assert result["status"] == "complete"
    assert [page["method"] for page in result["pages"]] == ["text", "ocr", "ocr", "mixed"]
    return job


@pytest.fixture
def mixed_job(parsed_mixed, tmp_path):
    return Path(shutil.copytree(parsed_mixed, tmp_path / "job"))


def read_book(path):
    with zipfile.ZipFile(path) as archive:
        chapters = {name: ET.fromstring(archive.read(name)) for name in archive.namelist()
                    if re.fullmatch(r"EPUB/chapter-\d+\.xhtml", name)}
        opf = ET.fromstring(archive.read("EPUB/package.opf"))
        manifest = {item.attrib["id"]: "EPUB/" + item.attrib["href"]
                    for item in opf.find(O + "manifest")}
        spine = [manifest[item.attrib["idref"]] for item in opf.find(O + "spine")
                 if manifest[item.attrib["idref"]] in chapters]
        nav = ET.fromstring(archive.read("EPUB/nav.xhtml"))
    assert len(spine) == len(chapters) and set(spine) == set(chapters)
    return chapters, spine, nav


def heading_targets(document, chapters, nav):
    """Resolve every heading's TOC link to its actual heading element."""
    targets = {}
    for heading in (block for block in document["blocks"]
                    if block["kind"] == "heading" and not block.get("exclude_from_export")):
        label = normal(heading["text"])
        links = [link for link in nav.iter(X + "a") if normal("".join(link.itertext())) == label]
        assert len(links) == 1, heading["text"]
        filename, separator, anchor = links[0].attrib["href"].partition("#")
        assert separator and anchor, (heading["text"], links[0].attrib["href"])
        filename = "EPUB/" + filename
        assert filename in chapters
        matches = [node for node in chapters[filename].iter() if node.get("id") == anchor]
        assert len(matches) == 1 and matches[0].tag in {X + f"h{n}" for n in range(1, 7)}
        assert normal("".join(matches[0].itertext())) == label
        targets[heading["id"]] = (filename, anchor, matches[0])
    return targets


def assert_original_order(chapters, spine):
    text = normal("".join("".join(chapters[name].find(X + "body").itertext()) for name in spine))
    truth = json.loads(SOURCE.with_suffix(".truth.json").read_text(encoding="utf-8"))
    position = 0
    for page in truth["pages"]:
        for fragment in page["ordered_fragments"]:
            fragment = normal(fragment)
            start = text.find(fragment, position)
            assert start >= 0, (page["page"], fragment)
            position = start + len(fragment)


def test_mixed_page_four_stays_one_chapter_with_two_heading_anchors(mixed_job, tmp_path):
    before = {path.name: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
              for path in (mixed_job / "pages").glob("*.json")}
    document = build_document(mixed_job)
    assert len(load_job(mixed_job)["pages"]) == 4
    headings = [block for block in document["blocks"] if block["kind"] == "heading"]
    page_four = [block for block in headings if block["sources"][0]["page"] == 4]
    assert [normal(block["text"]) for block in page_four] == ["四、同页混合区域", "区域扫描文字"]
    output = tmp_path / "mixed.epub"
    export_job(mixed_job, output)
    chapters, spine, nav = read_book(output)
    assert len(chapters) == 4  # Cover and report are not counted as source content.
    targets = heading_targets(document, chapters, nav)
    assert targets[page_four[0]["id"]][0] == targets[page_four[1]["id"]][0]
    assert targets[page_four[0]["id"]][1] != targets[page_four[1]["id"]][1]
    assert all(targets[block["id"]][2].get("data-source-pages") == "4" for block in page_four)
    assert_original_order(chapters, spine)
    assert before == {path.name: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                      for path in (mixed_job / "pages").glob("*.json")}


def test_nested_headings_keep_spine_boundaries_independent_of_pdf_pages(mixed_job, tmp_path):
    document = build_document(mixed_job)
    third_page = next(block for block in document["blocks"]
                      if block["kind"] == "heading" and block["sources"][0]["page"] == 3)
    region = next(block for block in document["blocks"] if normal(block.get("text", "")) == "区域扫描文字")
    detail = next(block for block in document["blocks"] if "这里是图像中的补充段落" in block.get("text", ""))
    for block, level in ((third_page, 2), (region, 2), (detail, 3)):
        save_edit(mixed_job, block["id"], block["text"], kind="heading", level=level)
    document = build_document(mixed_job)
    output = tmp_path / "nested.epub"
    export_job(mixed_job, output)
    chapters, spine, nav = read_book(output)
    assert len(chapters) == 3  # The page-three subsection continues page two's chapter.
    targets = heading_targets(document, chapters, nav)
    page_two = next(block for block in document["blocks"]
                    if block["kind"] == "heading" and block["sources"][0]["page"] == 2)
    page_four = next(block for block in document["blocks"]
                     if block["kind"] == "heading" and normal(block["text"]) == "四、同页混合区域")
    assert targets[third_page["id"]][0] == targets[page_two["id"]][0]
    assert len({targets[block["id"]][0] for block in (page_four, region, detail)}) == 1
    assert targets[region["id"]][2].tag == X + "h2"
    assert targets[detail["id"]][2].tag == X + "h3"
    assert_original_order(chapters, spine)


def test_two_reliable_top_level_pdf_bookmarks_on_one_page_can_split(mixed_job, tmp_path):
    from pypdf import PdfReader, PdfWriter

    # Only the outline changes. All four existing page contents and OCR results
    # stay the same, so this test does not run a second OCR pass.
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(SOURCE))
    writer.add_outline_item("区域扫描文字", 3)
    source_with_outline = tmp_path / "same-page-bookmarks.pdf"
    writer.write(source_with_outline)
    inspected = inspect_pdf(source_with_outline)
    assert inspected["page_count"] == 4
    document = deepcopy(build_document(mixed_job))
    document["toc"] = inspected["toc"]
    assert {item["title"] for item in document["toc"] if item["page"] == 4 and item["level"] == 1} == {
        "四、同页混合区域", "区域扫描文字",
    }
    output = tmp_path / "bookmarked.epub"
    write_epub(document, mixed_job, output)
    chapters, spine, nav = read_book(output)
    assert len(chapters) == 5  # Four PDF pages do not impose a universal four-file limit.
    targets = heading_targets(document, chapters, nav)
    page_four = [block for block in document["blocks"]
                 if block["kind"] == "heading" and block["sources"][0]["page"] == 4]
    assert len({targets[block["id"]][0] for block in page_four}) == 2
    assert_original_order(chapters, spine)

    # Apply the real saved-edit API with this reliable outline in job metadata.
    # Changing the title must not discard its original bookmark identity.
    from local_epub.storage import atomic_json
    job = load_job(mixed_job)
    job["toc"] = inspected["toc"]
    atomic_json(mixed_job / "job.json", job)
    renamed = page_four[-1]
    save_edit(mixed_job, renamed["id"], "校正后的扫描区域标题", kind="heading", level=1)
    document = build_document(mixed_job)
    export_job(mixed_job, output)
    chapters, _, nav = read_book(output)
    assert len(chapters) == 5
    heading_targets(document, chapters, nav)
    save_edit(mixed_job, renamed["id"], "校正后的扫描区域标题", kind="heading", level=2)
    export_job(mixed_job, output)
    chapters, _, _ = read_book(output)
    assert len(chapters) == 4


def test_unlocated_bookmarks_do_not_create_title_only_chapters(mixed_job, tmp_path):
    from local_epub.storage import atomic_json
    job = load_job(mixed_job)
    job["toc"] = [{"page": 1, "level": 1, "title": "无法匹配的书签甲"},
                  {"page": 1, "level": 1, "title": "无法匹配的书签乙"}]
    atomic_json(mixed_job / "job.json", job)
    output = tmp_path / "unlocated.epub"
    report = export_job(mixed_job, output)
    chapters, spine, nav = read_book(output)
    assert len(chapters) == 4
    targets = heading_targets(build_document(mixed_job), chapters, nav)
    assert targets["bookmark-0"][0] == targets["bookmark-1"][0] == spine[0]
    assert any(i.get("code") == "bookmark_position_unknown" for i in report["content"]["issues"])
    assert_original_order(chapters, spine)
