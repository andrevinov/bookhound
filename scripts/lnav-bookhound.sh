#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_PATH="${BOOKHOUND_LNAV_LOG_PATH:-$ROOT_DIR/.local/data/bookhound.jsonl}"

if [[ ! -f "$LOG_PATH" ]]; then
  mkdir -p "$(dirname "$LOG_PATH")"
  : > "$LOG_PATH"
fi

exec lnav -N -I "$ROOT_DIR/config/lnav" "$LOG_PATH" "$@"
