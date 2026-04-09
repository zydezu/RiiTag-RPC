#!/usr/bin/env bash
set -euo pipefail

# Build a Linux single-file executable for RiiTag-RPC using PyInstaller
# This script creates a Python venv, installs dependencies, and builds start.py into dist/start

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_VENV="${ROOT_DIR}/.venv"

echo "[packaging] Building Linux executable with PyInstaller..."

if [ -d "$BUILD_VENV" ]; then
  echo "[packaging] Using existing virtual environment: $BUILD_VENV"
else
  python3 -m venv "$BUILD_VENV"
fi
source "${BUILD_VENV}/bin/activate"

pip install --upgrade pip setuptools wheel
pip install -r "$ROOT_DIR/requirements.txt"
pip install pyinstaller

banner_file="${ROOT_DIR}/banner.txt"
config_file="${ROOT_DIR}/config.json"

echo "[packaging] Cleaning previous builds..."
rm -rf "$ROOT_DIR/dist" "$ROOT_DIR/build" "$ROOT_DIR/start.spec"

echo "[packaging] Building..."
# PyInstaller add-data syntax on Linux: source:destination
pyinstaller --onefile --name "RiiTag-RPC_Linux_x64" --add-data "${banner_file}:." --add-data "${config_file}:." "$ROOT_DIR/start.py"

echo "[packaging] Done. Executable is in $ROOT_DIR/dist/start (Linux)."
