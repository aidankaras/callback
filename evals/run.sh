#!/usr/bin/env bash
# Run the eval suite with and without the plugin. Extra arguments pass through
# (for example --case no-fabrication, or --runs 1 for a quick check).
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
if [ -n "$(find .venv -type f -links +1 -print -quit 2>/dev/null)" ]; then
  echo "run.sh: .venv holds hard links, which the eval runner refuses;" >&2
  echo "        rebuild it with: UV_LINK_MODE=copy uv sync --locked --reinstall" >&2
  exit 1
fi
mapfile -t tools < <(grep -A1 '^@server.tool()' core/mcp_server.py \
  | sed -n 's/^def \([a-z_]*\)(.*/mcp__plugin_callback_callback__\1/p')
exec claude plugin eval . --runs 3 --mocks off --trust-plugin --scaffold \
  --allow-tools "${tools[@]}" Write Edit WebFetch "Bash(uv run:*)" "$@"
