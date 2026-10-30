"""Explicit resource preparation, integrity checks, no implicit downloads."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from .storage import atomic_json, digest, read_json

FILES = ("det.onnx", "rec.onnx", "cls.onnx")
PACK_VERSION = "rapidocr-1.4.4-ppocr-v4-1"


def default_resource_dir():
    override = os.environ.get("LOCAL_EPUB_RESOURCES")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LocalPDFEPUB"
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support/LocalPDFEPUB"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "local-pdf-epub"
    return base / "resources"


def check_resources(path):
    root = Path(path)
    missing = [f for f in ("manifest.json", *FILES) if not (root / f).is_file()]
    errors = []
    manifest = None
    if (root / "manifest.json").is_file():
        try:
            manifest = read_json(root / "manifest.json")
            if manifest.get("format_version") != 1 or manifest.get("id") != PACK_VERSION:
                errors.append("不兼容的资源包版本；需要 " + PACK_VERSION)
            entries = manifest.get("files", {})
            catalog = read_json(Path(__file__).with_name("resource_catalog.json"), {})
            for name in set(FILES) | set(entries):
                if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name or "\\" in name:
                    errors.append("清单路径不安全：" + name)
                    continue
                if name not in FILES and not name.startswith("licenses/"):
                    errors.append("清单包含不支持的文件：" + name)
                    continue
                if not (root / name).is_file() and name not in missing:
                    missing.append(name)
                if name not in entries:
                    errors.append("清单缺少 " + name)
                elif (root / name).is_file():
                    expected = entries[name].get("sha256")
                    actual = digest(root / name)
                    if actual != expected:
                        errors.append("校验值不符：" + name)
                    pinned = catalog.get("models", {}).get(name)
                    if pinned and actual != pinned:
                        errors.append("模型不属于本版本支持的资源：" + name)
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            errors.append("资源清单损坏：" + str(e))
    return {"ok": not missing and not errors, "missing": missing, "errors": errors, "manifest": manifest}


def require_resources(path):
    result = check_resources(path)
    if not result["ok"]:
        raise RuntimeError("本地 OCR 资源不可用：" + "；".join(result["missing"] + result["errors"]) + "。请在资源设置导入离线 ZIP，或主动点击下载。")
    return result["manifest"]


def _manifest(root):
    files = {}
    purposes = {"det.onnx": "文字检测", "rec.onnx": "中文及英文识别", "cls.onnx": "行方向分类"}
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.name != "manifest.json":
            name = f.relative_to(root).as_posix()
            files[name] = {"sha256": digest(f), "bytes": f.stat().st_size, "purpose": purposes.get(name, "许可证和来源记录")}
    return {"format_version": 1, "id": PACK_VERSION, "version": "1", "purpose": "中英混排 OCR；CPU ONNX Runtime；无在线服务", "engine": "rapidocr-onnxruntime==1.4.4", "model_origin": "RapidAI/RapidOCR; PaddleOCR PP-OCRv4 det/rec, PP-OCRv2 cls", "license": "Apache-2.0", "files": files}


def prepare_from_installed(dest):
    spec = importlib.util.find_spec("rapidocr_onnxruntime")
    if not spec or not spec.origin:
        raise RuntimeError("开发环境未安装 rapidocr-onnxruntime==1.4.4")
    models = Path(spec.origin).parent / "models"
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    for kind in ("det", "rec", "cls"):
        candidates = sorted(models.glob("*" + kind + "*.onnx"))
        if len(candidates) != 1:
            raise RuntimeError("模型文件不明确：" + kind)
        shutil.copy2(candidates[0], root / (kind + ".onnx"))
    shutil.copytree(Path(__file__).with_name("model_licenses"), root / "licenses", dirs_exist_ok=True)
    atomic_json(root / "manifest.json", _manifest(root))
    return check_resources(root)


def export_pack(source, zip_path):
    require_resources(source)
    root = Path(source)
    target = Path(zip_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(root.rglob("*")):
            if f.is_file() and not f.is_symlink():
                z.write(f, f.relative_to(root).as_posix())
    return {"path": str(target), "sha256": digest(target)}


def import_pack(zip_path, dest):
    target = Path(dest).expanduser().resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        prior = check_resources(target) if target.is_dir() else {"ok": False}
        if not prior["ok"]:
            raise ValueError("目标目录非空且不是本应用的完整资源目录。请选择新的空目录；现有文件未改动。")
        declared = set(prior["manifest"]["files"]) | {"manifest.json"}
        actual = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
        if actual != declared or any(p.is_symlink() for p in target.rglob("*")):
            raise ValueError("资源目录包含未声明文件或符号链接，拒绝覆盖。请选择新的空目录；现有文件未改动。")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="resource-import-", dir=target.parent) as tmp:
        staged = Path(tmp) / "content"
        staged.mkdir()
        with zipfile.ZipFile(zip_path) as z:
            infos = z.infolist()
            if len(infos) > 100 or sum(i.file_size for i in infos) > 200 * 1024 * 1024:
                raise ValueError("资源包超出大小或文件数量限制")
            seen = set()
            for info in infos:
                n = info.filename
                if n in seen or n.startswith(("/", "\\")) or "\\" in n or any(p in ("..", "") for p in n.split("/")) or ":" in n:
                    raise ValueError("资源包包含不安全或重复路径")
                seen.add(n)
                if info.is_dir():
                    continue
                if n not in (*FILES, "manifest.json") and not n.startswith("licenses/"):
                    raise ValueError("资源包含未支持文件：" + n)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("资源包不允许符号链接")
                f = staged / n
                f.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, f.open("wb") as out:
                    shutil.copyfileobj(src, out)
        result = check_resources(staged)
        if not result["ok"]:
            raise ValueError("；".join(result["missing"] + result["errors"]))
        backup = Path(tmp) / "previous-resource-directory"
        if target.exists():
            target.rename(backup)
        try:
            staged.rename(target)
        except BaseException:
            if backup.exists():
                backup.rename(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    return check_resources(target)


def download_pack(dest, progress=None):
    """Only callable from an explicit resources download action; downloads code-free models."""
    catalog = read_json(Path(__file__).with_name("resource_catalog.json"))
    if not catalog:
        raise RuntimeError("此版本缺少固定资源下载目录，请改用离线资源包。")
    if progress:
        progress("正在下载固定版本模型包（不发送 PDF 内容）")
    with tempfile.TemporaryDirectory() as tmp:
        wheel = Path(tmp) / "ocr.whl"
        req = urllib.request.Request(catalog["wheel_url"], headers={"User-Agent": "LocalPDFEPUB/0.1"})
        with urllib.request.urlopen(req, timeout=60) as response, wheel.open("wb") as f:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 50 * 1024 * 1024:
                    raise ValueError("下载资源超出大小限制")
                f.write(chunk)
        if digest(wheel) != catalog["wheel_sha256"]:
            raise ValueError("下载文件 SHA-256 不匹配，未安装")
        staged = Path(tmp) / "models"
        staged.mkdir()
        with zipfile.ZipFile(wheel) as z:
            for kind in ("det", "rec", "cls"):
                names = [n for n in z.namelist() if "/models/" in n and kind in n.split("/")[-1] and n.endswith(".onnx")]
                if len(names) != 1:
                    raise ValueError("模型清单不匹配")
                (staged / (kind + ".onnx")).write_bytes(z.read(names[0]))
            license_names = [n for n in z.namelist() if n.endswith("LICENSE") or n.endswith("LICENSE.txt")]
            for index, name in enumerate(license_names):
                (staged / "licenses").mkdir(exist_ok=True)
                (staged / "licenses" / f"rapidocr-{index}.txt").write_bytes(z.read(name))
        shutil.copytree(Path(__file__).with_name("model_licenses"), staged / "licenses", dirs_exist_ok=True)
        atomic_json(staged / "manifest.json", _manifest(staged))
        pack = Path(tmp) / "resource.zip"
        export_pack(staged, pack)
        return import_pack(pack, dest)
