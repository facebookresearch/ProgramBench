#!/bin/bash
set -e
cd "$(dirname "$0")/.."
pip install pytest --quiet 2>/dev/null || true
python3 -m pytest eval/tests/ --junitxml=eval/results.xml -v || true
