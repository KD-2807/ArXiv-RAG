#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$project_dir/python${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m arxiv_shelf "$@"
