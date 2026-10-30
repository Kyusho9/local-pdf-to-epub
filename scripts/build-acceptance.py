#!/usr/bin/env python3
"""Compare real backend output against the hand-auditable fixture truth files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time

from local_epub.backend import engine_versions, extract_page
from local_epub.offline import enforce_offline

ROOT = Path(__file__).resolve().parents[1]


def normalize(value):
    return re.sub(r"\s+", "", value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, default=ROOT / "resources")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "backend-final")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    enforce_offline()
    results, failures = [], []
    for truth_path in sorted((ROOT / "tests" / "fixtures").glob("*.truth.json")):
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        source = truth_path.with_name(truth["fixture"])
        folder = args.output / source.stem
        folder.mkdir(exist_ok=True)
        fixture_result = {"fixture": source.name, "pages": []}
        for expected in truth["pages"]:
            started = time.monotonic()
            page = extract_page(source, expected["page"], folder, {"mode": "auto", "dpi": 180}, args.resources)
            (folder / f"page-{expected['page']:04d}.json").write_text(json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            text = normalize("".join(block.get("text", "") for block in page["blocks"]))
            position, matches = 0, []
            for fragment in expected.get("ordered_fragments", []):
                normalized = normalize(fragment)
                found = text.find(normalized, position)
                matches.append({"expected": fragment, "found": found >= 0, "position": found})
                if found >= 0:
                    position = found + len(normalized)
                else:
                    failures.append(f"{source.name} p{expected['page']}: 未按序找到 {fragment}")
            checks = {
                "method": page["method"] == expected["expected_method"],
                "all_sources_present": all(block.get("sources") for block in page["blocks"]),
                "forbidden_text_absent": all(normalize(value) not in text for value in expected.get("must_not_emit", [])),
                "image_count": sum(block["kind"] == "image" for block in page["blocks"]) >= expected.get("image_count_min", 0),
            }
            if "table" in expected:
                checks["table_cells"] = any(block.get("rows") == expected["table"] for block in page["blocks"])
            if "footnote" in expected:
                checks["footnote_preserved"] = any(normalize(expected["footnote"]) in normalize(block.get("text", "")) for block in page["blocks"] if block["kind"] == "footnote")
            if "formula_visual" in expected:
                checks["formula_image_fallback"] = any(issue.get("code") == "formula_image_fallback" for issue in page.get("issues", []) if isinstance(issue, dict))
            for name, passed in checks.items():
                if not passed:
                    failures.append(f"{source.name} p{expected['page']}: {name}")
            fixture_result["pages"].append({"page": expected["page"], "method": page["method"], "expected_method": expected["expected_method"], "seconds": round(time.monotonic() - started, 3), "ordered_fragments": matches, "checks": checks})
        results.append(fixture_result)
    report = {"engine_versions": engine_versions(), "comparison": "逐段原文与顺序对比，仅忽略空白；另验图片、表格空值、脚注和公式降级。", "fixtures": results, "failures": failures, "passed": not failures}
    (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": not failures, "failures": failures, "pages": sum(len(result["pages"]) for result in results)}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
