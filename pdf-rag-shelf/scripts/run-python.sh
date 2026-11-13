#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec wish "$project_dir/ui/app.tcl" python bash "$project_dir/scripts/python-cli.sh"
