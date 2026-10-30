"""EPUB image integrity using self-authored pixels and explicit source ranges."""
from copy import deepcopy
from pathlib import Path
import shutil
import uuid
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageDraw
import pytest

from local_epub.epub import write_epub
from local_epub.inline_graphics import text_sha

ROOT = Path(__file__).resolve().parents[1]
X = '{http://www.w3.org/1999/xhtml}'
E = '{http://www.idpf.org/2007/ops}'
OPF = '{http://www.idpf.org/2007/opf}'


def block(ident, kind, text, page, bbox=None, **extra):
    return {'id': ident, 'kind': kind, 'text': text, 'level': 1,
            'sources': [{'page': page, 'bbox': bbox or [40, 100, 370, 130]}],
            'confidence': None, 'asset': None, 'rows': None, 'issues': [], **extra}


def document(blocks):
    pages = [{'number': n, 'width': 420, 'height': 595, 'status': 'success', 'method': 'ocr',
              'blocks': deepcopy([b for b in blocks if b['sources'][0]['page'] == n])}
             for n in sorted({s['page'] for b in blocks for s in b['sources']})]
    return {'id': str(uuid.uuid4()), 'metadata': {'title': '自制图像验收', 'author': 'Test', 'language': 'zh-CN'},
            'pages': pages, 'blocks': blocks, 'toc': [], 'issues': [], 'missing_pages': [],
            'edit_conflicts': [], 'source_sha256': 'a'*64}


@pytest.fixture
def assets(tmp_path):
    job = tmp_path / 'job'; directory = job / 'assets'; directory.mkdir(parents=True)
    cover = Image.new('RGB', (210, 297), '#31506c'); draw = ImageDraw.Draw(cover)
    draw.polygon([(0, 220), (100, 60), (210, 260)], fill='#bfd0ce')
    cover.save(directory / 'cover.png')
    symbol = Image.new('RGB', (40, 40), 'white'); draw = ImageDraw.Draw(symbol)
    draw.ellipse((2, 2, 37, 37), fill='#777777'); draw.line((12, 10, 28, 28), fill='white', width=4)
    symbol.save(directory / 'symbol.png')
    return job


def cover_block():
    return block('cover', 'image', '原页图像区域', 1, [0, 0, 420, 595],
                 asset='assets/cover.png', image_role='cover')


def inline(value, offset, page=2):
    return {'asset': 'assets/symbol.png', 'offset': offset, 'text_sha256': text_sha(value),
            'source': {'page': page, 'bbox': [100, 105, 114, 119]},
            'placement': 'estimated-character-position-from-original-line', 'ocr_text_replaced': False}


def check_format(output):
    jar = ROOT / 'tools/epubcheck-5.4.0/epubcheck.jar'
    if jar.is_file() and shutil.which('java'):
        from local_epub.epub import run_epubcheck
        result = run_epubcheck(output, jar)
        assert result['status'] == 'passed', result
        assert '0 warnings' in result['log'], result['log']


def test_original_cover_used_once_and_cover_only_epub_is_valid(assets):
    doc = document([cover_block()]); before = deepcopy(doc)
    output = assets / 'cover-only.epub'
    report = write_epub(doc, assets, output, retain_page_margins=True)
    assert doc == before
    assert report['content']['cover_from_source'] is True
    assert report['content']['image_count'] == 1
    with zipfile.ZipFile(output) as book:
        assert not [name for name in book.namelist() if name.startswith('EPUB/chapter-')]
        opf = ET.fromstring(book.read('EPUB/package.opf'))
        covers = [node for node in opf.iter(OPF+'item') if node.get('properties') == 'cover-image']
        assert len(covers) == 1
        assert book.read('EPUB/'+covers[0].get('href')) == (assets/'assets/cover.png').read_bytes()
        cover = ET.fromstring(book.read('EPUB/cover.xhtml'))
        assert len(list(cover.iter(X+'img'))) == 1 and not list(cover.iter(X+'h1'))
        assert 'rendition:layout' not in book.read('EPUB/package.opf').decode()
    check_format(output)


def test_inline_images_share_text_offsets_with_source_events_and_existing_note(assets):
    prefix, suffix = '甲😀[1]乙', '后段'
    value = prefix + suffix
    paragraph = block('p2-b1', 'paragraph', value, 2)
    paragraph['sources'].append({'page': 3, 'bbox': [40, 100, 370, 130]})
    paragraph['source_spans'] = [
        {'start': 0, 'end': len(prefix), 'page': 2, 'sources': [paragraph['sources'][0]]},
        {'start': len(prefix), 'end': len(value), 'page': 3, 'sources': [paragraph['sources'][1]]}]
    paragraph['source_spans_text_sha256'] = text_sha(value)
    paragraph['inline_images'] = [inline(value, 0), inline(value, 3), inline(value, len(prefix), 3)]
    note = block('p2-note', 'footnote', '[1] 原注内容', 2, [40, 500, 370, 520])
    doc = document([cover_block(), paragraph, note]); before = deepcopy(doc)
    output = assets / 'inline.epub'; report = write_epub(doc, assets, output, retain_page_margins=True)
    assert doc == before
    assert report['content']['inline_image_count'] == 3
    assert report['content']['image_count'] == 2 and report['content']['linked_footnotes'] == 1
    with zipfile.ZipFile(output) as book:
        chapter = ET.fromstring(book.read('EPUB/chapter-0001.xhtml'))
        p = chapter.find('.//'+X+'p[@id="b-1-1"]')
        assert ''.join(p.itertext()) == value
        images = list(p.iter(X+'img'))
        assert len(images) == 3 and all(img.get('class') == 'inline-source-image' for img in images)
        assert all(img.get('data-inline-placement') == 'text-matched' for img in images)
        refs = [a for a in p.iter(X+'a') if a.get(E+'type') == 'noteref']
        assert len(refs) == 1 and ''.join(refs[0].itertext()) == '[1]'
        assert not list(refs[0].iter(X+'img'))
        boundaries = [node for node in p.iter(X+'span') if node.get('data-source-page')]
        assert [node.get('data-source-page') for node in boundaries] == ['2', '3']
        children = list(p)
        assert children.index(boundaries[1]) < children.index(images[-1])
        assert covers_not_repeated(chapter)
        css = book.read('EPUB/style.css').decode()
        assert 'height: .95em' in css
    check_format(output)


def covers_not_repeated(chapter):
    return all(img.get('src') != 'images/figure-1.png' for img in chapter.iter(X+'img'))


def test_changed_text_moves_original_symbol_to_paragraph_end_and_reports(assets):
    paragraph = block('p2-b1', 'paragraph', '人工修改后的新正文', 2)
    paragraph['inline_images'] = [inline('旧正文', 1)]
    doc = document([paragraph]); before = deepcopy(doc)
    output = assets / 'changed.epub'; report = write_epub(doc, assets, output)
    assert doc == before
    with zipfile.ZipFile(output) as book:
        chapter = ET.fromstring(book.read('EPUB/chapter-0001.xhtml'))
        p = chapter.find('.//'+X+'p[@id="b-1-1"]')
        assert p.text == paragraph['text']
        assert list(p)[-1].tag == X+'img'
        assert list(p)[-1].get('data-inline-placement') == 'paragraph-end-after-text-change'
        assert ''.join(p.itertext()) == paragraph['text']
    assert any(issue['code'] == 'inline_graphic_anchor_conflict' for issue in report['content']['issues'])


@pytest.mark.parametrize('kind', ['cover', 'inline'])
def test_every_image_uses_the_same_local_asset_boundary(assets, kind):
    outside = assets.parent / 'outside.png'; Image.new('RGB', (12, 12)).save(outside)
    if kind == 'cover':
        b = cover_block(); b['asset'] = '../outside.png'
    else:
        b = block('text', 'paragraph', '保留文字', 2)
        b['inline_images'] = [inline(b['text'], 1)]
        b['inline_images'][0]['asset'] = '../outside.png'
    with pytest.raises(ValueError, match='路径不安全'):
        write_epub(document([b]), assets, assets/'bad.epub')
