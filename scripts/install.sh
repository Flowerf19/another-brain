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
INSTALL_DIR="${INSTALL_DIR:-$HOME/.another-brain}"
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
if ! have docker; then
    say "      docker not installed — installing automatically"
    have curl || die "curl is required to install docker: https://docs.docker.com/get-docker/"
    curl -fsSL https://get.docker.com | sudo sh >>"$LOG" 2>&1 \
        || die "docker installation failed (see $LOG) — install manually: https://docs.docker.com/get-docker/"
    have docker || die "docker was installed but is not on PATH — re-run this script"
fi
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is required (Docker Desktop / docker-compose-plugin)"
# `docker compose version` works without daemon access — verify the socket too,
# otherwise a missing docker group only surfaces later as a cryptic pull failure.
if [ "${AB_SKIP_DOCKER:-}" != "1" ] && ! docker info >/dev/null 2>&1; then
    # Auto-remediate without asking: start the daemon, then grant this user
    # docker access. sudo may still prompt for a password (on /dev/tty).
    say "      docker daemon unreachable — attempting automatic fix"
    if have systemctl; then
        sudo systemctl enable --now docker >>"$LOG" 2>&1 \
            || sudo service docker start >>"$LOG" 2>&1 \
            || warn "could not start the docker service (see $LOG)"
    elif have service; then
        sudo service docker start >>"$LOG" 2>&1 \
            || warn "could not start the docker service (see $LOG)"
    fi
    if ! docker info >/dev/null 2>&1 \
        && ! id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
        sudo usermod -aG docker "$USER" >>"$LOG" 2>&1 \
            || warn "could not add $USER to the docker group (see $LOG)"
    fi
    if ! docker info >/dev/null 2>&1; then
        # Group membership only applies to new sessions. If the user IS in the
        # docker group (just added, or a stale session), re-run once with the
        # group active via `sg` — no re-login, no confirmation needed.
        if [ "${AB_DOCKER_REEXEC:-}" != "1" ] && have sg \
            && id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
            say "      docker group granted — re-running installer with it active"
            self="$0"
            if [ ! -f "$self" ]; then
                # `curl | sh`: the script is stdin, re-fetch it to a file.
                self="$(mktemp "${TMPDIR:-/tmp}/another-brain-install.XXXXXX.sh")"
                curl -fsSL "https://raw.githubusercontent.com/Flowerf19/another-brain/main/scripts/install.sh" -o "$self" \
                    || die "could not re-download the installer for the docker-group re-run"
            fi
            AB_DOCKER_REEXEC=1 exec sg docker -c "sh '$self'"
        fi
        die "cannot reach the docker daemon — start it (sudo systemctl enable --now docker) and/or re-login so the docker group takes effect, then re-run this script"
    fi
fi
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

    # Pre-download the embedding model (~0.5 GB, one-time) into the shared
    # cache volume: the server lazy-loads it, so a cold first brain_* call
    # would stall or fail. `run` reuses the service volumes.
    # shellcheck disable=SC2086
    run_quiet "      Pre-downloading the embedding model (~0.5 GB, one-time)" \
        docker compose -f "$SRC/docker/docker-compose.yml" $COMPOSE_ENV_ARG \
        run --rm --no-deps server model pull
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
