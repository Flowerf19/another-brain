# codex harness connector — sourced by ../connect.sh.
# Contract: harness_detect / harness_skill_dir / harness_register.

harness_detect() { [ -d "$HOME/.codex" ]; }

harness_skill_dir() { echo "$HOME/.codex/skills"; }

harness_register() {
    if ! have codex; then warn "codex CLI not found — register by hand in ~/.codex/config.toml"; return 1; fi
    # `codex mcp add` is idempotent (overwrites the same name).
    codex mcp add "$SERVER_NAME" --url "$MCP_URL"
}
