"""Optional, pinned Windows ONNX Runtime GPU resource preparation. Never called implicitly.

The application trusts its own payload manifest, not an imported ZIP's claims.
Preparation extracts a private embedded Python/runtime; it never installs packages
into the application, system Python, or the user's GPU environment.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import time
import urllib.request
import zipfile

from .storage import atomic_json, digest, read_json

MANIFEST_NAME = "manifest.json"


def _prefix(backend):
    if backend not in ("cuda", "directml"):
        raise ValueError("GPU 后端必须为 cuda 或 directml")
    return "gpu_runtime" if backend == "cuda" else "directml_runtime"


def default_gpu_runtime_dir(backend="cuda"):
    _prefix(backend)
    override = os.environ.get("LOCAL_EPUB_GPU_RUNTIME" if backend == "cuda" else "LOCAL_EPUB_DIRECTML_RUNTIME")
    if override:
        return Path(override).expanduser()
    from .resources import default_resource_dir
    return default_resource_dir().parent / ("gpu-runtime" if backend == "cuda" else "directml-runtime")


def _expected(backend="cuda"):
    path = Path(__file__).with_name(_prefix(backend) + "_manifest.json")
    value = read_json(path)
    if not isinstance(value, dict) or value.get("format_version") != 1 or not value.get("files"):
        raise ValueError("此版本缺少固定 GPU 运行包清单；请使用完整发布包。")
    for name, entry in value["files"].items():
        _safe_name(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("bytes"), int) or entry["bytes"] < 0:
            raise ValueError("GPU 运行包内置清单损坏")
        sha = entry.get("sha256", "")
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("GPU 运行包内置校验值无效")
    if len({n.casefold() for n in value["files"]}) != len(value["files"]):
        raise ValueError("GPU 运行包内置清单存在大小写冲突")
    return value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_name(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("运行包包含不安全路径")
    parts = name.split("/")
    if PurePosixPath(name).is_absolute() or any(p in ("", ".", "..") or p.endswith((".", " ")) for p in parts):
        raise ValueError("运行包包含不安全路径：" + name)
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if any(p.split(".")[0].casefold() in reserved for p in parts):
        raise ValueError("运行包包含 Windows 保留路径：" + name)
    return name


def _is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _desktop_metadata(path):
    # Finder may create this while an offline Windows pack is prepared on Mac.
    # It cannot be imported as Python/native code and is never exported in ZIPs.
    return path.name == ".DS_Store"


def runtime_summary(backend="cuda"):
    """Read-only UI information; does not probe hardware or initialize GPU."""
    expected = _expected(backend)
    catalog = read_json(Path(__file__).with_name(_prefix(backend) + "_catalog.json"), {})
    return {"id": expected["id"], "versions": expected.get("versions", {}),
            "installed_bytes": sum(e["bytes"] for e in expected["files"].values()),
            "download_bytes": sum(c["bytes"] for c in catalog.get("components", []))}


def check_gpu_runtime(path, backend="cuda"):
    root = Path(path).expanduser()
    missing, errors = [], []
    manifest = None
    expected = _expected(backend)
    if _is_link(root):
        errors.append("GPU 运行目录不允许为符号链接")
    if _is_link(root / MANIFEST_NAME):
        errors.append("GPU 运行包清单不允许为符号链接")
    if errors:
        return {"ok": False, "missing": missing, "errors": errors, "manifest": None,
                "manifest_sha256": hashlib.sha256(_canonical(expected)).hexdigest(),
                "python_executable": str(root / "python.exe")}
    if not (root / MANIFEST_NAME).is_file():
        missing.append(MANIFEST_NAME)
    else:
        try:
            manifest = read_json(root / MANIFEST_NAME)
            if manifest != expected:
                errors.append("运行包版本或清单不匹配；需要 " + expected["id"])
        except (OSError, ValueError, TypeError) as exc:
            errors.append("运行包清单损坏：" + str(exc))
    # Stop before traversing an untrusted root link, even for read-only checks.
    if not errors:
        actual = set()
        if root.is_dir():
            for file in root.rglob("*"):
                if _is_link(file):
                    errors.append("运行包不允许符号链接：" + file.relative_to(root).as_posix())
                elif file.is_file() and not _desktop_metadata(file):
                    actual.add(file.relative_to(root).as_posix())
        allowed = set(expected["files"]) | {MANIFEST_NAME}
        for name in sorted(actual - allowed):
            errors.append("运行包包含未声明文件：" + name)
        if not errors:
            for name, entry in expected["files"].items():
                file = root / name
                if not file.is_file():
                    missing.append(name)
                elif file.stat().st_size != entry["bytes"] or digest(file) != entry["sha256"]:
                    errors.append("运行包校验值不符：" + name)
    return {"ok": not missing and not errors, "missing": missing, "errors": errors,
            "manifest": manifest, "manifest_sha256": hashlib.sha256(_canonical(expected)).hexdigest(),
            "python_executable": str((root / "python.exe").resolve())}


def _require(result):
    if not result["ok"]:
        details = result["errors"] + result["missing"]
        summary = "；".join(details[:12])
        if len(details) > 12:
            summary += f"；另有 {len(details) - 12} 项缺失或损坏（检查结果包含完整清单）"
        raise ValueError("GPU 运行包不可用：" + summary + "。请导入离线 GPU 运行包，或主动下载准备。")
    return result


def _target_ok(target, backend="cuda"):
    if _is_link(target):
        raise ValueError("拒绝覆盖符号链接目录")
    if not target.exists():
        return
    if not target.is_dir():
        raise ValueError("运行包目标必须是目录")
    if not any(target.iterdir()):
        return
    expected = _expected(backend)
    if read_json(target / MANIFEST_NAME) != expected:
        raise ValueError("目标非空且不是本版本的 GPU 运行目录；请选择空目录，现有文件未改动。")
    allowed = set(expected["files"]) | {MANIFEST_NAME}
    for file in target.rglob("*"):
        if _is_link(file) or (file.is_file() and not _desktop_metadata(file)
                             and file.relative_to(target).as_posix() not in allowed):
            raise ValueError("运行目录包含额外文件或符号链接，拒绝覆盖。")


def _space(target, needed):
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(target.parent).free < needed + 128 * 1024 * 1024:
        raise OSError(f"可用空间不足，需要至少 {needed / (1024 ** 3):.1f} GB 临时空间。")


def _install(staged, target, temporary, backend="cuda"):
    _require(check_gpu_runtime(staged, backend))
    _target_ok(target, backend)
    backup = Path(temporary) / "previous-runtime"
    if target.exists():
        try:
            target.rename(backup)
        except PermissionError as exc:
            raise PermissionError("GPU 运行包正在使用中；请等待转换结束后再导入。") from exc
    try:
        staged.rename(target)
    except BaseException:
        if backup.exists():
            backup.rename(target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _copy_verified(source, target, entry, on_bytes=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    hasher, total, last_report = hashlib.sha256(), 0, 0.
    with target.open("wb") as out:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > entry["bytes"]:
                raise ValueError("运行资源大小超过固定清单：" + target.name)
            hasher.update(chunk)
            out.write(chunk)
            if on_bytes and time.monotonic() - last_report >= 2:
                on_bytes(total)
                last_report = time.monotonic()
    if total != entry["bytes"] or hasher.hexdigest() != entry["sha256"]:
        raise ValueError("运行资源大小或 SHA-256 不匹配：" + target.name)
    if on_bytes:
        on_bytes(total)


def import_gpu_pack(zip_path, dest, progress=None, *, backend="cuda"):
    target = Path(dest).expanduser()
    _target_ok(target, backend)
    target = target.resolve()
    expected = _expected(backend)
    _space(target, sum(e["bytes"] for e in expected["files"].values()))
    if progress:
        progress("正在校验并导入 GPU 离线运行包…")
    with tempfile.TemporaryDirectory(prefix="cuda-import-", dir=target.parent) as temporary:
        staged = Path(temporary) / "content"
        staged.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            allowed = set(expected["files"]) | {MANIFEST_NAME}
            if len(infos) > len(allowed) * 2 + 100:
                raise ValueError("运行包文件数量超出固定清单")
            seen = set()
            files = {}
            for info in infos:
                name = info.filename.rstrip("/") if info.is_dir() else info.filename
                _safe_name(name)
                key = name.casefold()
                if key in seen or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                    raise ValueError("运行包包含重复路径或符号链接")
                seen.add(key)
                if info.is_dir():
                    continue
                if name not in allowed:
                    raise ValueError("运行包包含未声明文件：" + name)
                files[name] = info
            if set(files) != allowed:
                raise ValueError("GPU 离线包缺少文件：" + "、".join(sorted(allowed - set(files))[:12]))
            manifest_info = files[MANIFEST_NAME]
            if manifest_info.file_size > len(_canonical(expected)) * 4 + 1024:
                raise ValueError("运行包清单大小异常")
            if json.loads(archive.read(manifest_info)) != expected:
                raise ValueError("离线运行包与本版本固定清单不匹配")
            for index, (name, entry) in enumerate(expected["files"].items(), 1):
                info = files[name]
                if info.file_size != entry["bytes"]:
                    raise ValueError("离线运行包文件大小不匹配：" + name)
                with archive.open(info) as source:
                    _copy_verified(source, staged / name, entry)
                if progress and index % 100 == 0:
                    progress(f"正在导入 GPU 运行包：{index}/{len(expected['files'])} 个文件")
            atomic_json(staged / MANIFEST_NAME, expected)
        _install(staged, target, temporary, backend)
    return check_gpu_runtime(target, backend)


def _catalog(backend="cuda"):
    value = read_json(Path(__file__).with_name(_prefix(backend) + "_catalog.json"))
    expected = _expected(backend)
    if not isinstance(value, dict) or value.get("id") != expected["id"]:
        raise ValueError("此版本缺少匹配的 GPU 官方下载清单")
    return value


def download_gpu_runtime(dest, progress=None, *, backend="cuda"):
    """Explicit user action only: retrieve pinned official components, no PDF data."""
    target = Path(dest).expanduser()
    _target_ok(target, backend)
    target = target.resolve()
    expected, catalog = _expected(backend), _catalog(backend)
    _space(target, sum(e["bytes"] for e in expected["files"].values()) + sum(c["bytes"] for c in catalog["components"]))
    with tempfile.TemporaryDirectory(prefix="cuda-download-", dir=target.parent) as temporary:
        staged = Path(temporary) / "content"
        staged.mkdir()
        installed = set()
        for number, component in enumerate(catalog["components"], 1):
            if not component["url"].startswith("https://"):
                raise ValueError("GPU 资源下载必须使用固定 HTTPS 地址")
            if progress:
                progress(f"正在下载 GPU 组件 {number}/{len(catalog['components'])}：{component['name']}（不发送文档）")
            archive_path = Path(temporary) / f"component-{number}.zip"
            request = urllib.request.Request(component["url"], headers={"User-Agent": "LocalPDFEPUB-runtime-preparation/1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                if not response.geturl().startswith("https://"):
                    raise ValueError("拒绝非 HTTPS 资源重定向")
                def download_progress(count):
                    if progress:
                        progress(f"下载 {component['name']}：{count / 1_000_000:.1f}/{component['bytes'] / 1_000_000:.1f} MB（组件 {number}/{len(catalog['components'])}）")
                _copy_verified(response, archive_path, component, download_progress)
            with zipfile.ZipFile(archive_path) as archive:
                if len(archive.namelist()) != len(set(archive.namelist())):
                    raise ValueError("官方下载组件含重复路径")
                for item in component["files"]:
                    name = _safe_name(item["target"])
                    if name not in expected["files"] or name in installed:
                        raise ValueError("官方下载映射不匹配：" + name)
                    info = archive.getinfo(item["source"])
                    if info.file_size != expected["files"][name]["bytes"] or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                        raise ValueError("官方下载组件内容不匹配：" + name)
                    with archive.open(info) as source:
                        _copy_verified(source, staged / name, expected["files"][name])
                    installed.add(name)
            archive_path.unlink()
        for name, content in catalog.get("generated_files", {}).items():
            _safe_name(name)
            if name not in expected["files"] or name in installed:
                raise ValueError("GPU 配置文件映射不匹配：" + name)
            from io import BytesIO
            _copy_verified(BytesIO(content.encode("utf-8")), staged / name, expected["files"][name])
            installed.add(name)
        if installed != set(expected["files"]):
            raise ValueError("GPU 官方下载组件清单不完整")
        atomic_json(staged / MANIFEST_NAME, expected)
        if progress:
            progress("正在验证全部 GPU 运行资源…")
        _install(staged, target, temporary, backend)
    return check_gpu_runtime(target, backend)


def export_gpu_pack(source, destination, *, backend="cuda"):
    root = Path(source).expanduser()
    state = _require(check_gpu_runtime(root, backend))
    output = Path(destination).expanduser().resolve()
    if output.is_relative_to(root.resolve()):
        raise ValueError("离线包输出不能位于 GPU 运行目录内")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="cuda-pack-", suffix=".zip", dir=output.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for name in sorted([*state["manifest"]["files"], MANIFEST_NAME]):
                archive.write(root / name, name)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"path": str(output), "sha256": digest(output), "bytes": output.stat().st_size}
