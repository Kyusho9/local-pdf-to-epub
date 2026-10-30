#!/usr/bin/env python3
"""Build the local, image-only macOS arm64 OpenCV wheel from pinned source.

Prepare .build-opencv-venv with packaging/opencv-build-requirements.txt first.
This script does not install anything into the application's .venv.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FLAGS = [
    "-DBUILD_LIST=core,imgproc,imgcodecs,python3",
    "-DCMAKE_BUILD_TYPE=Release",
    "-DCMAKE_OSX_ARCHITECTURES=arm64",
    "-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0",
    "-DCPU_BASELINE=NEON",
    "-DCPU_DISPATCH=NEON_FP16,NEON_DOTPROD",
    "-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local",
    "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF",
    "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
    "-DBUILD_SHARED_LIBS=OFF",
    "-DBUILD_TESTS=OFF", "-DBUILD_PERF_TESTS=OFF", "-DBUILD_EXAMPLES=OFF",
    "-DBUILD_opencv_apps=OFF", "-DBUILD_JAVA=OFF", "-DBUILD_DOCS=OFF",
    "-DWITH_FFMPEG=OFF", "-DWITH_AVFOUNDATION=OFF", "-DWITH_GSTREAMER=OFF",
    "-DWITH_QT=OFF", "-DWITH_GTK=OFF", "-DWITH_OPENCL=OFF", "-DWITH_IPP=OFF",
    "-DWITH_OPENVX=OFF", "-DWITH_VTK=OFF", "-DWITH_ITT=OFF", "-DWITH_LAPACK=OFF",
    "-DWITH_PROTOBUF=OFF", "-DWITH_OBSENSOR=OFF", "-DWITH_OPENEXR=OFF",
    "-DWITH_WEBP=OFF", "-DWITH_TIFF=OFF", "-DWITH_JASPER=OFF",
    "-DWITH_OPENJPEG=OFF", "-DWITH_JPEGXL=OFF", "-DWITH_AVIF=OFF",
    "-DBUILD_OPENEXR=OFF", "-DBUILD_PROTOBUF=OFF",
    "-DBUILD_ZLIB=ON", "-DBUILD_JPEG=ON", "-DBUILD_PNG=ON",
    "-DWITH_JPEG=ON", "-DWITH_PNG=ON", "-DOPENCV_FORCE_3RDPARTY_BUILD=ON",
    "-DOPENCV_GENERATE_PKGCONFIG=OFF", "-DPYTHON3_LIMITED_API=ON",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, default=ROOT / ".build-opencv-venv/bin/python")
    parser.add_argument("--source", type=Path, default=ROOT / "vendor/sources/opencv-python-4.11.0.86.tar.gz")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--deny-network", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise SystemExit("此配方当前仅实测 macOS arm64。")
    manifest = json.loads((ROOT / "packaging/opencv-source.json").read_text())
    source = args.source.resolve()
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["source_sha256"]:
        raise SystemExit("OpenCV 源码 SHA-256 不匹配。")
    work = ROOT / "build/opencv-source"
    folder = work / "opencv-python-4.11.0.86"
    if not folder.exists():
        work.mkdir(parents=True, exist_ok=True)
        with tarfile.open(source) as archive:
            archive.extractall(work, filter="data")
    # Visible packaging-only changes: embed origin, permit absent optional stubs
    # when their referenced feature modules are intentionally not built.
    setup = folder / "setup.py"
    code = setup.read_text()
    if '"LOCAL_BUILD.json"' not in code:
        before = '+ ["LICENSE.txt", "LICENSE-3RD-PARTY.txt"],'
        after = '+ ["LICENSE.txt", "LICENSE-3RD-PARTY.txt", "LOCAL_BUILD.json"],'
        if before not in code:
            raise SystemExit("setup.py 与已审查的源码版本不符。")
        code = code.replace(before, after, 1)
    if '"LOCAL_LICENSES.txt"' not in code:
        code = code.replace('"LOCAL_BUILD.json"', '"LOCAL_BUILD.json", "LOCAL_LICENSES.txt"', 1)
    before = 'if not found and "gapi" not in relpath_re:'
    after = 'if not found and not any(optional in relpath_re for optional in ("gapi", "py.typed", "/typing/")):'
    if before in code:
        code = code.replace(before, after, 1)
    setup.write_text(code)
    with tarfile.open(source) as archive:
        original = archive.extractfile("opencv-python-4.11.0.86/setup.py").read().decode()
    (ROOT / "packaging/opencv-local.patch").write_text("".join(difflib.unified_diff(original.splitlines(keepends=True), code.splitlines(keepends=True), fromfile="a/setup.py", tofile="b/setup.py")))
    local_build = {"local_build": "local-pdf-epub-image-only-1", "package": "opencv-python", "version": "4.11.0.86", "source_url": manifest["source_url"], "source_sha256": manifest["source_sha256"], "cmake_flags": FLAGS, "source_modification": "setup.py packages LOCAL_BUILD.json/LOCAL_LICENSES.txt and permits absent optional typing stubs for intentionally omitted feature modules; no OpenCV algorithm code modified", "ffmpeg": "disabled", "video_and_gui_modules": "not built"}
    (folder / "cv2/LOCAL_BUILD.json").write_text(json.dumps(local_build, ensure_ascii=False, indent=2) + "\n")
    notices = ["This is the LocalPDFtoEPUB image-only build. FFmpeg and video codecs are absent.\nThe unchanged upstream broad notices are retained separately; these texts cover the local static components.\n"]
    for relative in ("opencv/LICENSE", "opencv/3rdparty/zlib/LICENSE", "opencv/3rdparty/libpng/LICENSE", "opencv/3rdparty/libjpeg-turbo/LICENSE.md", "opencv/3rdparty/libjpeg-turbo/README.ijg", "opencv/3rdparty/flatbuffers/LICENSE.txt"):
        notices.append(f"\n===== {relative} =====\n" + (folder / relative).read_text())
    seen = set()
    for source_file in sorted((folder / "opencv/3rdparty/carotene").rglob("*")):
        if source_file.suffix not in (".cpp", ".hpp", ".h"):
            continue
        header = source_file.read_text().split("*/", 1)[0] + "*/"
        if "License" in header and "Copyright" in header and header not in seen:
            notices.append(f"\n===== {source_file.relative_to(folder)} license header =====\n" + header)
            seen.add(header)
    (folder / "cv2/LOCAL_LICENSES.txt").write_text("\n".join(notices))
    output = ROOT / "vendor/wheels"
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = str(args.python.resolve().parent) + os.pathsep + "/usr/bin:/bin:/usr/sbin:/sbin"
    # Preserve the venv executable path rather than resolving its Python symlink.
    python = args.python.absolute()
    env["PATH"] = str(python.parent) + os.pathsep + "/usr/bin:/bin:/usr/sbin:/sbin"
    env["CMAKE_ARGS"] = " ".join(FLAGS)
    env["CMAKE_GENERATOR"] = "Ninja"
    env["CMAKE_BUILD_PARALLEL_LEVEL"] = str(max(1, args.jobs))
    env["MACOSX_DEPLOYMENT_TARGET"] = "14.0"
    env["SOURCE_DATE_EPOCH"] = "1737035544"
    env["ENABLE_CONTRIB"] = "0"
    env["ENABLE_HEADLESS"] = "0"  # Preserve opencv-python distribution name.
    env["CI_BUILD"] = "0"
    env.pop("PYTHONPATH", None)
    prefix = ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)"] if args.deny_network else []
    subprocess.run([*prefix, str(python), "setup.py", "bdist_wheel", "--dist-dir", str(output)], cwd=folder, env=env, check=True)
    wheels = sorted(output.glob("opencv_python-4.11.0.86-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("需要且只应生成一个本地 OpenCV wheel。")
    wheel = wheels[0]
    record = {**local_build, "wheel": str(wheel.relative_to(ROOT)), "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "wheel_bytes": wheel.stat().st_size, "build_platform": platform.platform(), "build_network": "OS denied" if args.deny_network else "not OS blocked"}
    (ROOT / "packaging/opencv-minimal-build.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    Path(str(wheel) + ".sha256").write_text(record["wheel_sha256"] + "  " + wheel.name + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
