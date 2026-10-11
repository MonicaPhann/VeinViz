#!/usr/bin/env bash
# Start Vein Assist. First run creates the virtualenv and installs dependencies.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
exec .venv/bin/python server.py
