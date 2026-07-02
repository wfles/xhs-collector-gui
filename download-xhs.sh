#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/XHS-Downloader"
OUTPUT_ROOT="$HOME/Downloads"
OUTPUT_FOLDER="XHS-Downloads"

if [ "$#" -lt 1 ]; then
  echo "Usage: ./download-xhs.sh '<xiaohongshu share link or note link>'"
  echo "You can pass multiple links inside the quoted string, separated by spaces."
  exit 2
fi

mkdir -p "$OUTPUT_ROOT/$OUTPUT_FOLDER"

cd "$APP_DIR"
uv run --no-dev main.py \
  --url "$*" \
  --work_path "$OUTPUT_ROOT" \
  --folder_name "$OUTPUT_FOLDER" \
  --record_data false \
  --image_format AUTO \
  --folder_mode false \
  --author_archive false \
  --download_record false \
  --live_download true \
  --write_mtime true \
  --language zh_CN
