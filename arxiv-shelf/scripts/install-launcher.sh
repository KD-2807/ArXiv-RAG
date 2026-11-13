#!/usr/bin/env bash
set -euo pipefail
variant="${1:?Usage: install-launcher.sh python|rust}"
case "$variant" in python|rust) ;; *) exit 2 ;; esac
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
launcher="$HOME/.local/bin/arxiv-shelf-$variant"
printf '#!/usr/bin/env bash\nexec bash %q "$@"\n' "$project_dir/scripts/run-$variant.sh" > "$launcher"
chmod +x "$launcher"
escaped="${launcher//\\/\\\\}"
escaped="${escaped//\"/\\\"}"
escaped="${escaped//%/%%}"
cat > "$HOME/.local/share/applications/arxiv-shelf-$variant.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=arXiv Shelf ($variant)
Comment=Download and organise research papers
Exec="$escaped"
Icon=folder-documents
Terminal=false
Categories=Education;Science;
EOF
printf 'Installed %s\nOpen arXiv Shelf from your application menu.\n' "$launcher"
