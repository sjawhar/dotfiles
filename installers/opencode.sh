#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_command opencode "curl -fsSL https://opencode.ai/install | bash"

OPENCODE_DIR="${HOME}/.config/opencode"
OC_JSON="${OPENCODE_DIR}/opencode.json"
mkdir -p "$OPENCODE_DIR"

ensure_vendor https://github.com/sjawhar/streamlinear.git streamlinear
if [ -d "${DOTFILES_DIR}/vendor/streamlinear/.git" ]; then
    git -C "${DOTFILES_DIR}/vendor/streamlinear" remote get-url upstream &>/dev/null || \
        git -C "${DOTFILES_DIR}/vendor/streamlinear" remote add upstream https://github.com/obra/streamlinear.git
fi
ensure_vendor https://github.com/anthropics/skills.git anthropic-skills
ensure_vendor https://github.com/EveryInc/compound-engineering-plugin.git compound-engineering
ensure_vendor https://github.com/sjawhar/legion.git legion

mkdir -p "${OPENCODE_DIR}/skills"
ensure_link "${DOTFILES_DIR}/plugins/sjawhar/skills" "${OPENCODE_DIR}/skills/sjawhar"
ensure_link "${DOTFILES_DIR}/plugins/sjawhar/skills/using-jj" "${OPENCODE_DIR}/skills/using-jj"
ensure_link "${DOTFILES_DIR}/vendor/anthropic-skills/skills/skill-creator" "${OPENCODE_DIR}/skills/skill-creator"
ensure_link "${DOTFILES_DIR}/vendor/compound-engineering/plugins/compound-engineering/skills/create-agent-skills" "${OPENCODE_DIR}/skills/create-agent-skills"
ensure_link "${DOTFILES_DIR}/vendor/legion/.opencode/skills" "${OPENCODE_DIR}/skills/legion"

mkdir -p "${OPENCODE_DIR}/commands"
ensure_link "${DOTFILES_DIR}/plugins/sjawhar/commands" "${OPENCODE_DIR}/commands/sjawhar"
ensure_link "${DOTFILES_DIR}/vendor/compound-engineering/plugins/compound-engineering/skills" "${OPENCODE_DIR}/commands/compound-engineering"

ensure_link "${DOTFILES_DIR}/.claude/CLAUDE.md" "${OPENCODE_DIR}/AGENTS.md"

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
        '(.plugin // []) | any(contains("jj-snapshot"))' \
        '(.plugin //= []) | .plugin += ["file://{env:HOME}/.dotfiles/opencode/plugins/jj-snapshot.ts"]' \
        "Adding jj-snapshot plugin"

    ensure_json "$OC_JSON" \
        '.provider.anthropic.models["claude-opus-4-6"].limit.context == 1000000' \
        '.provider.anthropic.models["claude-opus-4-6"].limit = {"context": 1000000, "output": 128000}' \
        "Setting Opus 4.6 context limit to 1M"

    ensure_json "$OC_JSON" \
        '.autoupdate == false' \
        '.autoupdate = false' \
        "Disabling autoupdate"
fi

ensure_link "${DOTFILES_DIR}/opencode/oh-my-opencode.full.json" "${OPENCODE_DIR}/oh-my-opencode.json"
