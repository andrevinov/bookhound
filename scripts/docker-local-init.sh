#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DIR="$ROOT_DIR/.local"
CONFIG_PATH="$LOCAL_DIR/bookhound.toml"
DATA_DIR="$LOCAL_DIR/data"
TEMPLATE_PATH="$ROOT_DIR/config/bookhound.docker.example.toml"
SECRETS_TEMPLATE_PATH="$ROOT_DIR/config/bookhound.docker.with-secrets.toml"

mkdir -p "$DATA_DIR"

if [[ -f "$SECRETS_TEMPLATE_PATH" ]]; then
  cp "$SECRETS_TEMPLATE_PATH" "$CONFIG_PATH"
  echo "Updated $CONFIG_PATH from $SECRETS_TEMPLATE_PATH"
elif [[ ! -f "$CONFIG_PATH" ]]; then
  cp "$TEMPLATE_PATH" "$CONFIG_PATH"
  echo "Created $CONFIG_PATH from $TEMPLATE_PATH"
fi
