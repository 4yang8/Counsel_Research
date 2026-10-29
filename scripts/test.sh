#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}/tests:${PROJECT_ROOT}/experiments${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONDONTWRITEBYTECODE=1
exec "${COUNSELING_PYTHON:-python3}" -m unittest discover -s "${PROJECT_ROOT}/tests" -p 'test_*.py' "$@"
