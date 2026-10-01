#!/usr/bin/env sh
# One-time installer for video-notes (macOS / Linux). Usage: sh install.sh
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
[ -x "$ROOT/.venv/bin/python" ] || "$PYTHON" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip >/dev/null
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT"
echo
echo "Installed. Add this line to your shell profile to use 'video-notes' in any folder:"
echo "  export PATH=\"$ROOT/.venv/bin:\$PATH\""
"$ROOT/.venv/bin/video-notes" doctor || true
