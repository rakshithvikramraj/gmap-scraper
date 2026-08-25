#!/usr/bin/env bash
# One-time setup. Installs uv, Python, the dependencies and a browser.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "Installing Python..."
uv python install 3.12

echo "Installing dependencies..."
uv sync

echo "Downloading the browser (about 150MB, one time)..."
uv run playwright install chromium

echo
echo "Setup complete. Double-click run.command to start."
