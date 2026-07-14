#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT_DIR/scripts/docker-local-init.sh"

cd "$ROOT_DIR"
docker compose -f compose.local.yml build
