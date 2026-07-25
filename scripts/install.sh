#!/bin/sh
# Another Brain one-shot installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Flowerf19/another-brain/main/scripts/install.sh | sh
#
# What it does, in order:
#   1. preflight — needs git, docker (with the compose plugin), and npx
#   2. repo      — uses the current directory when run from a checkout,
#                  otherwise clones to $INSTALL_DIR (default ~/another-brain)
#   3. system    — docker compose up: Redis 8.8 + the MCP server
#                  (first boot downloads the ~0.5 GB embedding model)
#   4. skill     — installs the brain-memory skill for every supported
#                  agent via the skills CLI (npx skills add ... -g --all)
#
# Everything is overridable via env: INSTALL_DIR, AB_SKIP_DOCKER=1,
# AB_SKIP_SKILL=1.
set -eu

REPO_URL="https://github.com/Flowerf19/another-brain.git"
INSTALL_DIR="${INSTALL_DIR:-$HOME/another-brain}"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

say "Another Brain installer — will: fetch repo -> start Docker stack -> install agent skill"

# ---------------------------------------------------------------- preflight
have git || die "git is required: https://git-scm.com/downloads"
have docker || die "docker is required: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is required (Docker Desktop / docker-compose-plugin)"
if ! have npx; then
    warn "npx not found — step 4 (agent skill) will be skipped; install Node.js >= 18 and run:"
    warn "  npx skills add Flowerf19/another-brain -g -y"
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
if [ -f docker/docker-compose.yml ] && [ -f skills/brain-memory/SKILL.md ]; then
    SRC="$PWD"
    say "Using the current checkout: $SRC"
elif [ -d "$INSTALL_DIR/.git" ]; then
    SRC="$INSTALL_DIR"
    say "Updating existing clone: $SRC"
    git -C "$SRC" pull --ff-only < /dev/null || warn "git pull failed — continuing with the existing checkout"
else
    SRC="$INSTALL_DIR"
    say "Cloning $REPO_URL -> $SRC"
    git clone "$REPO_URL" "$SRC" < /dev/null
fi

# ------------------------------------------------------------------- system
COMPOSE_ENV_ARG=""
if [ -f "$SRC/.env" ]; then
    # compose reads env files from the compose file's directory, not the
    # repo root — pass overrides explicitly.
    COMPOSE_ENV_ARG="--env-file $SRC/.env"
fi

if [ "${AB_SKIP_DOCKER:-}" = "1" ]; then
    say "AB_SKIP_DOCKER=1 — skipping the Docker stack"
else
    say "Starting Redis 8.8 + MCP server (first build/pull takes a few minutes)"
    # shellcheck disable=SC2086
    docker compose -f "$SRC/docker/docker-compose.yml" $COMPOSE_ENV_ARG up -d --build < /dev/null
fi

# ---------------------------------------------------------- skill detection
# Map harness config dirs in $HOME to skills-CLI agent ids. Only the common
# ones — anything else is covered by the manual command printed at the end.
detect_skill_agents() {
    found=""
    [ -d "$HOME/.claude" ] && found="$found claude-code"
    [ -d "$HOME/.codex" ] && found="$found codex"
    [ -d "$HOME/.gemini" ] && found="$found gemini-cli"
    [ -d "$HOME/.cursor" ] && found="$found cursor"
    [ -d "$HOME/.pi" ] && found="$found pi"
    echo "$found" | xargs 2>/dev/null
}

# -------------------------------------------------------------------- skill
if [ "${AB_SKIP_SKILL:-}" = "1" ]; then
    say "AB_SKIP_SKILL=1 — skipping the agent skill"
elif ! have npx; then
    say "Skipping the agent skill (npx missing) — see the warning above"
elif [ -w /dev/tty ]; then
    detected=$(detect_skill_agents)
    if [ -z "$detected" ]; then
        say "No known agent harness detected in \$HOME — skipping the skill."
        say "Install later with the full picker: npx skills add Flowerf19/another-brain -g"
    else
        # Ask on the terminal, not stdin: under `curl | sh` stdin IS the
        # rest of this script.
        i=0
        for a in $detected; do i=$((i + 1)); eval "choice_$i=$a"; done
        {
            printf 'Detected agent harnesses:\n'
            i=0
            for a in $detected; do i=$((i + 1)); printf '  %d) %s\n' "$i" "$a"; done
            printf 'Install the brain-memory skill for which? [numbers, space-separated; a=all; n=skip] '
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
            say "Installing the brain-memory skill for: $chosen"
            # shellcheck disable=SC2086
            npx -y skills add Flowerf19/another-brain -g -y $agent_args < /dev/null
        else
            say "Skipping the skill — pick agents later: npx skills add Flowerf19/another-brain -g"
        fi
    fi
else
    # Non-interactive run: never install to agents silently.
    say "No terminal available — skipping the skill. Install later:"
    say "  npx skills add Flowerf19/another-brain -g"
fi

say "Done."
cat <<'EOF'

The memory service is at http://localhost:8000/mcp (Streamable HTTP).
To connect a harness (registers the MCP server + installs the skill):

    scripts/connect.sh              # list detected harnesses
    scripts/connect.sh claude-code codex

First boot downloads the embedding model (~0.5 GB) — the first recall may
take a few minutes. More: docs/deployment.md in the repo.
EOF
