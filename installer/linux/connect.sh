#!/bin/sh
# Another Brain harness connector.
#
#   installer/linux/connect.sh claude-code codex   # connect specific harnesses
#   installer/linux/connect.sh detect              # print detected harnesses
#   installer/linux/connect.sh                     # list known + detected harnesses
#   (macOS: installer/macos/connect.sh — same script)
#
# For each chosen harness this does the full setup, idempotently:
#   1. registers the Another Brain MCP server (Streamable HTTP) in the
#      harness's own MCP config — via its native CLI when it has one
#   2. installs the another-brain skill for that harness
#
# Per-harness logic lives in installer/linux/harnesses/<name>.sh — one file per
# harness, sourced by this script. To support a new harness, add one file
# there defining three functions (the helpers below are available to it):
#
#   harness_detect()     # return 0 when the harness is installed
#   harness_skill_dir()  # print the harness's skills directory
#   harness_register()   # register $SERVER_NAME at $MCP_URL
#
# MCP_URL overrides the endpoint (default http://localhost:1905/mcp).
set -u

MCP_URL="${MCP_URL:-http://localhost:1905/mcp}"
SERVER_NAME="another-brain"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
HARNESS_DIR="$SCRIPT_DIR/harnesses"

# Same output style as install.sh; plain when piped or NO_COLOR is set.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_HEAD='\033[1;34m'; C_WARN='\033[33m'; C_OFF='\033[0m'
else
    C_HEAD=''; C_WARN=''; C_OFF=''
fi
say() { printf '%b==>%b %s\n' "$C_HEAD" "$C_OFF" "$*"; }
warn() { printf '%bwarning:%b %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------- harness discovery

known_harnesses() {
    # No `ls --` (BSD ls lacks it); the glob is safe because the dir is ours.
    (cd "$HARNESS_DIR" && for f in *.sh; do [ -f "$f" ] && printf '%s\n' "${f%.sh}"; done)
}

load_harness() { # load_harness <name> — source its connector file
    case "$1" in *[!A-Za-z0-9_-]*) return 1 ;; esac
    [ -f "$HARNESS_DIR/$1.sh" ] || return 1
    # shellcheck disable=SC1090
    . "$HARNESS_DIR/$1.sh"
}

detect() {
    found=""
    for h in $(known_harnesses); do
        (load_harness "$h" && harness_detect) && found="$found $h"
    done
    echo "$found" | xargs 2>/dev/null
}

skill_dir_for() { # skill_dir_for <harness> — echoes its skills dir, or nothing
    (load_harness "$1" && harness_skill_dir) 2>/dev/null
}

register() {
    if ! load_harness "$1"; then
        warn "unknown harness '$1' — add this to its MCP config by hand:"
        snippet
        return 1
    fi
    harness_register
}

# --------------------------------------------------------- skill install

# Skill install: prefer the `skills` CLI via npx, but it needs Node >= 22
# (its CLI uses top-level await). Otherwise fall back to copying the skill
# directory out of this repo — the skill is plain files, same end result.
SKILL_SRC="$SCRIPT_DIR/../../skills/another-brain"
node_major=0
if have node; then
    node_major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
    case "$node_major" in *[!0-9]*|"") node_major=0 ;; esac
fi
use_npx=0
if have npx && [ "$node_major" -ge 22 ]; then use_npx=1; fi

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

# -------------------------------------------------------- MCP registration

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

# -------------------------------------------------------------------- main

if [ "${1:-}" = "detect" ]; then
    detect
    exit 0
fi

if [ $# -eq 0 ]; then
    echo "Known harnesses: $(known_harnesses | xargs)"
    echo "Detected here:   $(detect || echo none)"
    echo "Usage: $0 [$(known_harnesses | xargs | tr ' ' '|')]..."
    echo "Example: $0 claude-code codex"
    exit 0
fi

SKILL_AGENTS=""
status=0
for agent in "$@"; do
    say "Connecting $agent -> $MCP_URL"
    if register "$agent"; then
        SKILL_AGENTS="$SKILL_AGENTS $agent"
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
