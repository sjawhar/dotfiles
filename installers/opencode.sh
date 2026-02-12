#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_command opencode "curl -fsSL https://opencode.ai/install | bash"

OPENCODE_DIR="${HOME}/.config/opencode"
OC_JSON="${OPENCODE_DIR}/opencode.json"
mkdir -p "$OPENCODE_DIR"

ensure_clone https://github.com/EveryInc/compound-engineering-plugin "${OPENCODE_DIR}/compound-engineering"

AGENTS_SRC="${OPENCODE_DIR}/compound-engineering/plugins/compound-engineering/agents"
mkdir -p "${OPENCODE_DIR}/agents"
if [ -d "$AGENTS_SRC" ]; then
    find "$AGENTS_SRC" -type f -name '*.md' -exec ln -sfn {} "${OPENCODE_DIR}/agents/" \;
fi

ensure_clone https://github.com/obra/superpowers.git "${OPENCODE_DIR}/superpowers"

ensure_command oh-my-opencode "npm install -g oh-my-opencode"

if [ ! -f "$OC_JSON" ]; then
    if [ -t 0 ]; then
        echo "Running oh-my-opencode install..."
        oh-my-opencode install
    else
        echo "Skipping oh-my-opencode install (non-interactive). Run 'oh-my-opencode install' manually."
    fi
fi

if [ -f "$OC_JSON" ] && command -v jq &>/dev/null; then
    ensure_json "$OC_JSON" \
        '(.plugin // []) | any(contains("opencode-antigravity-auth"))' \
        '(.plugin //= []) | .plugin += ["opencode-antigravity-auth@beta"]' \
        "Adding opencode-antigravity-auth plugin"

    ensure_json "$OC_JSON" \
        '.provider.anthropic.models["claude-opus-4-6"].limit.context == 1000000' \
        '.provider.anthropic.models["claude-opus-4-6"].limit = {"context": 1000000, "output": 128000}' \
        "Setting Opus 4.6 context limit to 1M"

    ensure_json "$OC_JSON" \
        '.autoupdate == false' \
        '.autoupdate = false' \
        "Disabling autoupdate"

    ensure_json "$OC_JSON" \
        '.mcp.linear' \
        '.mcp.linear = {"type": "local", "command": ["npx", "-y", "github:obra/streamlinear"], "environment": {"LINEAR_API_TOKEN": "$LINEAR_API_TOKEN"}, "enabled": true}' \
        "Adding Streamlinear MCP config"
fi

ensure_link "${DOTFILES_DIR}/oh-my-opencode.minimal.json" "${OPENCODE_DIR}/oh-my-opencode.json"

mkdir -p "${OPENCODE_DIR}/skills"
for skill_dir in "${DOTFILES_DIR}"/opencode-skills/*/; do
    [ -d "$skill_dir" ] || continue
    ensure_link "$skill_dir" "${OPENCODE_DIR}/skills/$(basename "$skill_dir")"
done
