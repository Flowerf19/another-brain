#!/bin/sh
# Another Brain harness connector.
#
#   scripts/connect.sh claude-code codex        # connect specific harnesses
#   scripts/connect.sh                          # list detected harnesses
#
# For each chosen harness this does the full setup, idempotently:
#   1. registers the Another Brain MCP server (Streamable HTTP) in the
#      harness's own MCP config — via its native CLI when it has one
#   2. installs the another-brain skill for that harness (skills CLI)
#
# MCP_URL overrides the endpoint (default http://localhost:1905/mcp).
set -u

MCP_URL="${MCP_URL:-http://localhost:1905/mcp}"
SERVER_NAME="another-brain"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

KNOWN="claude-code codex gemini-cli cursor pi"

# Skill install: prefer the `skills` CLI via npx, but it needs Node >= 22
# (its CLI uses top-level await). Otherwise fall back to copying the skill
# directory out of this repo — the skill is plain files, same end result.
SKILL_SRC="$(dirname -- "$0")/../skills/another-brain"
node_major=0
if have node; then
    node_major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
    case "$node_major" in *[!0-9]*|"") node_major=0 ;; esac
fi
use_npx=0
if have npx && [ "$node_major" -ge 22 ]; then use_npx=1; fi

skill_dir_for() {
    case "$1" in
        claude-code) echo "$HOME/.claude/skills" ;;
        codex)       echo "$HOME/.codex/skills" ;;
        gemini-cli)  echo "$HOME/.gemini/skills" ;;
        cursor)      echo "$HOME/.cursor/skills" ;;
        pi)          echo "$HOME/.pi/agent/skills" ;;
    esac
}

install_skill_copy() { # install_skill_copy <harness>...
    if [ ! -d "$SKILL_SRC" ]; then
        warn "skill source not found ($SKILL_SRC) — run connect.sh from the cloned repo"
        return 1
    fi
    rc=0
    for a in "$@"; do
        dest=$(skill_dir_for "$a")
        if [ -z "$dest" ]; then
            warn "no known skills dir for $a — copy $SKILL_SRC there by hand"
            rc=1
            continue
        fi
        # Remove any previous copy first: `cp -R` into an existing directory
        # would nest a second another-brain/ inside the first on re-runs.
        rm -rf "$dest/another-brain"
        if mkdir -p "$dest" && cp -R "$SKILL_SRC" "$dest/another-brain"; then
            say "installed the skill for $a -> $dest/another-brain"
        else
            warn "could not copy the skill for $a (target $dest/another-brain)"
            rc=1
        fi
    done
    return $rc
}

detect() {
    found=""
    [ -d "$HOME/.claude" ] && found="$found claude-code"
    [ -d "$HOME/.codex" ] && found="$found codex"
    [ -d "$HOME/.gemini" ] && found="$found gemini-cli"
    [ -d "$HOME/.cursor" ] && found="$found cursor"
    [ -d "$HOME/.pi" ] && found="$found pi"
    echo "$found" | xargs 2>/dev/null
}

# Upsert one mcpServers entry into a JSON file via python3.
json_register() { # json_register <file>
    have python3 || { warn "python3 not found — add this to $1 by hand:"; snippet; return 1; }
    MCP_FILE="$1" MCP_NAME="$SERVER_NAME" MCP_URL="$MCP_URL" python3 - <<'PY'
import json, os, sys
path, name, url = os.environ["MCP_FILE"], os.environ["MCP_NAME"], os.environ["MCP_URL"]
try:
    data = json.load(open(path))
except (FileNotFoundError, ValueError):
    data = {}
data.setdefault("mcpServers", {})[name] = {"type": "http", "url": url}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(f"wrote {path}: mcpServers.{name} = {url}")
PY
}

snippet() {
    cat <<EOF
  "$SERVER_NAME": { "type": "http", "url": "$MCP_URL" }
EOF
}

register() {
    case "$1" in
        claude-code)
            if ! have claude; then warn "claude CLI not found — register by hand:"; snippet; return 1; fi
            # `claude mcp get` also sees project-scope entries, so it cannot
            # gate a user-scope add. On "already exists", remove + re-add so
            # a changed MCP_URL (e.g. a port move) actually lands.
            out=$(claude mcp add --transport http "$SERVER_NAME" "$MCP_URL" -s user 2>&1) || {
                case "$out" in
                    *"already exists"*)
                        claude mcp remove "$SERVER_NAME" -s user >/dev/null 2>&1
                        claude mcp add --transport http "$SERVER_NAME" "$MCP_URL" -s user >/dev/null 2>&1 \
                            || { warn "could not update the existing claude-code entry — run by hand: claude mcp remove $SERVER_NAME -s user && claude mcp add --transport http $SERVER_NAME $MCP_URL -s user"; return 1; }
                        say "claude-code: re-registered -> $MCP_URL"
                        ;;
                    *) printf '%s\n' "$out" >&2; return 1 ;;
                esac
            }
            ;;
        codex)
            if ! have codex; then warn "codex CLI not found — register by hand in ~/.codex/config.toml"; return 1; fi
            # `codex mcp add` is idempotent (overwrites the same name).
            codex mcp add "$SERVER_NAME" --url "$MCP_URL"
            ;;
        gemini-cli)
            json_register "$HOME/.gemini/settings.json"
            ;;
        cursor)
            json_register "$HOME/.cursor/mcp.json"
            ;;
        pi)
            # Pi has no built-in MCP (extension-provided); project-level
            # .mcp.json is the convention — register in the current directory.
            if [ -f package.json ] || [ -f pyproject.toml ] || [ -d .git ]; then
                json_register "$PWD/.mcp.json"
            else
                warn "pi: run this from your project root (writes .mcp.json there); Pi needs an MCP extension"
                return 1
            fi
            ;;
        *)
            warn "unknown harness '$1' — add this to its MCP config by hand:"
            snippet
            return 1
            ;;
    esac
}

if [ $# -eq 0 ]; then
    echo "Detected harnesses: $(detect || echo none)"
    echo "Usage: $0 [$(echo "$KNOWN" | tr ' ' '|')]..."
    echo "Example: $0 claude-code codex"
    exit 0
fi

SKILL_AGENTS=""
status=0
for agent in "$@"; do
    say "Connecting $agent -> $MCP_URL"
    if register "$agent"; then
        case "$agent" in
            claude-code|codex|gemini-cli|cursor|pi) SKILL_AGENTS="$SKILL_AGENTS $agent" ;;
        esac
    else
        status=1
    fi
done

if [ -n "$SKILL_AGENTS" ]; then
    if [ "$use_npx" = "1" ]; then
        agent_args=""
        for a in $SKILL_AGENTS; do agent_args="$agent_args -a $a"; done
        say "Installing the another-brain skill for:$SKILL_AGENTS"
        # shellcheck disable=SC2086
        npx -y skills add Flowerf19/another-brain -g -y $agent_args < /dev/null || status=1
    else
        say "Installing the another-brain skill for:$SKILL_AGENTS (direct copy — npx needs Node >= 22)"
        # shellcheck disable=SC2086
        install_skill_copy $SKILL_AGENTS || status=1
    fi
fi

say "Done. Restart the harness so it picks up the new MCP server."
exit $status
