# gemini-cli harness connector — sourced by ../connect.sh.
# Contract: harness_detect / harness_skill_dir / harness_register.

harness_detect() { [ -d "$HOME/.gemini" ]; }

harness_skill_dir() { echo "$HOME/.gemini/skills"; }

harness_register() {
    json_register "$HOME/.gemini/settings.json"
}
