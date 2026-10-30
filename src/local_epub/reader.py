"""Independent, unmodified Vivliostyle Viewer served strictly on loopback.

No conversion/inference traffic uses this server. The optional reading action
serves one EPUB and bundled static reader files behind an unpredictable URL.
It does not expose the user's filesystem or listen on the local network.
"""
from __future__ import annotations

import atexit
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path, PurePosixPath
import secrets
import sys
import threading
from urllib.parse import quote, unquote, urlsplit
import webbrowser
import xml.etree.ElementTree as ET
import zipfile

VERSION = '2.45.2'
_servers = []
_lock = threading.Lock()


def resource_root():
    base = (Path(sys._MEIPASS) / 'reader' if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[2] / 'vendor' / 'reader')
    return base / ('vivliostyle-' + VERSION)


def _safe_path(name):
    return (bool(name) and not name.startswith('/') and '\\' not in name and ':' not in name
            and all(part not in ('', '.', '..') for part in name.split('/')))


def _reader_assets(root):
    manifest_path = root / 'manifest.json'
    if not manifest_path.is_file():
        raise ValueError('缺少本地兼容阅读器资源。请重新解压完整应用包；源码运行需要 vendor/reader/vivliostyle-2.45.2。不会自动联网下载。')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('id') != 'vivliostyle-' + VERSION:
        raise ValueError('阅读器资源版本不兼容，需要 Vivliostyle ' + VERSION)
    assets = {}
    for entry in manifest['files']:
        name = entry['path']
        if not _safe_path(name):
            raise ValueError('阅读器清单路径不安全')
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError('阅读器资源缺失：' + name)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry['sha256'] or len(data) != entry['bytes']:
            raise ValueError('阅读器资源校验不符：' + name + '；请重新解压完整应用包。')
        assets[name] = data
    for name in ('viewer/index.html', 'profiles/source-margins.css'):
        if name not in assets:
            raise ValueError('阅读器清单缺少：' + name)
    return assets


def _book_assets(path):
    assets = {}
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(i.file_size for i in entries) > 512 * 1024 * 1024:
            raise ValueError('EPUB 超过本地预览的大小限制（512 MiB / 10000 文件）')
        for entry in entries:
            name = entry.filename
            if entry.is_dir():
                continue
            if not _safe_path(name) or name in assets or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('EPUB 包含不安全或重复路径')
            data = archive.read(entry)
            if name.endswith(('.xhtml', '.html', '.svg')):
                # Do not execute scripts from arbitrary imported books. Reader
                # JavaScript itself is a separate, checksummed official asset.
                node = ET.fromstring(data)
                for element in node.iter():
                    tag = element.tag.rsplit('}', 1)[-1].lower()
                    if tag in ('script', 'iframe', 'object', 'embed') or any(
                            key.rsplit('}', 1)[-1].lower().startswith('on') for key in element.attrib):
                        raise ValueError('兼容阅读器不接受带脚本或嵌入程序的 EPUB')
            assets['book/' + name] = data
    if assets.get('book/mimetype') != b'application/epub+zip' or 'book/META-INF/container.xml' not in assets:
        raise ValueError('不是有效的 EPUB 文件')
    return assets


def create_session(path, assets_root=None):
    """Start a read-only local session; caller owns close(), browser optional."""
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.lower() != '.epub':
        raise ValueError('请先导出有效 EPUB 再打开兼容阅读器。')
    assets = _reader_assets(Path(assets_root) if assets_root else resource_root())
    assets.update(_book_assets(path))
    container = ET.fromstring(assets['book/META-INF/container.xml'])
    rootfiles = container.findall('.//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile')
    package = next((node.get('full-path') for node in rootfiles
                    if node.get('media-type') == 'application/oebps-package+xml'), None)
    if not package or not _safe_path(package) or 'book/' + package not in assets:
        raise ValueError('EPUB 容器未指向有效的本地 OPF 文件')
    token = secrets.token_urlsafe(24)
    prefix = '/' + token + '/'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self._serve()

        def do_HEAD(self):
            self._serve(head_only=True)

        def _serve(self, head_only=False):
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                self.send_error(403)
                return
            url = unquote(urlsplit(self.path).path)
            if not url.startswith(prefix):
                self.send_error(404)
                return
            name = url[len(prefix):]
            # The EPUB loader fetches the root URL before container.xml.
            if name == 'book/':
                payload, mime = b'<!doctype html><title>Local EPUB</title>', 'text/html'
            else:
                if not _safe_path(name) or name not in assets:
                    self.send_error(404)
                    return
                payload = assets[name]
                mime = {'.js': 'text/javascript', '.css': 'text/css', '.xhtml': 'application/xhtml+xml',
                        '.opf': 'application/oebps-package+xml'}.get(PurePosixPath(name).suffix,
                                mimetypes.guess_type(name)[0] or 'application/octet-stream')
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy',
                "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self' data: blob:; font-src 'self' data:; "
                "worker-src 'self' blob:; frame-src 'none'; object-src 'none'; base-uri 'self'")
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True, name='local-epub-reader')
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}{prefix}'
    url = base + 'viewer/index.html#src=' + prefix + 'book/' + quote(package, safe='/') + '&bookMode=true&spread=false&allowScripts=false&style=' + prefix + 'profiles/source-margins.css'
    return {'url': url, 'reader': 'Vivliostyle Viewer', 'version': VERSION, 'server': server,
            'close': lambda: (server.shutdown(), server.server_close())}


def open_epub(path):
    session = create_session(path)
    with _lock:
        _servers.append(session)
    if not webbrowser.open(session['url'], new=2):
        raise RuntimeError('本地阅读服务已启动，但系统浏览器未打开。请在浏览器访问：' + session['url'])
    return {key: session[key] for key in ('url', 'reader', 'version')}


@atexit.register
def _close_sessions():
    for session in _servers:
        session['close']()
