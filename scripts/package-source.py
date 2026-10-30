#!/usr/bin/env python3
"""Create the source delivery ZIP from an explicit allowlist.

Run only after release code, licenses, and acceptance records are final:
    python scripts/package-source.py --dry-run
    python scripts/package-source.py

No virtual environments, build trees, application bundles, OCR caches, user PDF
snapshots, screenshots, or arbitrary artifacts are discovered for inclusion.
Additional final evidence must be named explicitly with --evidence and must be a
small JSON/log/report file outside all task/checkpoint directories.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import tomllib
import zipfile


SOURCE_DIRECTORIES = (
    "src", "tests", "scripts", "packaging", "docs", ".github",
    "vendor/wheels", "vendor/sources", "vendor/reader",
)
SOURCE_FILES = ("README.md", "LICENSE", "pyproject.toml", ".gitignore")
EXCLUDED_COMPONENTS = {
    ".git", ".venv", "venv", ".build-opencv-venv", "build", "dist",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".DS_Store", "__MACOSX", "screenshots",
}
# These paths are checked individually, never by recursively scanning artifacts.
FINAL_EVIDENCE = (
    'artifacts/release-0.1.5/windows-gui-directml.json',
    'artifacts/release-0.1.5/windows-gui-directml-result.json',
    'artifacts/release-0.1.5/windows-gui-directml-worker.log',
    'artifacts/release-0.1.5/windows-gui-directml-process-modules.json',
    'artifacts/release-0.1.5/windows-gui-directml-tcp.json',
    'artifacts/release-0.1.5/windows-gui-directml-firewall-active.json',
    'artifacts/release-0.1.5/windows-gui-directml-child.json',
    'artifacts/release-0.1.5/windows-gui-cuda.json',
    'artifacts/release-0.1.5/windows-gui-cuda-result.json',
    'artifacts/release-0.1.5/windows-gui-cuda-worker.log',
    'artifacts/release-0.1.5/windows-gui-cuda-process-modules.json',
    'artifacts/release-0.1.5/windows-gui-cuda-tcp.json',
    'artifacts/release-0.1.5/windows-gui-cuda-firewall-active.json',
    'artifacts/release-0.1.5/windows-gui-cuda-child.json',
    'artifacts/release-0.1.5/windows-gui-missing-fallback.json',
    'artifacts/release-0.1.5/windows-gui-missing-fallback-result.json',
    'artifacts/release-0.1.5/windows-gui-missing-fallback-worker.log',
    'artifacts/release-0.1.5/windows-gui-missing-fallback-process-modules.json',
    'artifacts/release-0.1.5/windows-gui-missing-fallback-tcp.json',
    'artifacts/release-0.1.5/windows-gui-missing-fallback-firewall-active.json',
    'artifacts/release-0.1.5/windows-gui-missing-fallback-child.json',
    'artifacts/release-0.1.5/windows-gui-missing-strict.json',
    'artifacts/release-0.1.5/windows-gui-missing-strict-result.json',
    'artifacts/release-0.1.5/windows-gui-missing-strict-worker.log',
    'artifacts/release-0.1.5/windows-gui-missing-strict-process-modules.json',
    'artifacts/release-0.1.5/windows-gui-missing-strict-tcp.json',
    'artifacts/release-0.1.5/windows-gui-missing-strict-firewall-active.json',
    'artifacts/release-0.1.5/windows-gui-missing-strict-child.json',
    'artifacts/release-0.1.5/windows-gui-epubcheck.json',
    'artifacts/release-0.1.5/windows-frozen-gpu-summary.json',
    'artifacts/release-0.1.5/windows-final-summary.json',
    'artifacts/release-0.1.5/windows-delivery.json',
    'artifacts/release-0.1.5/delivery-package-hashes.json',
    'artifacts/gpu-0.1.5/reproducible-review.json',
    'artifacts/release-0.1.5/windows-build-info.json',
    'artifacts/release-0.1.5/windows-build.log',
    'artifacts/release-0.1.5/windows-build.stderr.log',
    'artifacts/release-0.1.5/windows-package.json',
    'artifacts/release-0.1.5/windows-package-integrity.json',
    'artifacts/release-0.1.5/windows-tests.json',
    'artifacts/release-0.1.5/windows-pytest.log',
    'artifacts/release-0.1.5/windows-pytest.stderr.log',
    'artifacts/release-0.1.5/windows-resource-delivery.json',
    'artifacts/gpu-0.1.5/onnx-final-review.json',
    'artifacts/gpu-0.1.5/profile-final-directml-result.json',
    'artifacts/release-0.1.5/macos-gpu-resource-bundle.json',
    'artifacts/release-0.1.5/before-memcpy-fix-tests.json',
    'artifacts/release-0.1.5/before-memcpy-fix-tests.log',
    'artifacts/release-0.1.5/before-worker-destination-fix-macos-build.log',
    'artifacts/gpu-0.1.5/formal-directml-v2-result.json',
    'artifacts/gpu-0.1.5/dml/final-path.json',
    'artifacts/gpu-0.1.5/macos-old-app-process.json',
    'artifacts/gpu-0.1.5/macos-old-app-trash.json',
    'artifacts/gpu-0.1.5/explicit-download-summary.json',
    'artifacts/gpu-0.1.5/directml-download-summary.json',
    'artifacts/gpu-0.1.5/supplemental-license-sources.json',
    'artifacts/gpu-0.1.5/dml/comparison.json',
    'artifacts/gpu-0.1.5/dml/dxgi-adapters.json',
    'artifacts/gpu-0.1.5/dml/firewall.json',
    'artifacts/gpu-0.1.5/dml/offline-result.json',
    'artifacts/gpu-0.1.5/dml/final-import-result.json',
    'artifacts/gpu-0.1.5/dml/local-package-check.json',
    'artifacts/gpu-0.1.5/dml/nuget-license-provenance.json',
    'artifacts/gpu-0.1.5/dml/directml-binary-provenance.json',
    'artifacts/release-0.1.5/tests.json',
    'artifacts/release-0.1.5/tests.log',
    'artifacts/release-0.1.5/tests.stderr.log',
    'artifacts/release-0.1.5/macos-build-info.json',
    'artifacts/release-0.1.5/macos-build.log',
    'artifacts/release-0.1.5/macos-package-integrity.json',
    'artifacts/release-0.1.5/macos-archive-extraction-check.json',
    'artifacts/release-0.1.5/cross-platform-integrity.json',
    'artifacts/release-0.1.5/macos-gui/network-probe.json',
    'artifacts/release-0.1.5/macos-gui/process-audit.json',
    'artifacts/release-0.1.5/macos-gui/epubcheck.json',
    'artifacts/release-0.1.5/macos-gui/epubcheck-running-margins.json',
    'artifacts/release-0.1.5/macos-gui/gui/result.json',
    'artifacts/release-0.1.5/macos-gui/gui/worker.log',
    'artifacts/release-0.1.5/macos-gui/app.stdout.log',
    'artifacts/release-0.1.5/macos-gui/app.stderr.log',
    'artifacts/release-0.1.5/macos-page-number-repair-gui/network-probe.json',
    'artifacts/release-0.1.5/macos-page-number-repair-gui/process-audit.json',
    'artifacts/release-0.1.5/macos-page-number-repair-gui/epubcheck.json',
    'artifacts/release-0.1.5/macos-page-number-repair-gui/epubcheck-running-margins.json',
    'artifacts/release-0.1.5/macos-page-number-repair-gui/gui/result.json',
    'artifacts/release-0.1.5/macos-page-number-repair-gui/gui/worker.log',
    'artifacts/release-0.1.5/macos-page-number-repair-gui/app.stdout.log',
    'artifacts/release-0.1.5/macos-page-number-repair-gui/app.stderr.log',
    'artifacts/release-0.1.5/macos-page-number-repair-gui/epubcheck-confirmed-page-number.json',

    'artifacts/release-0.1.4/windows-reader-acceptance-summary.json',
    'artifacts/release-0.1.4/windows-frozen-reader-mixed.json',
    'artifacts/release-0.1.4/windows-reader-mixed-started.json',

    'artifacts/release-0.1.4/cross-platform-integrity.json',
    'artifacts/release-0.1.4/windows-new-started.json',
    'artifacts/release-0.1.4/windows-delivery-links.json',
    'artifacts/release-0.1.4/windows-frozen-reader-summary.json',
    'artifacts/release-0.1.4/windows-frozen-reader.json',
    'artifacts/release-0.1.4/windows-reader-started.json',

    'artifacts/production-reader/acceptance-summary.json',
    'artifacts/production-reader/render-final.json',
    'artifacts/production-reader/render-os.json',
    'artifacts/production-reader/os-network-controls.json',
    'artifacts/production-reader/state-comparison-inline.json',
    'artifacts/vivliostyle-running-margins/research-result.json',
    'artifacts/vivliostyle-running-margins/standard-epubcheck.json',
    'artifacts/release-0.1.4/tests.json',
    'artifacts/release-0.1.4/tests.log',
    'artifacts/release-0.1.4/tests.stderr.log',
    'artifacts/release-0.1.4/edge-reader.json',
    'artifacts/release-0.1.4/macos-build-info.json',
    'artifacts/release-0.1.4/macos-build.log',
    'artifacts/release-0.1.4/macos-package-integrity.json',
    'artifacts/release-0.1.4/macos-archive-extraction-check.json',
    'artifacts/release-0.1.4/macos-frozen-reader.json',
    'artifacts/release-0.1.4/macos-frozen-reader-process.json',
    'artifacts/release-0.1.4/source-gui-checks.json',
    'artifacts/release-0.1.4/gui-startup-paths.json',
    'artifacts/release-0.1.4/windows-pytest.log',
    'artifacts/release-0.1.4/windows-pytest.stderr.log',
    'artifacts/release-0.1.4/windows-build-info.json',
    'artifacts/release-0.1.4/windows-build.log',
    'artifacts/release-0.1.4/windows-build.stderr.log',
    'artifacts/release-0.1.4/windows-package.json',
    'artifacts/release-0.1.4/windows-package-integrity.json',
    'artifacts/release-0.1.4/windows-offline.json',
    'artifacts/release-0.1.4/windows-gui.json',
    'artifacts/release-0.1.4/windows-gui-result.json',
    'artifacts/release-0.1.4/windows-gui-worker.log',
    'artifacts/release-0.1.4/windows-gui-process-modules.json',
    'artifacts/release-0.1.4/windows-gui-tcp.json',
    'artifacts/release-0.1.4/windows-gui-running-margins.report.json',
    'artifacts/release-0.1.4/windows-repair-gui.json',
    'artifacts/release-0.1.4/windows-repair-gui-result.json',
    'artifacts/release-0.1.4/windows-repair-gui-worker.log',
    'artifacts/release-0.1.4/windows-repair-gui-process-modules.json',
    'artifacts/release-0.1.4/windows-repair-gui-tcp.json',
    'artifacts/release-0.1.4/windows-repair-confirmed.report.json',
    'artifacts/release-0.1.4/windows-gui-epubcheck.json',
    'artifacts/release-0.1.4/windows-confirmed-sample.json',
    'artifacts/release-0.1.4/macos-gui/app.stderr.log',
    'artifacts/release-0.1.4/macos-gui/app.stdout.log',
    'artifacts/release-0.1.4/macos-gui/book.report.json',
    'artifacts/release-0.1.4/macos-gui/epubcheck.json',
    'artifacts/release-0.1.4/macos-gui/network-probe.json',
    'artifacts/release-0.1.4/macos-gui/process-audit.json',
    'artifacts/release-0.1.4/macos-gui/gui/result.json',
    'artifacts/release-0.1.4/macos-gui/gui/worker.log',
    'artifacts/release-0.1.4/macos-gui/gui/running-margins.report.json',
    'artifacts/release-0.1.4/macos-page-number-repair-gui/app.stderr.log',
    'artifacts/release-0.1.4/macos-page-number-repair-gui/app.stdout.log',
    'artifacts/release-0.1.4/macos-page-number-repair-gui/book.report.json',
    'artifacts/release-0.1.4/macos-page-number-repair-gui/epubcheck.json',
    'artifacts/release-0.1.4/macos-page-number-repair-gui/network-probe.json',
    'artifacts/release-0.1.4/macos-page-number-repair-gui/process-audit.json',
    'artifacts/release-0.1.4/macos-page-number-repair-gui/gui/result.json',
    'artifacts/release-0.1.4/macos-page-number-repair-gui/gui/worker.log',
    'artifacts/release-0.1.4/macos-page-number-repair-gui/gui/running-margins.report.json',
    'artifacts/release-0.1.4/macos-page-number-repair-gui/gui/page-number-confirmed.report.json',

    'artifacts/release-0.1.3/cross-platform-integrity.json',
    'artifacts/release-0.1.3/windows-gui-epubcheck.json',
    'artifacts/release-0.1.3/reader-check.report.json',
    'artifacts/page-number-harness-final/gui/result.json',
    'artifacts/page-number-harness-final/gui/worker.log',
    'artifacts/release-0.1.3/macos-page-number-repair-gui/gui/page-number-confirmed.report.json',
    'artifacts/release-0.1.3/windows-repair-confirmed.report.json',
    'artifacts/page-number-repair-gui/result.json',
    'artifacts/running-margins-probe/research-result.json',
    'artifacts/running-margins-probe/apple-books-result.json',
    'artifacts/running-margins-probe/epubjs-result.json',
    'artifacts/running-margins-probe/01-page-margin-string.epubcheck.json',
    'artifacts/running-margins-probe/02-running-element.epubcheck.json',
    'artifacts/running-margins-probe/03-fixed-position.epubcheck.json',
    'artifacts/running-margins-probe/04-page-margin-constant.epubcheck.json',
    'artifacts/release-0.1.3/tests.json',
    'artifacts/release-0.1.3/tests.log',
    'artifacts/release-0.1.3/tests.stderr.log',
    'artifacts/release-0.1.3/macos-build-info.json',
    'artifacts/release-0.1.3/macos-build.log',
    'artifacts/release-0.1.3/macos-package-integrity.json',
    'artifacts/release-0.1.3/macos-archive-extraction-check.json',
    'artifacts/release-0.1.3/windows-pytest.log',
    'artifacts/release-0.1.3/windows-pytest.stderr.log',
    'artifacts/release-0.1.3/windows-build-info.json',
    'artifacts/release-0.1.3/windows-build.log',
    'artifacts/release-0.1.3/windows-build.stderr.log',
    'artifacts/release-0.1.3/windows-package.json',
    'artifacts/release-0.1.3/windows-cached-export.json',
    'artifacts/release-0.1.3/windows-epubcheck.json',
    'artifacts/release-0.1.3/windows-new-started.json',
    'artifacts/release-0.1.3/apple-books.json',
    'artifacts/release-0.1.3/source-packager-check.json',
    'artifacts/release-0.1.3/windows-gui.json',
    'artifacts/release-0.1.3/windows-gui-result.json',
    'artifacts/release-0.1.3/windows-gui-worker.log',
    'artifacts/release-0.1.3/windows-gui-process-modules.json',
    'artifacts/release-0.1.3/windows-gui-tcp.json',
    'artifacts/release-0.1.3/windows-repair-gui.json',
    'artifacts/release-0.1.3/windows-repair-gui-result.json',
    'artifacts/release-0.1.3/windows-repair-gui-worker.log',
    'artifacts/release-0.1.3/windows-repair-gui-process-modules.json',
    'artifacts/release-0.1.3/windows-repair-gui-tcp.json',
    'artifacts/release-0.1.3/macos-gui/app.stderr.log',
    'artifacts/release-0.1.3/macos-gui/app.stdout.log',
    'artifacts/release-0.1.3/macos-gui/book.report.json',
    'artifacts/release-0.1.3/macos-gui/epubcheck.json',
    'artifacts/release-0.1.3/macos-gui/gui/result.json',
    'artifacts/release-0.1.3/macos-gui/gui/worker.log',
    'artifacts/release-0.1.3/macos-gui/network-probe.json',
    'artifacts/release-0.1.3/macos-gui/network-probe.stderr.log',
    'artifacts/release-0.1.3/macos-gui/process-audit.json',
    'artifacts/release-0.1.3/macos-page-number-repair-gui/app.stderr.log',
    'artifacts/release-0.1.3/macos-page-number-repair-gui/app.stdout.log',
    'artifacts/release-0.1.3/macos-page-number-repair-gui/book.report.json',
    'artifacts/release-0.1.3/macos-page-number-repair-gui/epubcheck.json',
    'artifacts/release-0.1.3/macos-page-number-repair-gui/gui/result.json',
    'artifacts/release-0.1.3/macos-page-number-repair-gui/gui/worker.log',
    'artifacts/release-0.1.3/macos-page-number-repair-gui/network-probe.json',
    'artifacts/release-0.1.3/macos-page-number-repair-gui/network-probe.stderr.log',
    'artifacts/release-0.1.3/macos-page-number-repair-gui/process-audit.json',
    "artifacts/release-0.1.2/source-packager-check.json",
    "artifacts/release-0.1.2/tests.json",
    "artifacts/release-0.1.2/tests.log",
    "artifacts/release-0.1.2/tests.stderr.log",
    "artifacts/release-0.1.2/apple-books.json",
    "artifacts/release-0.1.2/mixed-page-info.report.json",
    "artifacts/release-0.1.2/macos-build-info.json",
    "artifacts/release-0.1.2/macos-build.log",
    "artifacts/release-0.1.2/macos-package-integrity.json",
    "artifacts/release-0.1.2/macos-archive-extraction-check.json",
    "artifacts/release-0.1.2/macos-gui/app.stderr.log",
    "artifacts/release-0.1.2/macos-gui/app.stdout.log",
    "artifacts/release-0.1.2/macos-gui/book.report.json",
    "artifacts/release-0.1.2/macos-gui/epubcheck.json",
    "artifacts/release-0.1.2/macos-gui/gui/result.json",
    "artifacts/release-0.1.2/macos-gui/gui/worker.log",
    "artifacts/release-0.1.2/macos-gui/network-probe.json",
    "artifacts/release-0.1.2/macos-gui/network-probe.stderr.log",
    "artifacts/release-0.1.2/macos-gui/process-audit.json",
    "artifacts/release-0.1.2/windows-pytest.log",
    "artifacts/release-0.1.2/windows-pytest.stderr.log",
    "artifacts/release-0.1.2/windows-build-info.json",
    "artifacts/release-0.1.2/windows-build.log",
    "artifacts/release-0.1.2/windows-build.stderr.log",
    "artifacts/release-0.1.2/windows-package.json",
    "artifacts/release-0.1.2/windows-gui.json",
    "artifacts/release-0.1.2/windows-gui-result.json",
    "artifacts/release-0.1.2/windows-gui-worker.log",
    "artifacts/release-0.1.2/windows-gui-process-modules.json",
    "artifacts/release-0.1.2/windows-gui-tcp.json",
    "artifacts/release-0.1.2/windows-cached-export.json",
    "artifacts/release-0.1.2/windows-epubcheck.json",
    "artifacts/release-0.1.2/windows-new-started.json",
    "artifacts/page-info-gui/result.json",
    "artifacts/release-0.1.1/tests.json",
    "artifacts/release-0.1.1/cached-export-check.json",
    "artifacts/release-0.1.1/macos-build-info.json",
    "artifacts/release-0.1.1/macos-build.log",
    "artifacts/release-0.1.1/macos-package-integrity.json",
    "artifacts/release-0.1.1/macos-archive-extraction-check.json",
    "artifacts/release-0.1.1/macos-gui/app.stderr.log",
    "artifacts/release-0.1.1/macos-gui/app.stdout.log",
    "artifacts/release-0.1.1/macos-gui/book.report.json",
    "artifacts/release-0.1.1/macos-gui/epubcheck.json",
    "artifacts/release-0.1.1/macos-gui/gui/result.json",
    "artifacts/release-0.1.1/macos-gui/gui/worker.log",
    "artifacts/release-0.1.1/macos-gui/network-probe.json",
    "artifacts/release-0.1.1/macos-gui/network-probe.stderr.log",
    "artifacts/release-0.1.1/macos-gui/process-audit.json",
    "artifacts/release-0.1.1/windows-pytest.log",
    "artifacts/release-0.1.1/windows-pytest.stderr.log",
    "artifacts/release-0.1.1/windows-build-info.json",
    "artifacts/release-0.1.1/windows-build.log",
    "artifacts/release-0.1.1/windows-build.stderr.log",
    "artifacts/release-0.1.1/windows-package.json",
    "artifacts/release-0.1.1/windows-gui.json",
    "artifacts/release-0.1.1/windows-gui-result.json",
    "artifacts/release-0.1.1/windows-gui-worker.log",
    "artifacts/release-0.1.1/windows-gui-process-modules.json",
    "artifacts/release-0.1.1/windows-gui-tcp.json",
    "artifacts/release-0.1.1/windows-cached-export.json",
    "artifacts/release-0.1.1/windows-epubcheck.json",
    "artifacts/release-0.1.1/windows-new-started.json",
    "artifacts/margins-gui/result.json",
    "artifacts/margins-gui/issues.txt",
    "artifacts/gui-stable-text/result.json",
    "artifacts/release-build-info.json",
    "artifacts/build-macos-final-docs.log",
    "artifacts/archive-extraction-check.json",
    "artifacts/cli-ascii-probe.log",
    "artifacts/backend-acceptance.json",
    "artifacts/backend-acceptance.log",
    "artifacts/backend-final/summary.json",
    "artifacts/pytest-final.log",
    "artifacts/package-integrity.json",
    "artifacts/opencv-minimal-validation.json",
    "artifacts/opencv-minimal-buildinfo.txt",
    "artifacts/opencv-minimal-linkage.txt",
    "artifacts/packaged-final-utf8.log",
    "artifacts/packaged-final-utf8/summary.json",
    "artifacts/packaged-final-utf8/epubcheck.json",
    "artifacts/packaged-final-utf8/00.log",
    "artifacts/packaged-final-utf8/01.log",
    "artifacts/packaged-final-utf8/02.log",
    "artifacts/packaged-final-utf8/03.log",
    "artifacts/packaged-final-utf8/01_chinese_crosspage.report.json",
    "artifacts/packaged-final-utf8/02_mixed_bad_layer.report.json",
    "artifacts/packaged-final-utf8/03_columns_figures_notes.report.json",
    "artifacts/packaged-final-utf8/01_chinese_crosspage.epubcheck.log",
    "artifacts/packaged-final-utf8/02_mixed_bad_layer.epubcheck.log",
    "artifacts/packaged-final-utf8/03_columns_figures_notes.epubcheck.log",
    "artifacts/offline-final/network-probe.json",
    "artifacts/offline-final/process-audit.json",
    "artifacts/offline-final/run.log",
    "artifacts/offline-final/offline.report.json",
    "artifacts/offline-final/epubcheck.json",
    "artifacts/offline-final/gui/result.json",
    "artifacts/offline-final/gui/worker.log",
    "artifacts/native-release-utf8/process-audit.json",
    "artifacts/native-release-utf8/network-probe.json",
    "artifacts/native-release-utf8/result.json",
    "artifacts/native-release-utf8/epubcheck.json",
    "artifacts/native-release-utf8/app.log",
    "artifacts/native-release-utf8/final-native.report.json",
    "artifacts/macos-gui-package/app.stderr.log",
    "artifacts/macos-gui-package/app.stdout.log",
    "artifacts/macos-gui-package/book.report.json",
    "artifacts/macos-gui-package/epubcheck.json",
    "artifacts/macos-gui-package/gui/result.json",
    "artifacts/macos-gui-package/gui/worker.log",
    "artifacts/macos-gui-package/network-probe.json",
    "artifacts/macos-gui-package/network-probe.stderr.log",
    "artifacts/macos-gui-package/process-audit.json",
    "artifacts/windows/backend-summary.json",
    "artifacts/windows/binaries.json",
    "artifacts/windows/build-info.json",
    "artifacts/windows/docs-refresh.json",
    "artifacts/windows/environment.json",
    "artifacts/windows/hardware-details.json",
    "artifacts/windows/epubcheck.json",
    "artifacts/windows/gui-epubcheck.json",
    "artifacts/windows/gui-offline.json",
    "artifacts/windows/gui-result.json",
    "artifacts/windows/gui-worker.log",
    "artifacts/windows/licenses-inventory.json",
    "artifacts/windows/offline-cli.json",
    "artifacts/windows/package-integrity.json",
    "artifacts/windows/packaged-summary.json",
    "artifacts/windows/process-modules.json",
    "artifacts/windows/pytest.log",
    "artifacts/windows/tcp-samples.json",
    "artifacts/resource-download.log",
    "artifacts/lock-install-dry-run.log",
)
EVIDENCE_DENIED_COMPONENTS = {
    "pages", "raw", "history", "assets", "preview", "previews",
    "resources", "models", "screenshots",
}
EVIDENCE_DENIED_FILES = {
    "source.pdf", "job.json", "document.json", "edits.json", "last-export.json",
    "manifest.json", "password.txt", ".env",
}
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
STORED_SUFFIXES = {".gz", ".whl", ".zip", ".png", ".jpg", ".jpeg", ".pdf", ".epub"}


def private_path(relative: PurePosixPath) -> bool:
    """Private investigations remain excluded even if named as extra evidence."""
    return any(part.casefold() in {"private", "user-data", "user-documents", "existing-cache"}
               or part.casefold().startswith(("user-", "private-"))
               or ".private." in part.casefold()
               for part in relative.parts)


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def excluded(relative: PurePosixPath) -> bool:
    return (private_path(relative)
            or any(part in EXCLUDED_COMPONENTS or part.endswith((".app", ".egg-info"))
                for part in relative.parts)
            or relative.suffix in {".pyc", ".pyo", ".tmp"}
            or relative.name.startswith(".env"))


def task_directory(path: Path) -> bool:
    """Recognize task folders even when they were renamed or copied locally."""
    return (".task" in path.name or path.name.startswith("task-")
            or any((path / marker).is_file() for marker in ("job.json", "document.json", "source.pdf")))


def checked_file(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.is_file():
        raise ValueError(f"Required file does not exist: {relative}")
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError(f"Refusing symbolic link in source delivery: {relative}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Path escapes source root: {relative}")
    return path


def source_files(root: Path, *, include_opencv_source: bool = True) -> dict[str, Path]:
    files = {name: checked_file(root, name) for name in SOURCE_FILES}
    requirements = sorted(root.glob("requirements*"))
    requirements = [path for path in requirements if path.is_file()]
    if not requirements:
        raise ValueError("No locked requirements file found")
    for path in requirements:
        files[path.name] = checked_file(root, path.name)
    for name in SOURCE_DIRECTORIES:
        directory = root / name
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"Required source directory is absent or a symlink: {name}")
        if task_directory(directory):
            raise ValueError(f"Required source directory contains task data: {name}")
        for current, subdirectories, names in os.walk(directory, followlinks=False):
            current_path = Path(current)
            retained = []
            for child in subdirectories:
                candidate = current_path / child
                relative = candidate.relative_to(root)
                if excluded(PurePosixPath(relative.as_posix())) or task_directory(candidate):
                    continue
                if candidate.is_symlink():
                    raise ValueError(f"Refusing symbolic link: {relative}")
                retained.append(child)
            subdirectories[:] = sorted(retained)
            for child in sorted(names):
                relative = (current_path / child).relative_to(root).as_posix()
                if not excluded(PurePosixPath(relative)):
                    if Path(relative).suffix.casefold() == ".epub" or (
                        Path(relative).suffix.casefold() == ".pdf"
                        and not relative.startswith("tests/fixtures/")
                    ):
                        raise ValueError(f"Only self-authored PDF fixtures may enter source delivery: {relative}")
                    files[relative] = checked_file(root, relative)
    # The official source distribution is necessary to rebuild our minimal wheel.
    provenance = json.loads(checked_file(root, "packaging/opencv-source.json").read_text(encoding="utf-8"))
    filename = provenance.get("filename", "")
    if not filename or Path(filename).name != filename or not filename.startswith("opencv-python-"):
        raise ValueError("Invalid OpenCV source provenance filename")
    archive_name = "vendor/sources/" + filename
    if include_opencv_source:
        archive = checked_file(root, archive_name)
        if sha256(archive) != provenance.get("source_sha256"):
            raise ValueError("Official OpenCV source archive does not match the recorded SHA-256")
        if archive_name not in files:
            raise ValueError("Official OpenCV source archive was not selected")
    else:
        # A public Git checkout can keep the pinned official URL/digest and
        # obtain this large source archive separately. Offline source ZIPs use
        # the default branch above and always include and verify its bytes.
        files.pop(archive_name, None)
    if not any(name.startswith("vendor/wheels/") and name.endswith(".whl") for name in files):
        raise ValueError("The locally built OpenCV wheel is missing")
    return files


def evidence_file(root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if (rel.is_absolute() or ".." in rel.parts or "\\" in relative
            or not rel.parts or rel.parts[0] != "artifacts" or len(rel.parts) < 2):
        raise ValueError(f"Evidence must be an explicit relative artifacts path: {relative}")
    if private_path(rel):
        raise ValueError(f"Private material cannot be included as delivery evidence: {relative}")
    if excluded(rel) or any(part in EVIDENCE_DENIED_COMPONENTS or ".task" in part
                            for part in rel.parts) or rel.name in EVIDENCE_DENIED_FILES:
        raise ValueError(f"Task data cannot be included as delivery evidence: {relative}")
    if rel.suffix not in {".json", ".log", ".txt"}:
        raise ValueError(f"Evidence must be JSON, a log, or a text report: {relative}")
    path = checked_file(root, relative)
    for ancestor in path.parents:
        if ancestor == root:
            break
        if task_directory(ancestor):
            raise ValueError(f"Task data cannot be included as delivery evidence: {relative}")
    if path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError(f"Evidence exceeds {MAX_EVIDENCE_BYTES} bytes: {relative}")
    # Explicitly reject a renamed intermediate document or source metadata file.
    if rel.suffix == ".json":
        # Windows PowerShell 5.1 writes UTF-8 JSON with a BOM. Validate it
        # without changing the original evidence bytes placed in the ZIP.
        content = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(content, dict) and (
            ("schema_version" in content and any(key in content for key in ("blocks", "pages", "source")))
            or ("version" in content and "history" in content and "blocks" in content)
            or any(key in content for key in ("password", "api_key", "access_token"))
        ):
            raise ValueError(f"Evidence appears to contain task data or credentials: {relative}")
    return path


def atomic_text(path: Path, value: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def update_checksums(output: Path, digest: str) -> None:
    checksum_path = output.parent / "SHA256SUMS.txt"
    existing = checksum_path.read_text(encoding="utf-8").splitlines() if checksum_path.exists() else []
    preserved = []
    for line in existing:
        match = re.match(r"^[0-9a-fA-F]{64}\s+[* ]?(.*)$", line)
        if match and match.group(1) in {output.name, output.name + ".sha256", checksum_path.name}:
            continue
        preserved.append(line)
    preserved.append(f"{digest}  {output.name}")
    atomic_text(output.with_name(output.name + ".sha256"), f"{digest}  {output.name}\n")
    atomic_text(checksum_path, "\n".join(preserved) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="ZIP path inside the project's dist directory")
    parser.add_argument("--evidence", action="append", default=[], metavar="ARTIFACTS/PATH",
                        help="Explicit additional final JSON/log/text report; never a task directory")
    parser.add_argument("--no-evidence", action="store_true",
                        help="Public-safe source ZIP: omit all machine-specific artifacts, keep documented test results")
    parser.add_argument("--dry-run", action="store_true", help="Validate and list selection; write no files")
    args = parser.parse_args()
    if args.no_evidence and args.evidence:
        raise ValueError("--no-evidence cannot be combined with --evidence")
    root = args.root.expanduser().resolve()
    config = tomllib.loads(checked_file(root, "pyproject.toml").read_text(encoding="utf-8"))
    version = config["project"]["version"]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", version):
        raise ValueError("Unsafe release version")
    output = (args.output or root / "dist" / f"LocalPDFtoEPUB-{version}-source.zip").expanduser().resolve()
    if output.parent != root / "dist" or not output.name.endswith("-source.zip"):
        raise ValueError("Source ZIP must be written inside dist with a filename ending in -source.zip")
    if output.name in {"LocalPDFtoEPUB-ocr-resources.zip", f"LocalPDFtoEPUB-{version}-macos-arm64.zip"}:
        raise ValueError("Refusing to overwrite the application or OCR resource delivery")
    files = source_files(root)
    omitted = []
    for relative in (() if args.no_evidence else FINAL_EVIDENCE):
        if (root / relative).exists():
            files[relative] = evidence_file(root, relative)
        else:
            omitted.append(relative)
    for relative in args.evidence:
        files[relative] = evidence_file(root, relative)
    files = dict(sorted(files.items()))
    summary = {
        "output": str(output), "files": len(files),
        "input_bytes": sum(path.stat().st_size for path in files.values()),
        "evidence": [name for name in files if name.startswith("artifacts/")],
        "optional_evidence_not_present": omitted,
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **summary, "selected_files": list(files)}, ensure_ascii=False, indent=2))
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"LocalPDFtoEPUB-{version}-source/"
    entries = [{"path": name, "bytes": path.stat().st_size, "sha256": sha256(path)} for name, path in files.items()]
    manifest = {
        "format_version": 1, "version": version,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": ("Explicit source directories, official OpenCV source and local wheel; no machine-specific artifacts"
                  if args.no_evidence else "Explicit source directories, official OpenCV source, local wheel, and allowlisted test evidence"),
        "excluded": "Environments, builds, application bundles, OCR model caches, task snapshots, user documents and screenshots",
        "optional_evidence_not_present": omitted, "files": entries,
    }
    descriptor, temporary = tempfile.mkstemp(prefix="source-delivery-", suffix=".zip", dir=output.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for entry in entries:
                name, path = entry["path"], files[entry["path"]]
                info = zipfile.ZipInfo(prefix + name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                mode = stat.S_IFREG | (0o755 if path.stat().st_mode & 0o111 else 0o644)
                info.external_attr = mode << 16
                info.compress_type = zipfile.ZIP_STORED if path.suffix.lower() in STORED_SUFFIXES else zipfile.ZIP_DEFLATED
                observed = hashlib.sha256()
                with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        observed.update(chunk)
                        target.write(chunk)
                if observed.hexdigest() != entry["sha256"]:
                    raise RuntimeError(f"Source changed while packaging; retry after work is finished: {name}")
            archive.writestr(prefix + "SOURCE-PACKAGE-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        with zipfile.ZipFile(temporary) as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError("ZIP integrity verification failed: " + bad)
            expected = {prefix + name for name in files} | {prefix + "SOURCE-PACKAGE-MANIFEST.json"}
            if set(archive.namelist()) != expected:
                raise RuntimeError("ZIP file list differs from the explicit selection")
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    checksum = sha256(output)
    update_checksums(output, checksum)
    print(json.dumps({**summary, "sha256": checksum, "archive_bytes": output.stat().st_size}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        raise SystemExit("Source packaging failed: " + str(exc)) from exc
