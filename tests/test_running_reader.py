"""EPUB marker integrity and actual loopback reader-serving boundaries."""
import http.client
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image
import pytest
from reportlab.pdfgen import canvas

from local_epub.epub import write_epub
from local_epub.page_number_repairs import clear_page_number_repair, save_page_number_repair
from local_epub.pipeline import build_document, create_job, export_job, merge_blocks, run_job, save_edit
from local_epub.reader import create_session, _book_assets
from local_epub.storage import atomic_json, read_json
from test_margin_plan import native_job, body, snapshot

ROOT = Path(__file__).resolve().parents[1]
X = "{http://www.w3.org/1999/xhtml}"
E = "{http://www.idpf.org/2007/ops}"


def chapters(output):
    with zipfile.ZipFile(output) as book:
        return [ET.fromstring(book.read(name)) for name in book.namelist()
                if name.startswith("EPUB/chapter-") and name.endswith(".xhtml")]


@pytest.mark.parametrize("wrapped", [False, True])
def test_footnote_reference_cannot_replace_text_inside_margin_attributes(tmp_path, wrapped):
    source = tmp_path / "notes.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(420, 595))
    for number in (1, 2):
        pdf.setFont("Helvetica", 9)
        pdf.drawString(40, 573, "Repeated book header")
        pdf.drawCentredString(210, 22, str(number))
        pdf.setFont("Helvetica", 13)
        pdf.drawString(40, 490, "A body reference [1] stays linked to its note.")
        pdf.drawString(40, 475 if wrapped else 445, "Other readable body content on this page.")
        pdf.drawString(40, 400, "The normal font size has enough body evidence.")
        pdf.drawString(40, 355, "Final body line before the actual footnote.")
        pdf.setFont("Helvetica", 9)
        pdf.drawString(40, 70, "[1] Actual note content.")
        pdf.showPage()
    pdf.save()
    job = tmp_path / "notes-task"
    create_job(source, job, {"mode": "text"})
    assert run_job(job, tmp_path / "no-models")["status"] == "complete"
    header = next(b for b in build_document(job)["blocks"] if b.get("page_margin_role") == "header")
    save_edit(job, header["id"], "Book [1] header")
    output = tmp_path / "notes.epub"
    report = export_job(job, output, retain_page_margins=True)
    roots = chapters(output)
    assert report["content"]["linked_footnotes"] == 2
    assert any(node.get("data-srchead") == "Book [1] header" for root in roots for node in root.iter())
    references = [n for root in roots for n in root.iter(X + "a") if n.get(E + "type") == "noteref"]
    assert len(references) == 2 and all("".join(n.itertext()) == "[1]" for n in references)


def test_real_crosspage_merge_links_each_repeated_marker_to_its_own_source_note(tmp_path):
    # The two-column PDF places page one's note earlier in reading order than
    # its final right-column paragraph, so the actual merge API can join the
    # adjacent paragraphs without moving or deleting the footnote.
    source = tmp_path / "two-column-notes.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(420, 595))
    for number in (1, 2):
        pdf.setFont("Helvetica", 9)
        pdf.drawString(40, 573, "Repeated header")
        pdf.drawCentredString(210, 22, str(number))
        pdf.setFont("Helvetica", 13)
        for line, y in enumerate((490, 445, 400, 355)):
            text = "Left [1] target" if number == 2 and line == 0 else f"Left body line {line}"
            pdf.drawString(40, y, text)
        for line, y in enumerate((490, 400, 120)):
            text = "Right [1] target" if number == 1 and line == 2 else f"Right body line {line}"
            pdf.drawString(235, y, text)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(40, 70, f"[1] Note for source page {number}.")
        pdf.showPage()
    pdf.save()
    job = tmp_path / "two-column-task"
    create_job(source, job, {"mode": "text"})
    assert run_job(job, tmp_path / "no-models")["status"] == "complete"
    document = build_document(job)
    first = next(b for b in document["blocks"] if b["text"] == "Right [1] target")
    second = next(b for b in document["blocks"] if b["text"] == "Left [1] target")
    merge_blocks(job, first["id"], second["id"])
    merged = next(b for b in build_document(job)["blocks"] if b["id"] == first["id"])
    assert [s["page"] for s in merged["source_spans"]] == [1, 2]
    output = tmp_path / "two-column-notes.epub"
    report = export_job(job, output, retain_page_margins=True)
    assert report["content"]["linked_footnotes"] == 2
    paragraph = next(n for root in chapters(output) for n in root.iter(X + "p")
                     if "".join(n.itertext()) == merged["text"])
    references = [n for n in paragraph.iter(X + "a") if n.get(E + "type") == "noteref"]
    assert len(references) == 2 and references[0].get("href") != references[1].get("href")
    with zipfile.ZipFile(output) as archive:
        for source_page, reference in enumerate(references, 1):
            filename, anchor = reference.get("href").split("#")
            root = ET.fromstring(archive.read("EPUB/" + filename))
            note = next(n for n in root.iter(X + "aside") if n.get("id") == anchor)
            assert note.get("data-source-pages") == str(source_page)
            assert f"Note for source page {source_page}." in "".join(note.itertext())


def test_unicode_crosspage_markup_reset_and_stale_checkpoint_preserve_manual_text(native_job, tmp_path):
    document = build_document(native_job)
    first, second = body(document, 1)[-1], body(document, 2)[0]
    corrected = first["text"] + " 中文😀<&"
    save_edit(native_job, first["id"], corrected)
    merge_blocks(native_job, first["id"], second["id"])
    merged = next(b for b in build_document(native_job)["blocks"] if b["id"] == first["id"])
    cached = snapshot(native_job)
    enabled = tmp_path / "on.epub"
    disabled = tmp_path / "off.epub"
    export_job(native_job, enabled, retain_page_margins=True)
    export_job(native_job, disabled, retain_page_margins=False)
    texts = lambda path: ["".join(n.itertext()) for root in chapters(path) for n in root.iter()
                          if n.get("data-source-pages") is not None]
    assert texts(enabled) == texts(disabled)
    paragraph = next(n for root in chapters(enabled) for n in root.iter(X + "p")
                     if "".join(n.itertext()) == merged["text"])
    events = list(paragraph)
    assert [n.get("data-source-page") for n in events] == ["1", "2"]
    assert events[0].tail == merged["text"][:merged["source_spans"][1]["start"]]
    assert events[1].tail == second["text"]
    assert snapshot(native_job) == cached
    changed = "不能确定新的跨页位置。" + merged["text"]
    save_edit(native_job, first["id"], changed)
    export_job(native_job, enabled, retain_page_margins=True)
    paragraph = next(n for root in chapters(enabled) for n in root.iter(X + "p") if "".join(n.itertext()) == changed)
    assert list(paragraph)[0].get("data-source-page") is None
    assert list(paragraph)[0].get("data-srcpage") == ""
    assert any(n.get("data-source-page") == "2" and n.get("data-srcpage") == "21"
               for root in chapters(enabled) for n in root.iter())
    # A changed automatic draft invalidates old merges and text mappings; the
    # user's entire saved edit survives as a conflict instead of being applied.
    page_path = native_job / "pages/0001.json"
    page = read_json(page_path)
    next(b for b in page["blocks"] if b["id"] == first["id"])["text"] += " new extraction"
    atomic_json(page_path, page)
    refreshed = build_document(native_job)
    assert any(c["edit"]["text"] == changed for c in refreshed["edit_conflicts"])
    assert all({s["page"] for s in b["source_spans"]} <= {p["page"] for p in b["sources"]}
               for b in refreshed["blocks"] if b.get("source_spans"))


def test_object_attributes_and_hidden_page_list_are_valid_and_body_text_unchanged(native_job, tmp_path):
    document = build_document(native_job)
    asset = native_job / "assets/test-image.png"
    Image.new("RGB", (40, 30), "white").save(asset)
    image = {"id": "test-figure", "kind": "image", "text": 'Image < & " 😀', "asset": "assets/test-image.png",
             "sources": [{"page": 2, "bbox": [40, 120, 200, 250]}], "issues": []}
    table = {"id": "test-table", "kind": "table", "text": "", "rows": [["甲<&", "😀"], ["3", "4"]],
             "sources": [{"page": 3, "bbox": [40, 120, 200, 250]}], "issues": []}
    document["blocks"] += [image, table]
    output = tmp_path / "objects.epub"
    write_epub(document, native_job, output, retain_page_margins=True)
    roots = chapters(output)
    rendered_image = next(n for root in roots for n in root.iter(X + "img"))
    assert rendered_image.get("alt") == image["text"]
    assert next(n for root in roots for n in root.iter(X + "td")).text == "甲<&"
    with zipfile.ZipFile(output) as archive:
        nav = ET.fromstring(archive.read("EPUB/nav.xhtml"))
        pages = next(n for n in nav.iter(X + "nav") if n.get(E + "type") == "page-list")
        assert pages.get("hidden") == "hidden"
        assert ["".join(n.itertext()) for n in pages.iter(X + "a")] == ["20", "21", "22", "23"]
        for link in pages.iter(X + "a"):
            filename, anchor = link.get("href").split("#")
            target = ET.fromstring(archive.read("EPUB/" + filename))
            assert sum(n.get("id") == anchor for n in target.iter()) == 1


def test_confirmed_missing_number_has_human_origin_and_clear_removes_its_anchor(tmp_path):
    job = tmp_path / "gap"
    create_job(ROOT / "tests/fixtures/04_page_number_gap.pdf", job, {"mode": "text"})
    assert run_job(job, tmp_path / "no-models")["status"] == "complete"
    save_page_number_repair(job, 3, "22")
    output = tmp_path / "confirmed.epub"
    export_job(job, output, retain_page_margins=True)
    markers = [n for root in chapters(output) for n in root.iter() if n.get("data-source-page") == "3"]
    assert markers and all(n.get("data-srcpage") == "22" and n.get("data-page-number-origin") == "user_confirmed_inference" for n in markers)
    clear_page_number_repair(job, 3)
    export_job(job, output, retain_page_margins=True)
    markers = [n for root in chapters(output) for n in root.iter() if n.get("data-source-page") == "3"]
    assert markers and all(n.get("data-srcpage") == "" and n.get("id") != "source-page-3" for n in markers)


def test_real_loopback_server_limits_host_paths_and_preserves_epub_bytes(native_job, tmp_path):
    output = tmp_path / "served.epub"
    export_job(native_job, output, retain_page_margins=True)
    session = create_session(output)
    parsed = urlsplit(session["url"])
    prefix = parsed.path.rsplit("viewer/index.html", 1)[0]
    assert session["server"].server_address[0] == "127.0.0.1"
    def fetch(path, host=None):
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            connection.request("GET", path, headers={} if host is None else {"Host": host})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()
    try:
        status, headers, data = fetch(parsed.path)
        assert status == 200 and b"Vivliostyle" in data
        assert "connect-src 'self'" in headers["Content-Security-Policy"]
        assert fetch(parsed.path, "example.invalid")[0] == 403
        assert fetch("/wrong-token/viewer/index.html")[0] == 404
        assert fetch(prefix + "book/%2e%2e/manifest.json")[0] == 404
        assert fetch(prefix + "book//EPUB/package.opf")[0] == 404
        with zipfile.ZipFile(output) as archive:
            assert fetch(prefix + "book/EPUB/chapter-0001.xhtml")[2] == archive.read("EPUB/chapter-0001.xhtml")
    finally:
        session["close"]()


def test_reader_rejects_scripted_or_unsafe_book_paths(tmp_path):
    for name, contents in (("EPUB/a.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><script>alert(1)</script></html>'),
                           ("EPUB/a.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><p onclick="alert(1)">x</p></html>'),
                           ("../outside.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"/>')):
        output = tmp_path / "unsafe.epub"
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", "<container/>")
            archive.writestr(name, contents)
        with pytest.raises(ValueError):
            _book_assets(output)
