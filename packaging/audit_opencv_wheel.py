#!/usr/bin/env python3
"""Read official macOS ARM wheel binary metadata without installing the wheel.

HTTP Range keeps the inspection small: ZIP directory plus libavutil only.
No executable or inference model from the candidate wheel is executed.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import re
import urllib.request
import zipfile


class RemoteZip(io.RawIOBase):
    def __init__(self, url, size):
        self.url, self.size, self.position = url, size, 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        return self.position

    def tell(self):
        return self.position

    def read(self, size=-1):
        size = self.size - self.position if size < 0 else min(size, self.size - self.position)
        if size <= 0:
            return b""
        request = urllib.request.Request(self.url, headers={"Range": f"bytes={self.position}-{self.position + size - 1}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
            if response.status != 206:
                data = data[self.position:self.position + size]
        self.position += len(data)
        return data


def audit(package, version, platform="macos-arm64"):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/{version}/json", timeout=30) as response:
        metadata = json.load(response)
    def matches(name):
        return ("win_amd64" in name if platform == "windows-x64" else "macosx" in name and "arm64" in name) and name.endswith(".whl")
    wheel = next(item for item in metadata["urls"] if matches(item["filename"]))
    with zipfile.ZipFile(RemoteZip(wheel["url"], wheel["size"])) as archive:
        libraries = [name for name in archive.namelist() if ("libavutil" in name and name.endswith(".dylib")) or ("ffmpeg" in name.lower() and name.endswith(".dll"))]
        binary = archive.read(libraries[0]) if libraries else b""
        return {"package": package, "version": version, "platform": platform, "filename": wheel["filename"], "url": wheel["url"], "wheel_sha256": wheel["digests"]["sha256"], "bytes": wheel["size"], "ffmpeg_libraries": libraries, "license_strings": [value.decode() for value in re.findall(rb"(?:LGPL|GPL) version [^\x00]+", binary)], "configuration": [value.decode(errors="replace") for value in re.findall(rb"--prefix=[^\x00]+", binary)], "gpl_enabled": b"--enable-gpl" in binary, "x264_libraries": [name for name in archive.namelist() if "x264" in name or "x265" in name]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    parser.add_argument("version")
    parser.add_argument("--platform", choices=["macos-arm64", "windows-x64"], default="macos-arm64")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.package, args.version, args.platform)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
