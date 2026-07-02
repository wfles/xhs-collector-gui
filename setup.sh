#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/XHS-Downloader"

git -C "$ROOT_DIR" submodule update --init --recursive

cd "$APP_DIR"
uv sync --no-dev

echo "Setup complete. Run ./start-xhs-gui.sh from the project root."
