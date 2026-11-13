#!/usr/bin/env bash
set -euo pipefail
variant="${1:?Usage: install-timer.sh python|rust [daily|limited] [count]}"
mode="${2:-daily}"
count="${3:-100}"
case "$variant" in python|rust) ;; *) exit 2 ;; esac
case "$mode" in daily|limited) ;; *) exit 2 ;; esac
[[ "$count" =~ ^[0-9]+$ ]] && (( count >= 1 && count <= 1000000 )) || exit 2
launcher="$HOME/.local/bin/arxiv-shelf-$variant"
[[ -x "$launcher" ]] || { printf 'Install the launcher first.\n' >&2; exit 1; }
unit="arxiv-shelf-$variant"
escaped="${launcher//\\/\\\\}"
escaped="${escaped//\"/\\\"}"
escaped="${escaped//%/%%}"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/$unit.service" <<EOF
[Unit]
Description=arXiv Shelf $variant scheduled downloads
StartLimitIntervalSec=0

[Service]
Type=oneshot
ExecStart="$escaped" --headless --mode $mode --limit $count
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=15min
TimeoutStartSec=infinity
TimeoutStopSec=45
EOF
cat > "$HOME/.config/systemd/user/$unit.timer" <<EOF
[Unit]
Description=Daily arXiv Shelf $variant

[Timer]
OnCalendar=*-*-* 09:00:00
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now "$unit.timer"
systemctl --user list-timers "$unit.timer"
