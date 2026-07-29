# claude-code harness connector — sourced by ../connect.sh.
# Contract: harness_detect / harness_skill_dir / harness_register.

harness_detect() { [ -d "$HOME/.claude" ]; }

harness_skill_dir() { echo "$HOME/.claude/skills"; }

harness_register() {
    if ! have claude; then warn "claude CLI not found — register by hand:"; snippet; return 1; fi
    # `claude mcp get` also sees project-scope entries, so it cannot gate a
    # user-scope add. On "already exists", remove + re-add so a changed
    # MCP_URL (e.g. a port move) actually lands.
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
}
