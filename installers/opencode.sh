#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_command opencode "curl -fsSL https://opencode.ai/install | bash"

OPENCODE_DIR="${HOME}/.config/opencode"
OC_JSON="${OPENCODE_DIR}/opencode.json"
mkdir -p "$OPENCODE_DIR"

# Vendor clones. Skills/agents/commands from these dirs are picked up by the
# @sjawhar/opencode-claude-bridge plugin via the wrapper in opencode/plugins/dotfiles-bridge.ts.
ensure_vendor https://github.com/sjawhar/streamlinear.git streamlinear
if [ -d "${DOTFILES_DIR}/vendor/streamlinear/.git" ]; then
    git -C "${DOTFILES_DIR}/vendor/streamlinear" remote get-url upstream &>/dev/null || \
        git -C "${DOTFILES_DIR}/vendor/streamlinear" remote add upstream https://github.com/obra/streamlinear.git
fi
ensure_vendor https://github.com/anthropics/skills.git anthropic-skills
ensure_vendor https://github.com/EveryInc/compound-engineering-plugin.git compound-engineering
ensure_vendor https://github.com/sjawhar/legion.git legion

# Native skill discovery: both OpenCode and Claude Code read skills from ~/.claude/skills/<name>/SKILL.md.
# Symlink each source dir under ~/.claude/skills/ so both tools find them with zero plugin code.
# (The bridge still scans the same dirs for disable-model-invocation handling and command registration.)
mkdir -p "${HOME}/.claude/skills"
ensure_link "${DOTFILES_DIR}/plugins/sjawhar/skills"                              "${HOME}/.claude/skills/sjawhar"
ensure_link "${DOTFILES_DIR}/vendor/legion/.opencode/skills"                      "${HOME}/.claude/skills/legion"
ensure_link "${DOTFILES_DIR}/vendor/sentry-for-ai/skills"                         "${HOME}/.claude/skills/sentry-for-ai"
ensure_link "${DOTFILES_DIR}/vendor/ghost-wispr/.opencode/skills"                 "${HOME}/.claude/skills/ghost-wispr"

# Remove legacy OpenCode-specific symlinks that are now redundant. The bridge
# handles agent/command registration; native ~/.claude/skills/ discovery handles skills.
# Keep compound-engineering alone — it intentionally exposes skills as commands as a
# workaround for OpenCode not honoring Claude's disable-model-invocation field.
for legacy_skill in sjawhar skill-creator using-jj legion github linear sentry-for-ai ghost-wispr; do
    [ -L "${OPENCODE_DIR}/skills/${legacy_skill}" ] && rm "${OPENCODE_DIR}/skills/${legacy_skill}"
done
for legacy_cmd_link in sjawhar sentry-for-ai plan-review.md; do
    target="${OPENCODE_DIR}/commands/${legacy_cmd_link}"
    [ -L "$target" ] && rm "$target"
done

# Instructions: share the same markdown between Claude Code and OpenCode.
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

# Install the Claude bridge wrapper's dep.
# opencode/package.json declares @sjawhar/opencode-claude-bridge; bun fetches it here.
if [ -f "${DOTFILES_DIR}/opencode/package.json" ] && command -v bun &>/dev/null; then
    ( cd "${DOTFILES_DIR}/opencode" && bun install )
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
        '(.plugin // []) | any(contains("dotfiles-bridge"))' \
        '(.plugin //= []) | .plugin += ["file://{env:HOME}/.dotfiles/opencode/plugins/dotfiles-bridge.ts"]' \
        "Adding dotfiles-bridge plugin"

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
