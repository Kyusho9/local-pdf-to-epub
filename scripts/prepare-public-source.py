#!/usr/bin/env python3
"""Prepare an isolated, reviewable source tree; never initialize Git or upload.

The default is a read-only preflight. Use --write --output artifacts/... only
after reviewing its file list. A source delivery ZIP may include explicitly
approved test-machine evidence; this public tree deliberately includes none.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
MAX_PUBLIC_FILE_BYTES = 100 * 1024 * 1024


def package_module():
    spec = importlib.util.spec_from_file_location("source_delivery", ROOT / "scripts/package-source.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Copy checked source into a new isolated directory")
    parser.add_argument("--output", type=Path, help="New directory inside this project's artifacts directory")
    args = parser.parse_args()
    module = package_module()
    selected = module.source_files(ROOT, include_opencv_source=False)
    records = []
    for name, path in sorted(selected.items()):
        if name.startswith("artifacts/") or module.private_path(Path(name)):
            raise ValueError(f"Private source selection: {name}")
        size = path.stat().st_size
        if size > MAX_PUBLIC_FILE_BYTES:
            raise ValueError(f"File exceeds GitHub's 100 MiB Git object limit: {name}")
        records.append({"path": name, "bytes": size, "sha256": module.sha256(path)})
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    result = {
        "format_version": 1, "version": version, "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "preflight_only", "git_initialized": False, "uploaded": False,
        "scope": "Explicit source directories only; no artifacts, user documents, caches, environments, or runtime packages",
        "file_count": len(records), "bytes": sum(item["bytes"] for item in records), "files": records,
        "external_build_source": json.loads((ROOT / "packaging/opencv-source.json").read_text(encoding="utf-8")),
    }
    if args.write:
        if args.output is None:
            raise ValueError("--write requires --output")
        output = args.output.expanduser().resolve()
        if not output.is_relative_to(ROOT / "artifacts") or output == ROOT / "artifacts":
            raise ValueError("Public staging must be a new directory inside artifacts")
        if output.exists():
            raise ValueError("Refusing to replace an existing staging directory")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="public-source-", dir=output.parent) as temporary:
            stage = Path(temporary) / "tree"
            stage.mkdir()
            for item in records:
                destination = stage / item["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(module.checked_file(ROOT, item["path"]), destination)
                if module.sha256(destination) != item["sha256"]:
                    raise RuntimeError(f"Source changed during staging: {item['path']}")
            if any(module.sha256(selected[item["path"]]) != item["sha256"] for item in records):
                raise RuntimeError("Source changed during staging; wait for the release source to freeze")
            result["status"] = "isolated_tree_verified_not_published"
            (stage / "PUBLIC-SOURCE-MANIFEST.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stage.rename(output)
        result["output"] = str(output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
