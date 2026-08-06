# pi harness connector — sourced by ../connect.sh.
# Contract: harness_detect / harness_skill_dir / harness_register.
#
# Pi has no built-in MCP; the pi-mcp-adapter extension provides it and reads
# the shared global config ~/.config/mcp/mcp.json in every project, so that
# is the registration target. (Project-level .mcp.json also works but ties
# the brain to one repo and fails when connect.sh runs outside a project.)

harness_detect() { [ -d "$HOME/.pi" ]; }

harness_skill_dir() { echo "$HOME/.pi/agent/skills"; }

harness_register() {
    json_register "$HOME/.config/mcp/mcp.json"
}
