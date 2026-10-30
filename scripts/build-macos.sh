#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src"
.venv/bin/python scripts/build.py "$@"
