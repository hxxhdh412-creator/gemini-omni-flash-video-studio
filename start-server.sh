#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[Omni Studio] Installing ffmpeg..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y ffmpeg
  fi
fi

pip3 install -r requirements.txt
echo "[Omni Studio] Starting Cloud Server on port ${PORT:-8080}..."
exec python3 omni_server.py --host 0.0.0.0 --port "${PORT:-8080}"
