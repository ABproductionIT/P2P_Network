#!/usr/bin/env bash
# Create .venv and install deps with uv (no sudo, no python3 -m venv).
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install once (user space, no sudo):" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  echo "  source \"\$HOME/.local/bin/env\"" >&2
  exit 1
fi

# Drop a broken ensurepip-less venv if present
if [[ -d .venv ]] && [[ ! -x .venv/bin/python ]]; then
  rm -rf .venv
fi

uv venv .venv
uv pip install -r requirements.txt --python .venv/bin/python
echo "Ready. Run signaling: ./scripts/run-signaling.sh"
