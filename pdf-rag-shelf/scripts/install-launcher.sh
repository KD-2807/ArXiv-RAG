#!/usr/bin/env bash
set -euo pipefail
variant="${1:-python}"
case "$variant" in python) ;; *) exit 2 ;; esac
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
launcher="$HOME/.local/bin/pdf-rag-shelf-$variant"
printf '#!/usr/bin/env bash\nexec bash %q "$@"\n' "$project_dir/scripts/run-$variant.sh" > "$launcher"
chmod +x "$launcher"
escaped="${launcher//\\/\\\\}"
escaped="${escaped//\"/\\\"}"
escaped="${escaped//%/%%}"
cat > "$HOME/.local/share/applications/pdf-rag-shelf-$variant.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PDF RAG Shelf
Comment=Embed and search local PDF papers
Exec="$escaped"
Icon=folder-documents
Terminal=false
Categories=Education;Science;
EOF
printf 'Installed %s\nOpen PDF RAG Shelf from your application menu.\n' "$launcher"
