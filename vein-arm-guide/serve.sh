#!/usr/bin/env bash
# Serve the arm page on port 8081 (macOS / Linux; on Windows, use serve.bat). Open http://localhost:8081 here,
# or http://<this-computer's-IP>:8081 on the Quest.
cd "$(dirname "$0")"
exec python3 -m http.server "${PORT:-8081}" --bind 0.0.0.0
