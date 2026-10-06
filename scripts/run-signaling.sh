#!/usr/bin/env bash
# Run signaling in the foreground on 0.0.0.0:9000 (non-privileged; no sudo).
# Logs print to this terminal (stdout/stderr). Ctrl+C stops the process.
# Do not background with nohup, &, or systemd if you want visible logs here.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Run ./scripts/setup.sh first." >&2
  exit 1
fi

echo "Starting signaling in foreground on ${HOST:-0.0.0.0}:${PORT:-9000} (Ctrl+C to stop; logs below)." >&2
echo "Clients use ws://127.0.0.1:${PORT:-9000} or ws://<LAN-IP>:${PORT:-9000} — not 0.0.0.0, not wss:// unless TLS is in front." >&2
exec .venv/bin/python signaling_server.py --host "${HOST:-0.0.0.0}" --port "${PORT:-9000}" "$@"
