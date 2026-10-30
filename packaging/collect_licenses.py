#!/usr/bin/env python3
"""Capture installed distribution metadata and all shipped license/notice texts.

This inventory records the build environment. Release maintainers must review
it for licensing obligations; metadata is not a legal compatibility verdict.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "packaging" / "licenses"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    inventory = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata.get("Name", "").lower()):
        name = dist.metadata.get("Name", "unknown")
        folder = OUT / f"{name}-{dist.version}"
        captured = []
        for file in dist.files or []:
            lower = str(file).lower()
            basename = Path(lower).name
            in_license_dir = any(part in ("licenses", "licences", "legal") for part in Path(lower).parts)
            if not in_license_dir and not any(token in basename for token in ("license", "licence", "notice", "copying", "copyright")):
                continue
            source = Path(dist.locate_file(file))
            if not source.is_file() or source.suffix in (".py", ".pyc", ".so", ".dylib", ".dll", ".pyd"):
                continue
            # Normalize RECORD paths: never follow parent components into output.
            relative = Path(*[part for part in Path(str(file)).parts if part not in ("..", "/", ".")])
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            captured.append({"path": str(target.relative_to(OUT)), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        record = {
            "name": name, "version": dist.version,
            "license_expression": dist.metadata.get("License-Expression"),
            "license_metadata": dist.metadata.get("License"),
            "home_page": dist.metadata.get("Home-page"),
            "project_urls": dist.metadata.get_all("Project-URL") or [],
            "license_classifiers": [v for v in dist.metadata.get_all("Classifier") or [] if v.startswith("License ::")],
            "license_files": captured,
        }
        normalized = name.lower().replace("_", "-")
        supplemental_folder = {
            "pyside6-essentials": f"Qt-{dist.version}",
            "shiboken6": f"Qt-{dist.version}",
            "flatbuffers": f"FlatBuffers-{dist.version}",
            "rapidocr-onnxruntime": "model-origins",
        }.get(normalized)
        record["supplemental_license_files"] = []
        if supplemental_folder:
            for source in sorted((OUT / supplemental_folder).glob("*")):
                if source.is_file():
                    record["supplemental_license_files"].append({"path": str(source.relative_to(OUT)), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "origin": "official source supplement, not supplied by installed wheel"})
        inventory.append(record)
    for python_license in (Path(sys.base_prefix) / "LICENSE", Path(sys.base_prefix) / "LICENSE.txt", Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"):
        if python_license.exists():
            shutil.copy2(python_license, OUT / "Python-LICENSE.txt")
            break
    (OUT / "inventory.json").write_text(json.dumps({"python": sys.version, "distributions": inventory}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {len(inventory)} distributions in {OUT}")


if __name__ == "__main__":
    main()
