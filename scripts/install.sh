#!/bin/sh
# Native Ubuntu/macOS installer.
set -eu

command -v uv >/dev/null 2>&1 || {
    echo "error: uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
}

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
uv tool install --force "$ROOT"
echo "installed: $(command -v another-brain || echo another-brain)"

if [ "${AB_SKIP_MODEL:-0}" != "1" ]; then
    another-brain model pull
else
    echo "model download skipped; run 'another-brain model pull' before first write/search"
fi

another-brain doctor
echo "Native install complete. Register the stdio command 'another-brain' in your MCP host."
