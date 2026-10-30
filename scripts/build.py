#!/usr/bin/env python3
"""Build a portable package on the current target OS from the pinned environment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from importlib import metadata
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def source_fingerprint():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "src").rglob("*"))
            if path.is_file() and path.suffix in (".py", ".json", ".txt") and "__pycache__" not in path.parts}


def dependency_fingerprint():
    result = {}
    for dist in metadata.distributions():
        record = next((Path(dist.locate_file(file)) for file in dist.files or [] if str(file).endswith(".dist-info/RECORD")), None)
        result[dist.metadata["Name"]] = {"version": dist.version, "record_sha256": hashlib.sha256(record.read_bytes()).hexdigest() if record and record.is_file() else None}
    return result


def reader_fingerprint():
    directories = ('vendor/reader/vivliostyle-2.45.2', 'vendor/sources/vivliostyle-2.45.2')
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for directory in directories for path in sorted((ROOT / directory).rglob('*'))
            if path.is_file()}


def verify_opencv_for_distribution():
    import cv2
    info = cv2.getBuildInformation()
    cv_root = Path(cv2.__file__).parent
    local_build = None
    if (cv_root / "LOCAL_BUILD.json").is_file():
        local_build = json.loads((cv_root / "LOCAL_BUILD.json").read_text(encoding="utf-8"))
    bundled_video = [path.name for path in cv_root.rglob("*") if path.is_file() and path.suffix in (".dylib", ".dll") and any(token in path.name.lower() for token in ("avcodec", "avformat", "avutil", "ffmpeg", "x264", "x265"))]
    if platform.system() == "Darwin" and (not isinstance(local_build, dict) or local_build.get("local_build") != "local-pdf-epub-image-only-1"):
        raise SystemExit("macOS 发布缺少已审查的 OpenCV 本地构建标识；请安装 vendor/wheels 中的最小图片 wheel，不能从 PyPI 自动替换。")
    if platform.system() == "Darwin" and (bundled_video or re.search(r"FFMPEG:\s+YES", info)):
        raise SystemExit("macOS 发布必须安装本项目自建的最小图片 OpenCV wheel；检测到视频编解码依赖，已停止打包。见 packaging 中的源码构建与许可审查记录。")
    return {"version": cv2.__version__, "local_build": local_build, "bundled_video_libraries": bundled_video, "build_information": info}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-archive", action="store_true")
    args = parser.parse_args()
    if platform.system() not in ("Windows", "Darwin"):
        raise SystemExit("正式首版发布目标为 Windows x64 或 macOS arm64；请在对应系统构建。")
    if platform.system() == "Windows" and platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("Windows 构建必须使用 x64 Python。")
    source_hashes = source_fingerprint()
    dependency_hashes = dependency_fingerprint()
    reader_hashes = reader_fingerprint()
    opencv_info = verify_opencv_for_distribution()
    subprocess.run([sys.executable, str(ROOT / "packaging" / "collect_licenses.py")], check=True, cwd=ROOT)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "pyinstaller-cache")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "packaging" / "local_epub.spec")], check=True, cwd=ROOT, env=env)
    if source_fingerprint() != source_hashes or dependency_fingerprint() != dependency_hashes or reader_fingerprint() != reader_hashes:
        raise SystemExit("构建期间源码或依赖发生变化，请等待环境稳定后重新构建；本次不生成发布归档。")
    platform_tag = "windows-x64" if platform.system() == "Windows" else "macos-" + platform.machine()
    output = ROOT / "dist" / ("LocalPDFtoEPUB.app" if platform.system() == "Darwin" else "LocalPDFtoEPUB")
    report = {"platform": platform.platform(), "python": sys.version, "package": str(output), "architecture": platform.machine(), "status": "built; runtime validation is recorded separately", "models": "separate offline resource pack", "developer_certificate_signed": False, "notarized": False, "macos_signing": "PyInstaller ad-hoc signature" if platform.system() == "Darwin" else None, "source_sha256": source_hashes, "dependency_records": dependency_hashes, "opencv_runtime": opencv_info}
    report['reader_resources_sha256'] = reader_hashes
    (ROOT / "dist" / "build-info.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.skip_archive:
        base = ROOT / "dist" / ("LocalPDFtoEPUB-" + VERSION + "-" + platform_tag)
        if platform.system() == "Darwin":
            # ditto keeps bundle symlinks and framework structure intact.
            archive = Path(str(base) + ".zip")
            subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(output), str(archive)], check=True)
        else:
            archive = Path(shutil.make_archive(str(base), "zip", output.parent, output.name))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(str(archive) + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        print(archive)
    print(output)


if __name__ == "__main__":
    main()
