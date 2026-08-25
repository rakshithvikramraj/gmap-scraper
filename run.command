#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# setup.command's PATH export only ever reached its own process, so the very
# sequence the README gives - setup.command then run.command in one Terminal -
# ran this with a PATH that had never seen ~/.local/bin.
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. If you just ran setup.command, close this Terminal window,"
  echo "open a new one, and try again."
  exit 1
fi

exec uv run python app.py
