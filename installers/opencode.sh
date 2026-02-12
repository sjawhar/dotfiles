#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_command opencode "curl -fsSL https://opencode.ai/install | bash"

OPENCODE_DIR="${HOME}/.config/opencode"
VENDOR_DIR="${DOTFILES_DIR}/vendor"
OC_JSON="${OPENCODE_DIR}/opencode.json"
mkdir -p "$OPENCODE_DIR"

ensure_vendor https://github.com/obra/superpowers.git superpowers
ensure_vendor https://github.com/EveryInc/compound-engineering-plugin compound-engineering
ensure_vendor https://github.com/obra/streamlinear.git streamlinear

mkdir -p "${OPENCODE_DIR}/skills"
ensure_link "${VENDOR_DIR}/superpowers/skills" "${OPENCODE_DIR}/skills/superpowers"
ensure_link "${DOTFILES_DIR}/plugins/sjawhar/skills" "${OPENCODE_DIR}/skills/sjawhar"

mkdir -p "${OPENCODE_DIR}/plugins"
ensure_link "${VENDOR_DIR}/superpowers/.opencode/plugins/superpowers.js" "${OPENCODE_DIR}/plugins/superpowers.js"

CE_DIR="${VENDOR_DIR}/compound-engineering"
CE_PLUGIN="${CE_DIR}/plugins/compound-engineering"

# CE commands: symlink under namespaced directory
mkdir -p "${OPENCODE_DIR}/command"
ensure_link "${CE_PLUGIN}/commands" "${OPENCODE_DIR}/command/compound-engineering"

# CE skills: install only model-invocable skills (skip disable-model-invocation: true)
mkdir -p "${OPENCODE_DIR}/skills/compound-engineering"
if [ -d "${CE_PLUGIN}/skills" ]; then
    for skill_dir in "${CE_PLUGIN}/skills"/*/; do
        [ -d "$skill_dir" ] || continue
        skill_md="${skill_dir}SKILL.md"
        [ -f "$skill_md" ] || continue
        if grep -q "disable-model-invocation: true" "$skill_md" 2>/dev/null; then
            continue
        fi
        ensure_link "$skill_dir" "${OPENCODE_DIR}/skills/compound-engineering/$(basename "$skill_dir")"
    done
fi

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
fi

ensure_link "${DOTFILES_DIR}/oh-my-opencode.minimal.json" "${OPENCODE_DIR}/oh-my-opencode.json"
