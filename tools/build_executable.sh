#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_VENV="${ROOT_DIR}/.venv"

echo "[packaging] Building Linux executable with PyInstaller..."

if [ ! -d "$BUILD_VENV" ]; then
  uv venv "$BUILD_VENV"
fi

uv pip install --python "$BUILD_VENV/bin/python" pyinstaller -r "$ROOT_DIR/requirements.txt"

echo "[packaging] Cleaning previous builds..."
rm -rf "$ROOT_DIR/dist" "$ROOT_DIR/build" "$ROOT_DIR/buildhooks" "$ROOT_DIR"/*.spec

echo "[packaging] Building..."
uv run --python "$BUILD_VENV/bin/python" pyinstaller --onefile --name "RiiTag-RPC_Linux_x64" \
  --add-data "${ROOT_DIR}/banner.txt:." \
  --add-data "${ROOT_DIR}/config.json:." \
  "$ROOT_DIR/start.py"
echo "[packaging] Done. Executable is in $ROOT_DIR/dist/RiiTag-RPC_Linux_x64"
