#!/usr/bin/env python3
"""Pack only audited, hash-pinned corresponding sources; never fetch or run them."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError(f"Unsafe source entry: {name}")
    return name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path,
                        default=ROOT / "packaging/third-party-sources-0.1.6.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    sources = {}
    for record in catalog["files"]:
        name = safe_name(record["path"])
        path = args.source_root / name
        if name in sources or path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing, duplicate or symlink source: {name}")
        if path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError(f"Source integrity mismatch: {name}")
        sources[name] = path
    for name in ("docs/THIRD_PARTY_SOURCES.md", "docs/THIRD_PARTY.md",
                 "packaging/build_third_party_sources.py",
                 "packaging/third-party-sources-0.1.6.json"):
        sources[name] = ROOT / name
    manifest = {"release": catalog["release"], "scope": catalog["scope"],
                "contains_user_documents": False, "binary_rebuilt": False,
                "files": [{"path": name, "bytes": path.stat().st_size,
                           "sha256": sha256(path)}
                          for name, path in sorted(sources.items())]}
    prefix = f"LocalPDFtoEPUB-{catalog['release']}-third-party-sources/"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".partial")
    with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
        for name, path in sorted(sources.items()):
            info = zipfile.ZipInfo(prefix + name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = (0o100755 if path.suffix in (".sh", ".py") else 0o100644) << 16
            info.compress_type = (zipfile.ZIP_STORED if name.endswith((".xz", ".gz", ".bz2"))
                                  else zipfile.ZIP_DEFLATED)
            with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
        info = zipfile.ZipInfo(prefix + "manifest.json", (2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise ValueError("Source ZIP failed CRC validation")
        for record in manifest["files"]:
            with archive.open(prefix + record["path"]) as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != record["sha256"]:
                raise ValueError("Source ZIP member digest mismatch: " + record["path"])
    temporary.replace(args.output)
    checksum = sha256(args.output)
    Path(str(args.output) + ".sha256").write_text(
        f"{checksum}  {args.output.name}\n", encoding="ascii")
    print(json.dumps({"archive": args.output.name, "bytes": args.output.stat().st_size,
                      "sha256": checksum, "files": len(sources) + 1,
                      "all_member_hashes_verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
