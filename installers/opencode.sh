#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_command opencode "curl -fsSL https://opencode.ai/install | bash"

OPENCODE_DIR="${HOME}/.config/opencode"
OC_JSON="${OPENCODE_DIR}/opencode.json"
mkdir -p "$OPENCODE_DIR"

ensure_vendor https://github.com/obra/streamlinear.git streamlinear

mkdir -p "${OPENCODE_DIR}/skills"
ensure_link "${DOTFILES_DIR}/plugins/sjawhar/skills" "${OPENCODE_DIR}/skills/sjawhar"

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

ensure_link "${DOTFILES_DIR}/opencode/oh-my-opencode.minimal.json" "${OPENCODE_DIR}/oh-my-opencode.json"
