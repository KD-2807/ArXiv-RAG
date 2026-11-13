#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$project_dir/python${PYTHONPATH:+:$PYTHONPATH}"
export OPENBLAS_NUM_THREADS=1
exec "$project_dir/.venv/bin/python" -m rag_shelf "$@"
