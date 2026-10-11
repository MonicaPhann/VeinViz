#!/usr/bin/env bash
# Start the vein camera stream. First run creates the virtualenv and installs dependencies.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Open it, set CAMERA_SOURCE to your camera's address, then run ./run.sh again."
  exit 1
fi
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python server.py "$@"
