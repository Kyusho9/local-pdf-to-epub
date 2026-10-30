#!/usr/bin/env python3
"""Produce a Windows app ZIP that uses the user's installed VC++ runtime.

Only release packaging changes: application production binaries/code are retained
byte-for-byte. The Shapely-local MSVCP copy is created on the user's machine by the
included launcher, never included by this builder. No network or system mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

MICROSOFT_RUNTIME_DLL = re.compile(
    r"(?:vcruntime|msvcp|msvcr|concrt|vcomp|ucrtbase|api-ms-win-crt|mfc).*\.dll$", re.I
)
SHAPELY_RUNTIME = "LocalPDFtoEPUB/_internal/Shapely.libs/msvcp140-0fa7eb792d3fbcf2233e4ea47e9144b9.dll"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(source: Path, destination: Path, docs: Path, record_path: Path) -> dict:
    if source.resolve() == destination.resolve():
        raise ValueError("Input and output ZIPs must differ.")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing release asset: {destination}")
    bootstrap = Path(__file__).resolve().parent / "windows-system-runtime"
    additions = {f"LocalPDFtoEPUB/{name}": bootstrap / name for name in (
        "Start-LocalPDFtoEPUB.cmd", "Prepare-System-Runtime.ps1", "README-system-runtime.txt"
    )}
    for name in ("THIRD_PARTY.md", "THIRD_PARTY_SOURCES.md", "PLATFORMS.md"):
        additions[f"LocalPDFtoEPUB/_internal/docs/{name}"] = docs / name
    # Read all required files before creating an output, avoiding a partial archive
    # when an audit document is absent.
    payloads = {name: path.read_bytes() for name, path in additions.items()}
    removed, unchanged, differences = [], [], []
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as src:
        names = src.namelist()
        if len(names) != len(set(names)) or SHAPELY_RUNTIME not in names:
            raise ValueError("Unexpected or duplicate app package layout; audit the Shapely bootstrap before continuing.")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or path.parts[0] != "LocalPDFtoEPUB":
                raise ValueError(f"Unsafe or unexpected ZIP entry: {name}")
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
            for info in src.infolist():
                if MICROSOFT_RUNTIME_DLL.fullmatch(PurePosixPath(info.filename).name):
                    data = src.read(info.filename)
                    removed.append({"path": info.filename, "sha256": sha(data), "bytes": len(data)})
                    continue
                if info.filename in payloads:
                    continue
                data = src.read(info.filename)
                dst.writestr(info, data)
                if not info.is_dir():
                    unchanged.append({"path": info.filename, "sha256": sha(data), "bytes": len(data)})
            for name, data in payloads.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 10, 30, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                dst.writestr(info, data)
                differences.append({"path": name, "sha256": sha(data), "bytes": len(data)})
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("Output ZIP integrity check failed.")
        if any(MICROSOFT_RUNTIME_DLL.fullmatch(PurePosixPath(n).name) for n in archive.namelist()):
            raise ValueError("Microsoft runtime DLL unexpectedly present in public ZIP.")
        if not all(sha(archive.read(item["path"])) == item["sha256"] for item in unchanged):
            raise ValueError("A retained production file changed.")
    result = {
        "source_name": source.name, "source_sha256": sha(source.read_bytes()),
        "output_name": destination.name, "sha256": sha(destination.read_bytes()),
        "bytes": destination.stat().st_size, "removed": removed,
        "unchanged_count": len(unchanged), "unchanged": unchanged,
        "added_or_updated": differences, "vc_ucrt_dlls_in_published_zip": 0,
        "production_code_unchanged": True,
    }
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Original complete Windows x64 app ZIP; not modified")
    parser.add_argument("destination", type=Path, help="New system-runtime ZIP path; must not exist")
    parser.add_argument("--docs", type=Path, default=Path(__file__).resolve().parents[1] / "docs")
    parser.add_argument("--record", type=Path, help="SHA/integrity JSON path")
    args = parser.parse_args()
    result = build(args.source, args.destination, args.docs, args.record or args.destination.with_suffix(".integrity.json"))
    print(json.dumps({k: v for k, v in result.items() if k not in ("removed", "unchanged")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
