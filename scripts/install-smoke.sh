#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/bookhound-install-smoke.XXXXXX")"
PYTHON_BIN="${PYTHON:-python3}"
POETRY_BIN="${POETRY:-poetry}"

cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

cd "$ROOT_DIR"

echo "Cleaning previous build artifacts..."
rm -rf dist build
find . -maxdepth 3 -type d -name "*.egg-info" -prune -exec rm -rf {} +

echo "Building wheel..."
"$POETRY_BIN" build --format wheel

WHEEL_PATH="$(find dist -maxdepth 1 -name "bookhound-*.whl" | sort | tail -n 1)"
if [[ -z "$WHEEL_PATH" ]]; then
  echo "No Bookhound wheel was produced in dist/." >&2
  exit 1
fi

echo "Creating temporary virtual environment..."
"$PYTHON_BIN" -m venv "$TMP_ROOT/venv"
"$TMP_ROOT/venv/bin/python" -m pip install --upgrade pip >/dev/null
"$TMP_ROOT/venv/bin/pip" install "$WHEEL_PATH" >/dev/null

CONFIG_PATH="$TMP_ROOT/bookhound.toml"
cat >"$CONFIG_PATH" <<EOF
[paths]
database_path = "$TMP_ROOT/bookhound.sqlite3"
pdf_directory = "$TMP_ROOT/pdfs"

[sources.arxiv]
enabled = false

[sources.common_crawl]
enabled = false

[sources.seed_crawler]
enabled = false

[sources.sitemap]
enabled = false

[sources.link_expansion]
enabled = false
EOF

BOOKHOUND="$TMP_ROOT/venv/bin/bookhound"

echo "Checking installed CLI help..."
"$BOOKHOUND" --help >/dev/null

echo "Checking installed CLI version..."
"$BOOKHOUND" --version

echo "Checking installed search command..."
"$BOOKHOUND" --config "$CONFIG_PATH" search "machine learning" --limit 1 >/dev/null

echo "Install smoke passed: $WHEEL_PATH"
