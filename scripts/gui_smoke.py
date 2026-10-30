#!/usr/bin/env python3
"""Compatibility wrapper for the explicit in-package GUI acceptance entry.

  PYTHONPATH=src python scripts/gui_smoke.py --source SAMPLE.pdf \
      --resources resources --output artifacts/test.epub --evidence artifacts/gui

Packaged equivalent:
  LocalPDFtoEPUB.exe gui-test --source SAMPLE.pdf --resources resources \
      --output OUTPUT/test.epub --evidence OUTPUT/gui
"""
from local_epub.gui_acceptance import main

if __name__ == "__main__":
    raise SystemExit(main())
