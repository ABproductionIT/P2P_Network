#!/usr/bin/env bash
# Start signaling on 0.0.0.0:9000 (non-privileged; no sudo).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Run ./scripts/setup.sh first." >&2
  exit 1
fi

exec .venv/bin/python signaling_server.py --host "${HOST:-0.0.0.0}" --port "${PORT:-9000}" "$@"
