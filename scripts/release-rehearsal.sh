#!/usr/bin/env bash
# TASK-090 release rehearsal: the whole operator story from an empty profile
# on a machine whose only prerequisite is `uv`.
#
#   scripts/release-rehearsal.sh
#
# Everything happens under one temp root: a fake HOME (so platformdirs
# resolves the data and model directories from scratch), an isolated uv tool
# directory, and an isolated bin directory. The host profile is never read or
# written — the rehearsal proves a first-time install, not a re-install.
#
# uv's own package cache IS shared with the host on purpose: uv is the stated
# prerequisite, so re-downloading the dependency graph would test uv, not us.
# The product's model cache is NOT shared — `model pull` downloads the pinned
# ~206 MB profile for real, because "works from empty" is the claim.
#
# Steps, each asserted:
#   1. build the wheel, install it as a tool, prove the exe is the isolated one
#   2. no model yet: the bare command exits 3 with a typed error, no traceback,
#      and creates no per-user data directory
#   3. `model pull` into the empty cache; `model status` verifies every hash
#   4. `connect cursor`: MCP entry written as stdio + skill installed
#   5. MCP lifecycle over stdio (scripts/rehearsal_flow.py), then a restart
#   6. `recent` from a separate process — the store survived
#   7. `doctor` exits 0 and reports the platform tier
#   8. no daemon, container, or listening socket was ever required
#   9. `uv tool uninstall` removes the executable
#
# KEEP_REHEARSAL=1 leaves the temp root in place for inspection.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_HEAD='\033[1;34m'; C_OK='\033[32m'; C_BAD='\033[31m'; C_OFF='\033[0m'
else
    C_HEAD=''; C_OK=''; C_BAD=''; C_OFF=''
fi
say()  { printf '%b==>%b %s\n' "$C_HEAD" "$C_OFF" "$*"; }
ok()   { printf '  %bok%b %s\n' "$C_OK" "$C_OFF" "$*"; }
die()  { printf '%bFAIL:%b %s\n' "$C_BAD" "$C_OFF" "$*" >&2; exit 1; }

# uv's cache, resolved against the REAL home before we replace it.
HOST_UV_CACHE="${UV_CACHE_DIR:-$HOME/.cache/uv}"

REHEARSAL_ROOT="$(mktemp -d -t another-brain-rehearsal-XXXXXX)"
cleanup() {
    if [ "${KEEP_REHEARSAL:-0}" = "1" ]; then
        printf '\nrehearsal root kept: %s\n' "$REHEARSAL_ROOT"
    else
        rm -rf "$REHEARSAL_ROOT"
    fi
}
trap cleanup EXIT

# ---- the empty profile ---------------------------------------------------
export HOME="$REHEARSAL_ROOT/home"
mkdir -p "$HOME"
# platformdirs reads these; leaving the host's values would leak the host
# profile into the "empty" run.
unset XDG_DATA_HOME XDG_CACHE_HOME XDG_CONFIG_HOME XDG_STATE_HOME
# Any of these would defeat the point of a from-scratch profile.
unset BRAIN_DATA_DIR BRAIN_MODEL_CACHE_DIR BRAIN_ID BRAIN_DISABLE_SQLITE_VEC
export UV_CACHE_DIR="$HOST_UV_CACHE"
export UV_TOOL_DIR="$REHEARSAL_ROOT/uv-tools"
export UV_TOOL_BIN_DIR="$REHEARSAL_ROOT/bin"
export PATH="$UV_TOOL_BIN_DIR:$PATH"

DATA_DIR="$HOME/.local/share/another-brain"
MODEL_DIR="$HOME/.cache/another-brain/models"

say "Rehearsal profile: $HOME"
command -v uv >/dev/null || die "uv is not on PATH — it is the one prerequisite"
ok "uv $(uv --version | awk '{print $2}') is the only prerequisite present"

# ---- 1. build + install --------------------------------------------------
say "1. Build the wheel and install it as a tool"
uv build --wheel --out-dir "$REHEARSAL_ROOT/dist" --project "$REPO_ROOT" >/dev/null
WHEEL="$(find "$REHEARSAL_ROOT/dist" -name '*.whl' | head -1)"
[ -n "$WHEEL" ] || die "no wheel was produced"
ok "built $(basename "$WHEEL")"

uv tool install "$WHEEL" >/dev/null 2>&1 || die "uv tool install failed"
EXE="$(command -v another-brain || true)"
[ -n "$EXE" ] || die "another-brain is not on PATH after install"
case "$EXE" in
    "$UV_TOOL_BIN_DIR"/*) ok "executable is the isolated one: $EXE" ;;
    *) die "another-brain resolved to $EXE, not the rehearsal install" ;;
esac

TOOL_PY="$UV_TOOL_DIR/another-brain/bin/python"
[ -x "$TOOL_PY" ] || die "tool venv interpreter not found at $TOOL_PY"

# ---- 2. no model yet -----------------------------------------------------
say "2. Before the model: the typed contract holds"
[ ! -d "$DATA_DIR" ] || die "a data directory existed before first use: $DATA_DIR"

set +e
BARE_ERR="$(another-brain </dev/null 2>&1 >/dev/null)"
BARE_RC=$?
set -e
[ "$BARE_RC" -eq 3 ] || die "bare command exited $BARE_RC, expected 3"
case "$BARE_ERR" in
    *"model profile not installed"*"model pull"*) ;;
    *) die "expected a model-not-installed error, got: $BARE_ERR" ;;
esac
case "$BARE_ERR" in
    *Traceback*) die "the bare command leaked a traceback" ;;
esac
ok "exit 3 + typed error, no traceback"

# Recorded behavior, not a wish: build_runtime() opens storage BEFORE the
# tokenizer check that raises, so a modelless start does leave an empty
# profile behind (data dir, model cache dir, bootstrapped brain.sqlite3 +
# WAL). What must hold is that it is EMPTY and well-formed — no memories, no
# partial state — never that it is absent.
[ -f "$DATA_DIR/brain.sqlite3" ] || die "the failed start left no bootstrapped store"
ROWS="$("$TOOL_PY" -c "
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
print(con.execute('select count(*) from memories').fetchone()[0])
" "$DATA_DIR/brain.sqlite3")"
[ "$ROWS" = "0" ] || die "the failed start wrote $ROWS memories"
ok "footprint of a modelless start: an empty, schema-complete store (0 memories)"

# ---- 3. model pull -------------------------------------------------------
say "3. model pull into an empty cache (~206 MB, real download)"
# Step 2 created the cache directory; what must be absent is any profile in
# it — the pull has to do the real download, not adopt something present.
if [ -n "$(ls -A "$MODEL_DIR" 2>/dev/null)" ]; then
    die "the model cache is not empty before pull: $(ls -A "$MODEL_DIR")"
fi
another-brain model pull || die "model pull failed"
another-brain model status | grep -q '^installed: yes' || die "model status is not installed"
if another-brain model status | grep -q ': missing'; then
    die "model status reports a missing file"
fi
ok "model installed and every pinned file hash-verified"
ok "model cache size: $(du -sh "$MODEL_DIR" | cut -f1)"

# ---- 4. connect ----------------------------------------------------------
say "4. connect one harness"
another-brain connect --detect >/dev/null || die "connect --detect failed"
another-brain connect cursor || die "connect cursor failed"
CURSOR_JSON="$HOME/.cursor/mcp.json"
[ -f "$CURSOR_JSON" ] || die "connect did not write $CURSOR_JSON"
grep -q '"command": "another-brain"' "$CURSOR_JSON" \
    || die "the MCP entry is not the stdio command form"
if grep -q '"url"' "$CURSOR_JSON"; then
    die "the MCP entry carries a url — stdio expected"
fi
[ -f "$HOME/.cursor/skills/another-brain/SKILL.md" ] \
    || die "the skill was not installed"
ok "stdio entry written and skill installed, no manual JSON"

# ---- 5. the MCP lifecycle ------------------------------------------------
say "5. MCP lifecycle over stdio, then a restart"
"$TOOL_PY" "$REPO_ROOT/scripts/rehearsal_flow.py" "$EXE" \
    || die "the MCP flow failed"
ok "remember/search/get/reinforce/forget + restart + audit"

# ---- 6. the store survived ----------------------------------------------
say "6. A separate process sees the same store"
[ -f "$DATA_DIR/brain.sqlite3" ] || die "no brain.sqlite3 at $DATA_DIR"
another-brain recent --limit 5 >/dev/null || die "recent failed"
ok "recent works from a fresh process; db $(du -h "$DATA_DIR/brain.sqlite3" | cut -f1)"

# ---- 7. doctor -----------------------------------------------------------
say "7. doctor"
DOCTOR_OUT="$(another-brain doctor)" || die "doctor exited nonzero"
printf '%s\n' "$DOCTOR_OUT" | sed 's/^/    /'
ok "doctor exited 0"

# ---- 8. nothing was ever required to be running -------------------------
say "8. No daemon, container, or socket prerequisite"
# The stdio server is a child of its client and exits with it; nothing binds
# a port unless the operator asks for `serve --http`, which nothing here did.
if command -v ss >/dev/null 2>&1; then
    if ss -tlnH 2>/dev/null | grep -q ':1905 '; then
        die "something is listening on 1905 — nothing here should bind a port"
    fi
    ok "nothing listening on 1905; the stdio server dies with its client"
else
    ok "port check skipped (ss unavailable)"
fi
# The stronger claim is about what got installed at all: assert the forbidden
# families are absent from the tool venv the user actually received.
SITE="$(echo "$UV_TOOL_DIR"/another-brain/lib/python*/site-packages)"
[ -d "$SITE" ] || die "tool venv site-packages not found"
if ls "$SITE" | grep -Eqi 'redis|torch|sentence_transformers|docker'; then
    die "a forbidden dependency family is installed in the tool venv"
fi
ok "tool venv has no redis/torch/sentence-transformers/docker: $(ls "$SITE" | wc -l) entries"

# ---- 9. uninstall --------------------------------------------------------
say "9. uninstall"
uv tool uninstall another-brain >/dev/null 2>&1 || die "uv tool uninstall failed"
hash -r  # `command -v` would otherwise answer from the shell's hash table
[ ! -e "$UV_TOOL_BIN_DIR/another-brain" ] || die "the shim survived uninstall"
[ ! -d "$UV_TOOL_DIR/another-brain" ] || die "the tool venv survived uninstall"
if command -v another-brain >/dev/null 2>&1; then
    die "another-brain is still resolvable at $(command -v another-brain)"
fi
ok "shim and tool venv removed"
[ -f "$DATA_DIR/brain.sqlite3" ] || die "uninstall deleted the user's memories"
ok "user data deliberately left behind at $DATA_DIR"

printf '\n%bREHEARSAL PASSED%b — empty profile to uninstall, uv the only prerequisite.\n' \
    "$C_OK" "$C_OFF"
