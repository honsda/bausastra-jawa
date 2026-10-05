#!/usr/bin/env bash
# Bausastra launcher (Linux/macOS): build web/ + start API.
set -eu
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[bausastra] No virtualenv at .venv. Create it first:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "[bausastra] Node.js/npm is required to build the frontend."
  exit 1
fi
if [ ! -d "web/node_modules" ]; then
  (cd web && npm install)
fi
(cd web && npm run build)
if [ ! -f "web/dist/index.html" ]; then
  echo "[bausastra] Frontend build failed."
  exit 1
fi
PORT="${1:-5000}"
echo "[bausastra] Starting API + website at http://127.0.0.1:${PORT} ..."
exec .venv/bin/python -m bausastra.cli serve --port "${PORT}"
