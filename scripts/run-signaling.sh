#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Run ./scripts/setup.sh first." >&2
  exit 1
fi
echo "Open http://localhost:${PORT:-9000}; use HTTPS behind Caddy for remote clients." >&2
exec .venv/bin/python signaling_server.py --host "${HOST:-0.0.0.0}" --port "${PORT:-9000}" "$@"
