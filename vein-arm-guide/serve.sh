#!/usr/bin/env bash
# Serve the arm page on port 8081 (macOS / Linux; on Windows, use serve.bat).
cd "$(dirname "$0")"
PORT="${PORT:-8081}"
echo "Open http://localhost:$PORT in your browser."
IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8', 80)); print(s.getsockname()[0])" 2>/dev/null) && echo "Other devices on this network (and the Quest): http://$IP:$PORT"
exec python3 -m http.server "$PORT" --bind 0.0.0.0
