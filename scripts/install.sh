#!/bin/sh
# Another Brain one-shot installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Flowerf19/another-brain/main/scripts/install.sh | sh
#
# Steps: preflight -> fetch repo -> Docker stack (Redis 8.8 + MCP server)
# -> connect chosen harnesses via connect.sh (MCP registration + skill).
# Verbose child output goes to $LOG; the terminal stays compact.
#
# Overrides: INSTALL_DIR, AB_SKIP_DOCKER=1, AB_SKIP_SKILL=1, MCP_URL, LOG.
set -u

REPO_URL="https://github.com/Flowerf19/another-brain.git"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.another-brain}"
LOG="${LOG:-${TMPDIR:-/tmp}/another-brain-install.log}"

# Homebrew-style output: bold blue "==>" section headers, results indented
# under them, one blank line between sections. Colors only when stdout is a
# TTY and NO_COLOR (https://no-color.org) is unset.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_HEAD='\033[1;34m'; C_BOLD='\033[1m'; C_OK='\033[32m'
    C_ERR='\033[31m'; C_WARN='\033[33m'; C_DIM='\033[2m'; C_OFF='\033[0m'
else
    C_HEAD=''; C_BOLD=''; C_OK=''; C_ERR=''; C_WARN=''; C_DIM=''; C_OFF=''
fi

section() { printf '\n%b==>%b %b%s%b\n' "$C_HEAD" "$C_OFF" "$C_BOLD" "$*" "$C_OFF"; }
note() { printf '    %s\n' "$*"; }
ok_() { printf '    %bok:%b %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '    %bwarning:%b %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
die() { printf '    %berror:%b %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# run_task [-soft] <label> <cmd...>: one "ok:/FAILED:" result line under the
# current section while the command's verbose output streams to $LOG. On a
# TTY a dim line live-updates with the child's latest log line, so slow
# steps (image build, the ~0.5 GB model download) show real progress.
# On failure print this task's own log excerpt (not the whole shared log)
# and exit — or, with -soft, return 1 instead.
run_task() {
    fatal=1
    if [ "$1" = "-soft" ]; then fatal=0; shift; fi
    label="$1"; shift
    : >>"$LOG"
    log_start=$(wc -c <"$LOG" | tr -d '[:space:]')
    "$@" >>"$LOG" 2>&1 &
    pid=$!
    t0=$(date +%s)
    ok=0
    if [ -t 1 ]; then
        cols=$(tput cols 2>/dev/null) || cols=80
        case "$cols" in *[!0-9]*|"") cols=80 ;; esac
        width=$((cols - 6)); [ "$width" -lt 20 ] && width=20
        while kill -0 "$pid" 2>/dev/null; do
            # Latest line this task wrote: split \r-progress (tqdm) into
            # lines, drop control chars and non-ASCII (avoids cutting a
            # UTF-8 char in half), fit the terminal.
            line=$(tail -c "+$((log_start + 1))" "$LOG" 2>/dev/null \
                | tr '\r' '\n' | tr -cd '[:print:]\n' \
                | grep -v '^[[:space:]]*$' | tail -n 1 | cut -c "1-$width")
            [ -n "$line" ] || line="$label... ($(($(date +%s) - t0))s)"
            printf '\r\033[K    %b%s%b' "$C_DIM" "$line" "$C_OFF"
            sleep 1
        done
        wait "$pid" && ok=1
        dt=$(($(date +%s) - t0))
        printf '\r\033[K'
        if [ "$ok" = 1 ]; then
            printf '    %bok:%b %s %b(%ss)%b\n' "$C_OK" "$C_OFF" "$label" "$C_DIM" "$dt" "$C_OFF"
            return 0
        fi
        printf '    %bFAILED:%b %s %b(%ss)%b\n' "$C_ERR" "$C_OFF" "$label" "$C_DIM" "$dt" "$C_OFF" >&2
    else
        printf '    %s...' "$label"
        while kill -0 "$pid" 2>/dev/null; do printf '.'; sleep 5; done
        if wait "$pid"; then
            printf ' ok\n'
            return 0
        fi
        printf ' FAILED\n' >&2
    fi
    # Only this task's output, prefixed and dimmed.
    printf '%b' "$C_DIM" >&2
    tail -c "+$((log_start + 1))" "$LOG" 2>/dev/null | tr '\r' '\n' \
        | grep -v '^[[:space:]]*$' | tail -n 15 | sed 's/^/      | /' >&2
    printf '%b' "$C_OFF" >&2
    note "full log: $LOG" >&2
    [ "$fatal" = "1" ] && exit 1
    return 1
}

printf '%b==>%b %bAnother Brain installer%b\n' "$C_HEAD" "$C_OFF" "$C_BOLD" "$C_OFF"
printf '    %blog: %s%b\n' "$C_DIM" "$LOG" "$C_OFF"

# ---------------------------------------------------------------- preflight
section "[1/4] Prerequisites"
have git || die "git is required: https://git-scm.com/downloads"
if ! have docker; then
    note "docker not installed — installing automatically"
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
    note "docker daemon unreachable — attempting automatic fix"
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
            note "docker group granted — re-running installer with it active"
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
ok_ "git, docker, compose"
if have nc; then
    for port in 1906 1905; do
        if nc -z 127.0.0.1 "$port" 2>/dev/null; then
            warn "port $port is already in use — create $INSTALL_DIR/.env with"
            warn "REDIS_PORT=<free port> or MCP_HTTP_PORT=<free port>, then re-run this script"
        fi
    done
fi

# --------------------------------------------------------------------- repo
section "[2/4] Repository"
if [ -f docker/docker-compose.yml ] && [ -f skills/another-brain/SKILL.md ]; then
    SRC="$PWD"
    ok_ "using current checkout ($SRC)"
elif [ -d "$INSTALL_DIR/.git" ]; then
    SRC="$INSTALL_DIR"
    run_task "updated ($SRC)" git -C "$SRC" pull --ff-only
else
    SRC="$INSTALL_DIR"
    run_task "cloned -> $SRC" git clone "$REPO_URL" "$SRC"
fi

# ------------------------------------------------------------------- system
COMPOSE_ENV_ARG=""
if [ -f "$SRC/.env" ]; then
    # compose reads env files from the compose file's directory, not the
    # repo root — pass overrides explicitly.
    COMPOSE_ENV_ARG="--env-file $SRC/.env"
fi

section "[3/4] Docker stack"
if [ "${AB_SKIP_DOCKER:-}" = "1" ]; then
    note "skipped (AB_SKIP_DOCKER=1)"
else
    # shellcheck disable=SC2086
    run_task "Redis + MCP server" \
        docker compose -f "$SRC/docker/docker-compose.yml" $COMPOSE_ENV_ARG up -d --build

    # Pre-download the embedding model (~0.5 GB, one-time) into the shared
    # cache volume: the server lazy-loads it, so a cold first brain_* call
    # would stall or fail. `run` reuses the service volumes.
    # shellcheck disable=SC2086
    run_task "embedding model — ~0.5 GB, one-time" \
        docker compose -f "$SRC/docker/docker-compose.yml" $COMPOSE_ENV_ARG \
        run --rm --no-deps server model pull
fi

# ---------------------------------------------------------------- harnesses
# Step 4 delegates to connect.sh, which does BOTH halves per harness:
# register the MCP server in the harness's config AND install the skill.
# A skill without the registered server is inert — the brain_* tools it
# references would not exist in the session.
if [ -z "${MCP_URL:-}" ] && [ -f "$SRC/.env" ]; then
    # Honor a port override so the registered endpoint matches the stack.
    port=$(sed -n 's/^MCP_HTTP_PORT=//p' "$SRC/.env" | tail -n 1)
    [ -n "$port" ] && MCP_URL="http://localhost:$port/mcp"
fi
MCP_URL="${MCP_URL:-http://localhost:1905/mcp}"

detect_skill_agents() {
    found=""
    [ -d "$HOME/.claude" ] && found="$found claude-code"
    [ -d "$HOME/.codex" ] && found="$found codex"
    [ -d "$HOME/.gemini" ] && found="$found gemini-cli"
    [ -d "$HOME/.cursor" ] && found="$found cursor"
    [ -d "$HOME/.pi" ] && found="$found pi"
    echo "$found" | xargs 2>/dev/null
}

section "[4/4] Connect harnesses (MCP server + skill)"
if [ "${AB_SKIP_SKILL:-}" = "1" ]; then
    note "skipped (AB_SKIP_SKILL=1)"
elif [ -w /dev/tty ]; then
    detected=$(detect_skill_agents)
    if [ -z "$detected" ]; then
        note "no known harness detected; connect later: sh $SRC/scripts/connect.sh <harness>"
    else
        # Ask on the terminal, not stdin: under `curl | sh` stdin IS the
        # rest of this script.
        i=0
        for a in $detected; do i=$((i + 1)); eval "choice_$i=$a"; done
        {
            i=0
            printf '     '
            for a in $detected; do i=$((i + 1)); printf ' %d) %s  ' "$i" "$a"; done
            printf '\n    select [numbers / a=all / n=skip]: '
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
            # Not fatal on failure: the server is already up, and the
            # connection can be redone any time with connect.sh.
            # shellcheck disable=SC2086
            run_task -soft "$chosen" \
                env MCP_URL="$MCP_URL" sh "$SRC/scripts/connect.sh" $chosen \
                || note "re-run: sh $SRC/scripts/connect.sh $chosen"
        else
            note "skipped; connect later: sh $SRC/scripts/connect.sh <harness>"
        fi
    fi
else
    # Non-interactive run: never touch agent configs silently.
    note "no terminal; connect later: sh $SRC/scripts/connect.sh <harness>"
fi

section "Done — MCP endpoint: $MCP_URL"
note "Restart connected harnesses so they pick up the MCP server."
