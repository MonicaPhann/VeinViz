#!/usr/bin/env bash
# Start Vein Assist on macOS or Linux (on Windows, use run.bat).
# The first run creates the virtual environment and installs the Python packages.
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "Python 3.11 or newer is needed (found: $("$PY" --version 2>&1 || echo none))."
  echo "Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

if [ ! -f .venv/installed.ok ]; then
  echo "First run: setting up Python packages. This takes a few minutes..."
  [ -x .venv/bin/python ] || "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/installed.ok
fi

[ -f .env ] || cp .env.example .env
if ! grep -q '^GEMINI_API_KEY=.' .env; then
  echo "Your Gemini API key is missing. Open .env, paste it after GEMINI_API_KEY= and run this again."
  echo "Get a key at https://aistudio.google.com/apikey"
  exit 1
fi

exec .venv/bin/python server.py
