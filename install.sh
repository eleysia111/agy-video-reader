#!/usr/bin/env bash
set -euo pipefail

TARGET="${HOME}/.gemini/config/plugins/agy-video-reader"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${SRC}" != "${TARGET}" ]; then
  echo "[agy-video-reader] Installing plugin to ${TARGET}..."
  mkdir -p "${TARGET}"
  rsync -a --exclude='.git' --exclude='.venv' --exclude='cache' --exclude='__pycache__' \
    --exclude='cookies.txt' --exclude='settings.json' "${SRC}/" "${TARGET}/"
fi

echo "[agy-video-reader] Syncing isolated uv environment..."
cd "${TARGET}"
uv sync

echo "[agy-video-reader] Installed successfully! Open Antigravity Customizations to manage it."
