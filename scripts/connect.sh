#!/bin/sh
# Register the installed native stdio command with supported MCP hosts.
set -eu

NAME="another-brain"

json_register() {
    target=$1
    command -v python3 >/dev/null 2>&1 || {
        echo "error: python3 is required to update $target" >&2
        return 1
    }
    MCP_FILE="$target" python3 - <<'PY'
import json, os
path = os.environ["MCP_FILE"]
try:
    with open(path, encoding="utf-8") as source:
        data = json.load(source)
except (FileNotFoundError, ValueError):
    data = {}
data.setdefault("mcpServers", {})["another-brain"] = {
    "command": "another-brain",
    "args": [],
}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as target:
    json.dump(data, target, indent=2, ensure_ascii=False)
    target.write("\n")
print(f"registered native stdio server in {path}")
PY
}

if [ $# -eq 0 ]; then
    echo "usage: $0 claude-code|codex|cursor|gemini-cli|pi [...]"
    exit 0
fi

for harness in "$@"; do
    case "$harness" in
        claude-code)
            claude mcp remove "$NAME" -s user >/dev/null 2>&1 || true
            claude mcp add "$NAME" -s user -- another-brain
            ;;
        codex)
            codex mcp add "$NAME" -- another-brain
            ;;
        cursor) json_register "$HOME/.cursor/mcp.json" ;;
        gemini-cli) json_register "$HOME/.gemini/settings.json" ;;
        pi) json_register "$HOME/.config/mcp/mcp.json" ;;
        *) echo "error: unsupported harness: $harness" >&2; exit 2 ;;
    esac
done
