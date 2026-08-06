# cursor harness connector — sourced by ../connect.sh.
# Contract: harness_detect / harness_skill_dir / harness_register.

harness_detect() { [ -d "$HOME/.cursor" ]; }

harness_skill_dir() { echo "$HOME/.cursor/skills"; }

harness_register() {
    json_register "$HOME/.cursor/mcp.json"
}
