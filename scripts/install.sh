#!/bin/sh
# Another Brain one-shot installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Flowerf19/another-brain/main/scripts/install.sh | sh
#
# Steps: preflight -> fetch repo -> Docker stack (Redis 8.8 + MCP server)
# -> pick which agent harnesses get the another-brain skill.
# Verbose child output goes to $LOG; the terminal stays compact.
#
# Overrides: INSTALL_DIR, AB_SKIP_DOCKER=1, AB_SKIP_SKILL=1, LOG.
set -u

REPO_URL="https://github.com/Flowerf19/another-brain.git"
INSTALL_DIR="${INSTALL_DIR:-$HOME/another-brain}"
LOG="${LOG:-${TMPDIR:-/tmp}/another-brain-install.log}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# run_quiet <label> <cmd...>: compact status + dots while working; on
# failure dump the log tail and exit.
run_quiet() {
    label="$1"; shift
    printf '%s...' "$label"
    : >>"$LOG"
    "$@" >>"$LOG" 2>&1 &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do printf '.'; sleep 5; done
    if wait "$pid"; then
        printf ' OK\n'
    else
        printf ' FAILED\n' >&2
        tail -n 25 "$LOG" >&2
        exit 1
    fi
}

say "Another Brain installer — log: $LOG"

# ---------------------------------------------------------------- preflight
say "[1/4] Checking prerequisites"
have git || die "git is required: https://git-scm.com/downloads"
have docker || die "docker is required: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is required (Docker Desktop / docker-compose-plugin)"
if ! have npx; then
    warn "npx not found — step 4 (agent skill) will be skipped; install Node.js >= 18 and run:"
    warn "  npx skills add Flowerf19/another-brain -g"
fi
if have nc; then
    for port in 6379 8000; do
        if nc -z 127.0.0.1 "$port" 2>/dev/null; then
            warn "port $port is already in use — create $INSTALL_DIR/.env with"
            warn "REDIS_PORT=<free port> or MCP_HTTP_PORT=<free port>, then re-run this script"
        fi
    done
fi

# --------------------------------------------------------------------- repo
if [ -f docker/docker-compose.yml ] && [ -f skills/another-brain/SKILL.md ]; then
    SRC="$PWD"
    say "[2/4] Repo — using current checkout ($SRC)"
elif [ -d "$INSTALL_DIR/.git" ]; then
    SRC="$INSTALL_DIR"
    run_quiet "[2/4] Updating repo ($SRC)" git -C "$SRC" pull --ff-only
else
    SRC="$INSTALL_DIR"
    run_quiet "[2/4] Cloning repo -> $SRC" git clone "$REPO_URL" "$SRC"
fi

# ------------------------------------------------------------------- system
COMPOSE_ENV_ARG=""
if [ -f "$SRC/.env" ]; then
    # compose reads env files from the compose file's directory, not the
    # repo root — pass overrides explicitly.
    COMPOSE_ENV_ARG="--env-file $SRC/.env"
fi

if [ "${AB_SKIP_DOCKER:-}" = "1" ]; then
    say "[3/4] Docker stack — skipped (AB_SKIP_DOCKER=1)"
else
    # shellcheck disable=SC2086
    run_quiet "[3/4] Starting Redis + MCP server (first run takes minutes)" \
        docker compose -f "$SRC/docker/docker-compose.yml" $COMPOSE_ENV_ARG up -d --build
fi

# -------------------------------------------------------------------- skill
detect_skill_agents() {
    found=""
    [ -d "$HOME/.claude" ] && found="$found claude-code"
    [ -d "$HOME/.codex" ] && found="$found codex"
    [ -d "$HOME/.gemini" ] && found="$found gemini-cli"
    [ -d "$HOME/.cursor" ] && found="$found cursor"
    [ -d "$HOME/.pi" ] && found="$found pi"
    echo "$found" | xargs 2>/dev/null
}

if [ "${AB_SKIP_SKILL:-}" = "1" ]; then
    say "[4/4] Agent skill — skipped (AB_SKIP_SKILL=1)"
elif ! have npx; then
    say "[4/4] Agent skill — skipped (npx missing)"
elif [ -w /dev/tty ]; then
    detected=$(detect_skill_agents)
    if [ -z "$detected" ]; then
        say "[4/4] Agent skill — no known harness detected; install later:"
        say "      npx skills add Flowerf19/another-brain -g"
    else
        # Ask on the terminal, not stdin: under `curl | sh` stdin IS the
        # rest of this script.
        i=0
        for a in $detected; do i=$((i + 1)); eval "choice_$i=$a"; done
        {
            printf '[4/4] Install the another-brain skill for which harnesses?\n'
            i=0
            for a in $detected; do i=$((i + 1)); printf '  %d) %s\n' "$i" "$a"; done
            printf '      [numbers, space-separated; a=all; n=skip] '
        } > /dev/tty
        read -r answer < /dev/tty || answer="n"
        chosen=""
        case "$answer" in
            n|N|no|No|NO) : ;;
            a|A|all|ALL) chosen="$detected" ;;
            *)
                for num in $answer; do
                    eval "agent=\${choice_$num:-}"
                    [ -n "$agent" ] && chosen="$chosen $agent"
                done
                chosen=$(echo "$chosen" | xargs 2>/dev/null)
                ;;
        esac
        if [ -n "$chosen" ]; then
            agent_args=""
            for a in $chosen; do agent_args="$agent_args -a $a"; done
            # shellcheck disable=SC2086
            run_quiet "[4/4] Installing skill for: $chosen" \
                npx -y skills add Flowerf19/another-brain -g -y $agent_args
        else
            say "[4/4] Agent skill — skipped; install later: npx skills add Flowerf19/another-brain -g"
        fi
    fi
else
    # Non-interactive run: never install to agents silently.
    say "[4/4] Agent skill — no terminal; install later: npx skills add Flowerf19/another-brain -g"
fi

say "Done — MCP endpoint: http://localhost:8000/mcp"
say "Connect a harness later: scripts/connect.sh <name>   (in $SRC)"
say "First boot downloads the embedding model (~0.5 GB); first recall may take minutes."
