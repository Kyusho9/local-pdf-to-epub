"""Small EPUB 3 writer, independent of PDF/OCR engines. No external assets."""
from datetime import datetime, timezone
from pathlib import Path
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from .storage import atomic_json, digest
from .inline_graphics import text_sha
from .reporting import collect_issues, summarize_issues, format_issue
from .page_margins import export_document, source_spans_for_block


def _chapter_chunks(blocks, toc):
    """TOC anchors are independent of the spine's forced section boundaries."""
    normalize = lambda value: re.sub(r"\s+", "", value or "")
    bookmarks = {(entry.get("page"), normalize(entry.get("title")))
                 for entry in toc if entry.get("level", 1) == 1}
    chunks, current, previous_pages = [], [], set()
    for block in blocks:
        pages = {s["page"] for s in block.get("sources", [])}
        meaningful = not block.get("decoration") and not any(
            isinstance(i, dict) and i.get("code") == "margin_content" for i in block.get("issues", []))
        if block.get("kind") == "heading" and block.get("level", 1) == 1:
            # A validated manual rename retains its bookmark identity. Manual
            # changes of kind/level still control whether it starts a chapter.
            bookmark_title = block.get("automatic_text", block.get("text"))
            confirmed = block.get("origin") != "pdf_bookmark" and any(
                (page, normalize(bookmark_title)) in bookmarks for page in pages)
            # An unconfirmed heading within the same source page remains in
            # the current section. Real top-level PDF bookmarks may split it.
            new_page = bool(pages and previous_pages and pages.isdisjoint(previous_pages))
            if previous_pages and (confirmed or new_page):
                chunks.append(current)
                current = []
        current.append(block)
        if meaningful and pages:
            previous_pages = pages
    if current:
        chunks.append(current)
    return chunks


def clean(value):
    value = str(value or "")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", value)
    return html.escape(value, quote=True)


def xhtml(title, body, language="zh-CN", margin_reset=False, margin_attrs=None):
    reset = ' ' + margin_attrs if margin_attrs is not None else (' data-srchead="" data-srcfoot="" data-srcpage=""' if margin_reset else '')
    return '<?xml version="1.0" encoding="utf-8"?>\n' + f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{clean(language)}" lang="{clean(language)}"><head><title>{clean(title)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head><body{reset}>{body}</body></html>'


class MarginMarkup:
    """Standard EPUB source anchors, with no visible body text or fixed layout.

    The independent Vivliostyle stylesheet interprets data attributes as running
    strings. Reading systems supporting print-page maps can use page-list alone.
    Offsets refer to unescaped Unicode text, never to guessed PDF page numbers.
    """
    def __init__(self, plan):
        self.plan = plan or {}
        self.pages = {p['number']: p for p in self.plan.get('pages', [])}
        self.seen = set()
        self.navigation = []

    def attributes(self, page_number):
        page = self.pages.get(page_number, {})
        values = (page.get('header_text', ''), page.get('footer_body_text', ''),
                  page.get('page_number_text', ''))
        return ' '.join(f'{key}="{clean(value)}"' for key, value in zip(
            ('data-srchead', 'data-srcfoot', 'data-srcpage'), values))

    def block_attributes(self, block):
        if not self.plan:
            return ''
        events = self.plan.get('blocks', {}).get(block['id'], {}).get('events') or [{'page': None}]
        return self.attributes(events[0].get('page'))

    def event(self, page_number, filename, anchor_only=False):
        page = self.pages.get(page_number, {})
        attrs = self.attributes(page_number)
        if anchor_only:
            # The block sets strings before pagination. An inline child at
            # its first character would otherwise lag one reading page.
            attrs += ' data-margin-anchor="true"'
        number = page.get('page_number') or {}
        attrs += f' data-page-number-origin="{clean(number.get("provenance", ""))}"'
        if page_number is not None:
            attrs += f' data-source-page="{page_number}"'
        label = page.get('page_number_text', '')
        if label and page_number not in self.seen:
            anchor = f'source-page-{page_number}'
            attrs += (f' id="{anchor}" epub:type="pagebreak" role="doc-pagebreak"'
                      f' aria-label="{clean(label)}" title="{clean(label)}"')
            self.navigation.append((label, filename + '#' + anchor))
            self.seen.add(page_number)
        return '<span class="source-boundary" ' + attrs + '></span>'

    def text(self, block, filename, note_links=None, inline_images=None):
        """Merge source events, note tokens and original inline image markers.

        Offsets always address the unchanged Unicode text. Image placement must
        already have been checked against its text digest by the writer. A
        position inside a linked [1] token is moved to that token's end so the
        image cannot corrupt the existing reference or imply a new note link.
        """
        value = block.get('text', '')
        entry = self.plan.get('blocks', {}).get(block['id'], {}) if self.plan else {}
        events = (entry.get('events') or [{'offset': 0, 'page': None}]) if self.plan else []
        page_events = {}
        previous = 0
        for event in events:
            offset = event['offset']
            if type(offset) is not int or not previous <= offset <= len(value):
                raise ValueError('原页来源文字范围无效：' + block['id'])
            page_events.setdefault(offset, []).append(event)
            previous = offset
        notes = {}
        for match in re.finditer(r'\[\d+\]', value):
            link = (note_links or {}).get(match.start())
            if link is not None and not any(match.start() < offset < match.end() for offset in page_events):
                notes[match.start()] = (match.end(), link)
        pictures = {}
        for item in inline_images or []:
            offset = item['offset']
            if type(offset) is not int or not 0 <= offset <= len(value):
                raise ValueError('行内原图文字位置无效：' + block['id'])
            for start, (end, _) in notes.items():
                if start < offset < end:
                    offset = end
                    break
            pictures.setdefault(offset, []).append(item['html'])
        positions = sorted(set(page_events) | set(pictures) | set(notes) | {len(value)})
        output, cursor = [], 0
        while cursor <= len(value):
            for event in page_events.get(cursor, []):
                output.append(self.event(event.get('page'), filename, cursor == 0))
            output.extend(pictures.get(cursor, []))
            if cursor == len(value):
                break
            if cursor in notes:
                end, link = notes[cursor]
                output.append(f'<a epub:type="noteref" href="{clean(link)}">{clean(value[cursor:end])}</a>')
                cursor = end
            else:
                end = next(offset for offset in positions if offset > cursor)
                output.append(clean(value[cursor:end]))
                cursor = end
        return ''.join(output)

    def before_object(self, block, filename):
        if not self.plan:
            return ''
        entry = self.plan.get('blocks', {}).get(block['id'], {})
        events = entry.get('events') or [{'offset': 0, 'page': None}]
        return self.event(events[0].get('page'), filename, anchor_only=True)


def _internal_validate(path):
    errors = []
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if infos[0].filename != "mimetype" or infos[0].compress_type != zipfile.ZIP_STORED or z.read("mimetype") != b"application/epub+zip":
            errors.append("mimetype 文件位置、内容或压缩方式不正确")
        for n in z.namelist():
            if n.endswith((".xhtml", ".opf", ".xml", ".svg")):
                try:
                    ET.fromstring(z.read(n))
                except ET.ParseError as e:
                    errors.append(n + ": " + str(e))
        if "EPUB/nav.xhtml" not in z.namelist():
            errors.append("缺少 EPUB 3 导航")
    return {"status": "passed" if not errors else "failed", "scope": "ZIP/mimetype/XML/navigation presence; not EPUBCheck", "errors": errors}


def run_epubcheck(path, jar=None):
    jar = jar or os.environ.get("LOCAL_EPUB_EPUBCHECK")
    if not jar:
        return {"status": "not_run", "message": "未配置 EPUBCheck；内置结构检查不等同 EPUBCheck。可通过 --epubcheck /path/epubcheck.jar 配置。"}
    java = shutil.which("java")
    if not java or not Path(jar).is_file():
        return {"status": "unavailable", "message": "需要本地 Java 和有效 EPUBCheck JAR；本功能不会自动下载。"}
    try:
        p = subprocess.run([java, "-jar", str(jar), str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        return {"status": "passed" if p.returncode == 0 else "failed", "returncode": p.returncode, "log": p.stdout + p.stderr}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"status": "failed", "message": str(e)}


def write_epub(document, job_dir, output, font_size=1.0, line_height=1.8, epubcheck=None,
               retain_page_margins=False, include_page_info=None):
    if include_page_info:
        raise ValueError("原页信息附录已移除；请使用 --retain-page-margins 保留供兼容阅读器显示的页眉页脚。")
    document = export_document(document, retain_page_margins)
    root = Path(job_dir).resolve()
    target = Path(output).resolve()
    if target.suffix.lower() != ".epub":
        raise ValueError("输出文件必须使用 .epub 扩展名")
    if not 0.7 <= float(font_size) <= 2 or not 1.1 <= float(line_height) <= 3:
        raise ValueError("基础字号比例或行距超出范围")
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = document["metadata"]
    title = metadata.get("title") or "未命名文档"
    author = metadata.get("author") or "佚名"
    language = metadata.get("language") or "zh-CN"
    if not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language):
        raise ValueError("语言代码无效，例如 zh-CN 或 en")
    assets, asset_ids, manifest = {}, {}, []
    def register_image(asset, cover=False):
        if not isinstance(asset, str) or not asset:
            raise ValueError("原图资源路径为空或无效。")
        src = (root / asset).resolve()
        if not src.is_relative_to(root) or not src.is_file():
            raise ValueError("图片路径不安全或缺失：" + str(asset))
        suffix = src.suffix.lower()
        media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix)
        if not media:
            raise ValueError("仅支持本地 PNG/JPEG 插图")
        if asset not in asset_ids:
            dest = f"images/figure-{len(assets) + 1}{suffix}"
            asset_ids[asset] = dest
            assets[dest] = src
            manifest.append((f"image{len(assets)}", dest, media, "cover-image" if cover else ""))
        return asset_ids[asset]

    exported_blocks = [b for b in document["blocks"] if not b.get("exclude_from_export")]
    cover_block = next((b for b in exported_blocks if b.get('kind') == 'image'
                        and b.get('image_role') == 'cover' and b.get('asset')
                        and {source.get('page') for source in b.get('sources', [])} == {1}), None)
    cover_asset = register_image(cover_block['asset'], cover=True) if cover_block else None
    body_blocks = [b for b in exported_blocks if b is not cover_block]
    chunks = _chapter_chunks(body_blocks, document.get("toc", []))
    if not chunks and cover_block is None:
        raise ValueError("文档没有可导出的正文；请检查原页与历史人工修订。")
    inline_image_count = 0

    def inline_markup(block):
        nonlocal inline_image_count
        value = block.get('text', '')
        items = []
        for item in block.get('inline_images', []):
            image_src = register_image(item.get('asset'))
            offset = item.get('offset')
            valid = (item.get('text_sha256') == text_sha(value)
                     and type(offset) is int and 0 <= offset <= len(value))
            if not valid:
                offset = len(value)
                source = item.get('source') or {}
                issue = {'code': 'inline_graphic_anchor_conflict',
                         'message': '人工修订后原图标的字符位置不再可靠，已将原图保留在该段末尾；OCR文字未改写，请回查原页。',
                         'page': source.get('page'), 'bbox': source.get('bbox'),
                         'block_id': block['id'], 'type': 'heuristic', 'severity': 'warning'}
                if not any(old.get('code') == issue['code'] and old.get('bbox') == issue['bbox']
                           for old in block.get('issues', []) if isinstance(old, dict)):
                    block.setdefault('issues', []).append(issue)
            source = item.get('source') or {}
            state = 'text-matched' if valid else 'paragraph-end-after-text-change'
            attrs = (f'class="inline-source-image" src="{image_src}" alt="原文行内图标" '
                     f'data-source-page="{clean(source.get("page"))}" data-inline-placement="{state}" '
                     f'title="{clean("原图；字符位置为几何估计，请核对" if valid else "原图；文字已改动，暂置段末，请核对")}"')
            items.append({'offset': offset, 'html': '<img ' + attrs + '/>'})
            inline_image_count += 1
        return items
    block_locations = {}
    notes = {}
    references = {}
    for ci, chunk in enumerate(chunks, 1):
        for bi, block in enumerate(chunk, 1):
            block_locations[block["id"]] = (f"chapter-{ci:04d}.xhtml", f"b-{ci}-{bi}")
            if block.get("kind") == "footnote":
                match = re.match(r"^\s*(\[\d+\])", block.get("text", ""))
                if match and block.get("sources"):
                    key = (block["sources"][0]["page"], match.group(1))
                    notes.setdefault(key, []).append(block)
    for ci, chunk in enumerate(chunks, 1):
        for bi, block in enumerate(chunk, 1):
            if block.get("kind") != "paragraph":
                continue
            spans = source_spans_for_block(block) or []
            for match in re.finditer(r"\[\d+\]", block.get("text", "")):
                origins = {span['page'] for span in spans
                           if span['start'] <= match.start() and match.end() <= span['end']}
                if len(origins) != 1:
                    continue
                key = (origins.pop(), match.group())
                if len(notes.get(key, [])) == 1:
                    references.setdefault(key, []).append({'block': block, 'offset': match.start()})
    linked_notes = {key: values[0] for key, values in notes.items() if len(values) == 1 and len(references.get(key, [])) == 1}
    contents = {}
    spine = ["cover"]
    toc_entries = [("封面", "cover.xhtml", 1)] if cover_block else []
    margins = MarginMarkup(document.get('margin_plan'))
    for ci, chunk in enumerate(chunks, 1):
        name = f"chapter-{ci:04d}.xhtml"
        chapter_title = next((b.get("text") for b in chunk if b["kind"] == "heading"), title if ci == 1 else f"正文 {ci}")
        if not any(b["kind"] == "heading" for b in chunk) and ci == 1:
            toc_entries.append((chapter_title, name, 1))
        body = []
        for bi, b in enumerate(chunk, 1):
            bid = f"b-{ci}-{bi}"
            source_pages = sorted(set(s["page"] for s in b.get("sources", [])))
            source = ",".join(map(str, source_pages))
            attrs = f'id="{bid}" data-source-pages="{source}"'
            if retain_page_margins:
                attrs += ' ' + margins.block_attributes(b)
            kind = b.get("kind", "paragraph")
            note_links = {}
            if kind == "paragraph":
                for key, note in linked_notes.items():
                    if references[key][0]["block"]["id"] == b["id"]:
                        href, anchor = block_locations[note["id"]]
                        note_links[references[key][0]['offset']] = href + '#' + anchor
            text = (clean(b.get("text", "")) if kind in ('image', 'table')
                    else margins.text(b, name, note_links, inline_markup(b)))
            if kind in ('image', 'table'):
                # A separate zero-text anchor keeps attributes out of img alt
                # and table cells while preserving the original object order.
                body.append(margins.before_object(b, name))
            if kind == "image":
                asset = b.get("asset")
                if not asset:
                    body.append(f'<p {attrs}>[原图缺失；请回查第 {source} 页]</p>')
                    continue
                image_src = register_image(asset)
                image_class = ' class="inline-symbol-fallback"' if b.get('image_role') == 'inline-symbol' else ''
                body.append(f'<figure {attrs}{image_class}><img src="{image_src}" alt="{text or "原文插图"}"/>' + (f'<figcaption>{text}</figcaption>' if text else '') + '</figure>')
            elif kind == "table" and b.get("rows"):
                rows = ''.join('<tr>' + ''.join(f'<td>{clean(cell)}</td>' for cell in row) + '</tr>' for row in b["rows"])
                body.append(f'<table {attrs}><tbody>{rows}</tbody></table>')
            elif kind == "heading":
                level = max(1, min(6, b.get("level", 1)))
                toc_entries.append((b.get("text", ""), name + "#" + bid, level))
                body.append(f'<h{level} {attrs}>{text}</h{level}>')
            elif kind == "footnote":
                backlink = ""
                for key, note in linked_notes.items():
                    if note["id"] == b["id"]:
                        href, anchor = block_locations[references[key][0]["block"]["id"]]
                        backlink = f' <a href="{href}#{anchor}" aria-label="返回正文">↩</a>'
                body.append(f'<aside {attrs} epub:type="footnote"><p>{text}{backlink}</p></aside>')
            else:
                body.append(f'<p {attrs}>{text}</p>')
        contents[name] = xhtml(chapter_title, "\n".join(body), language, retain_page_margins,
                               margins.block_attributes(chunk[0]) if retain_page_margins else None)
        ident = f"chapter{ci}"
        manifest.append((ident, name, "application/xhtml+xml", ""))
        spine.append(ident)
    if cover_asset:
        # Use original pixels once, as an ordinary image in a reflowable EPUB.
        # No fixed-layout metadata or additional generated text cover is added.
        contents["cover.xhtml"] = xhtml(title, f'<section epub:type="cover" class="image-cover" data-source-pages="1"><img src="{cover_asset}" alt="{clean(title)}：原书封面"/></section>', language, retain_page_margins)
    else:
        contents["cover.xhtml"] = xhtml(title, f'<section epub:type="cover" class="cover"><h1>{clean(title)}</h1><p>{clean(author)}</p><p class="small">本地 PDF 转 EPUB</p></section>', language, retain_page_margins)
    manifest.insert(0, ("cover", "cover.xhtml", "application/xhtml+xml", ""))
    appendix = ['<h1>转换说明与待核对内容</h1>', '<p>格式合法不代表文字准确。OCR 分数为引擎输出，不是全文准确率；请对照源 PDF 校对。</p>']
    if document["missing_pages"]:
        appendix.append('<h2>本书是部分结果</h2><ul>')
        appendix.extend(f'<li>第 {p["number"]} 页：{clean(p["status"])} {clean(p.get("error"))}</li>' for p in document["missing_pages"])
        appendix.append('</ul>')
    if document["edit_conflicts"]:
        appendix.append('<h2>未自动应用的人工修订（原样保留）</h2>')
        for conflict in document["edit_conflicts"]:
            appendix.append(f'<h3>{clean(conflict["block_id"])}</h3><p>{clean(conflict["reason"])}</p><blockquote>{clean(conflict["edit"].get("text"))}</blockquote>')
    resolved_note_boxes = {(note["sources"][0]["page"], tuple(note["sources"][0].get("bbox") or [])) for note in linked_notes.values()}
    def unresolved(issue):
        return not (isinstance(issue, dict) and issue.get("code") == "footnote_unlinked" and (issue.get("page"), tuple(issue.get("bbox") or [])) in resolved_note_boxes)
    report_document = {**document, "issues": [i for i in document.get("issues", []) if unresolved(i)],
                       "blocks": [{**b, "issues": [i for i in b.get("issues", []) if unresolved(i)]} for b in document["blocks"]]}
    summary = summarize_issues(report_document)
    issues = summary["issues"]
    if summary["margin_omitted_count"]:
        appendix.append(f'<p>按输出设置未保留 {summary["margin_omitted_count"]} 处已识别页眉、页脚或页码；原始内容仍保留在任务中。</p>')
    if retain_page_margins:
        appendix.append('<p>已保留原页眉、页脚及有效页码映射。使用本地兼容阅读器（Vivliostyle 和本应用页边样式）显示；每个重排阅读页采用起始正文来源的页眉、页脚和页码。原 PDF 一页可能延伸为多个阅读页，同一阅读页也可能含多个原页。Calibre 的“纸书页码”页脚仅支持页码，可能显示范围。Flow 与图书 App 尚未验证支持此页边样式。</p>')
    if summary["excluded"]:
        count = len(summary["excluded"])
        appendix.append(f'<p>依照历史人工决定，另有 {count} 处内容未进入正文；原文字、来源和修订记录仍保留在任务中。</p>')
    number_suggestions = document.get("page_number_suggestions", [])
    number_repairs = document.get("page_number_repairs", [])
    number_conflicts = document.get("page_number_repair_conflicts", [])
    number_notice = ("页码建议仅依据邻页信息，原书可能故意留空；未经主动确认不会补写。"
                     "确认值属于人工推算，不是 OCR 识别结果。" +
                     ("有效确认值已纳入页脚映射，可在兼容阅读器页脚显示。" if retain_page_margins else
                      "当前未保留页边内容，确认值不会进入输出页脚。"))
    if number_suggestions or number_repairs or number_conflicts:
        appendix.append('<h2>页码校对记录</h2><p>' + clean(number_notice) + '</p>')
        if number_suggestions:
            appendix.append(f'<p>有 {len(number_suggestions)} 处页码建议尚未确认，请在应用中回查原页。</p>')
            appendix.append('<ul>' + ''.join(
                f'<li>原 PDF 第 {clean(suggestion.get("page"))} 页：待确认页码建议“{clean(suggestion.get("label"))}”。'
                f'{clean(suggestion.get("message"))}</li>'
                for suggestion in number_suggestions) + '</ul>')
        if number_repairs:
            appendix.append('<ul>' + ''.join(
                f'<li>原 PDF 第 {clean(record.get("page"))} 页：用户确认的人工推算页码“{clean(record.get("label"))}”（非 OCR）。</li>'
                for record in number_repairs) + '</ul>')
        if number_conflicts:
            appendix.append('<h3>尚未应用的页码修订</h3><ul>' + ''.join(
                f'<li>原 PDF 第 {clean(conflict.get("page"))} 页：{clean(conflict.get("reason"))}；原人工推算记录保留，需重新确认。</li>'
                for conflict in number_conflicts) + '</ul>')
    if summary["review"]:
        appendix.append('<h2>待核对内容</h2><ul>')
        appendix.extend('<li>' + clean(format_issue(item)) + '</li>' for item in summary["review"])
        appendix.append('</ul>')
    else:
        appendix.append('<h2>待核对内容</h2><p>没有额外待核对项；这不代表已验证全文文字准确。</p>')
    if summary["processing"]:
        appendix.append('<h2>处理记录</h2><ul>')
        appendix.extend('<li>' + clean(format_issue(item)) + '</li>' for item in summary["processing"])
        appendix.append('</ul>')
    contents["report.xhtml"] = xhtml("转换说明", ''.join(appendix), language, retain_page_margins)
    manifest.append(("report", "report.xhtml", "application/xhtml+xml", ""))
    spine.append("report")
    # Valid nested ordered lists: levels are normalized when PDF skips a heading depth.
    nodes, stack = [], []
    for label, href, level in toc_entries + [("转换说明与缺页报告", "report.xhtml", 1)]:
        node = {"title": label, "href": href, "children": [], "level": level}
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        (stack[-1]["children"] if stack else nodes).append(node)
        stack.append(node)
    def nav_list(items):
        return '<ol>' + ''.join(f'<li><a href="{n["href"]}">{clean(n["title"])}</a>' + (nav_list(n["children"]) if n["children"] else '') + '</li>' for n in items) + '</ol>'
    navigation = '<nav epub:type="toc" id="toc"><h1>目录</h1>' + nav_list(nodes) + '</nav>'
    if margins.navigation:
        navigation += '<nav epub:type="page-list" id="source-pages" hidden="hidden"><h2>原书页码</h2><ol>' + ''.join(
            f'<li><a href="{clean(href)}">{clean(label)}</a></li>' for label, href in margins.navigation) + '</ol></nav>'
    contents["nav.xhtml"] = xhtml("目录", navigation, language)
    manifest.extend([("nav", "nav.xhtml", "application/xhtml+xml", "nav"), ("css", "style.css", "text/css", "")])
    contents["style.css"] = f'''body {{ font-family: serif; font-size: {float(font_size)}em; line-height: {float(line_height)}; margin: 5%; overflow-wrap: break-word; }}
p {{ margin: 0.5em 0; text-align: justify; }}
h1,h2,h3,h4,h5,h6 {{ line-height: 1.4; page-break-after: avoid; text-align: start; }}
img {{ max-width: 100%; height: auto; }} figure {{ margin: 1em 0; text-align: center; }}
figcaption,.small {{ font-size: 0.85em; }} aside {{ border-top: 1px solid; font-size: 0.9em; margin-top: 1em; }}
table {{ border-collapse: collapse; max-width: 100%; }} td {{ border: 1px solid; padding: .3em; }}
.cover {{ text-align: center; padding-top: 20%; }} .cover p {{ text-align: center; }}
.image-cover {{ text-align: center; padding: 0; break-inside: avoid; }}
.image-cover img {{ max-width: 100%; max-height: 80vh; width: auto; height: auto; }}
img.inline-source-image, .inline-symbol-fallback img {{ height: .95em; width: auto; max-width: none; vertical-align: -.08em; margin: 0 .06em; }}
'''
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = ''.join(f'<item id="{i}" href="{h}" media-type="{m}"' + (f' properties="{p}"' if p else '') + '/>' for i, h, m, p in manifest)
    contents["package.opf"] = f'''<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{clean(language)}"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="bookid">urn:uuid:{clean(document["id"])}</dc:identifier><dc:title>{clean(title)}</dc:title><dc:creator>{clean(author)}</dc:creator><dc:language>{clean(language)}</dc:language><meta property="dcterms:modified">{modified}</meta></metadata><manifest>{items}</manifest><spine>{''.join(f'<itemref idref="{i}"/>' for i in spine)}</spine></package>'''
    fd, tmp = tempfile.mkstemp(suffix=".epub", dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w") as z:
            z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>', compress_type=zipfile.ZIP_DEFLATED)
            for name, data in contents.items():
                z.writestr("EPUB/" + name, data.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
            for name, src in assets.items():
                z.write(src, "EPUB/" + name, compress_type=zipfile.ZIP_DEFLATED)
        internal = _internal_validate(tmp)
        if internal["status"] != "passed":
            raise RuntimeError("EPUB 内置检查失败：" + str(internal["errors"]))
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    external = run_epubcheck(target, epubcheck)
    report_path = target.with_suffix(".report.json")
    report = {"output": str(target), "sha256": digest(target), "report": str(report_path), "complete": not document["missing_pages"], "format": {"internal": internal, "epubcheck": external}, "content": {"status": "requires_human_review", "message": "未计算或承诺识别准确率；请核对疑点和原页。", "issues": issues, "source_issues": collect_issues(document, include_excluded=True), "review": summary["review"], "processing": summary["processing"], "excluded_blocks": summary["excluded"], "export_conflicts": document.get("export_conflicts", []), "missing_pages": document["missing_pages"], "edit_conflicts": document["edit_conflicts"], "block_count": len(exported_blocks), "source_block_count": len(document["blocks"]), "image_count": len(assets), "linked_footnotes": len(linked_notes)}, "source_sha256": document["source_sha256"]}
    report["export_options"] = {"schema_version": 2, "font_size": font_size, "line_height": line_height,
                                "language": language, "retain_page_margins": bool(retain_page_margins)}
    report["content"].update(inline_image_count=inline_image_count, cover_from_source=bool(cover_block),
                             margin_omitted_count=summary["margin_omitted_count"],
                             page_number_suggestions=number_suggestions, page_number_repairs=number_repairs,
                             page_number_repair_conflicts=number_conflicts, page_number_notice=number_notice)
    if retain_page_margins:
        report['content']['running_margins'] = {
            'profile': 'vivliostyle-2.45.2', 'mapping': 'first-source-on-reading-page',
            'print_page_labels': [{'label': label, 'href': href} for label, href in margins.navigation],
            'issues': document.get('margin_plan', {}).get('issues', []),
            'reader_required': '本地兼容阅读器（Vivliostyle + 页边样式）；Calibre 仅页码',
        }
    atomic_json(report_path, report)
    return report
