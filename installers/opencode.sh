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
# Pinned: upstreams restructured after these commits (skills moved under
# packages/); consumers below and in skills-sources.json expect this layout.
ensure_vendor https://github.com/EveryInc/compound-engineering-plugin.git compound-engineering d3f35297adccea3ad8735e988253966ffa8cf74c
ensure_vendor https://github.com/sjawhar/legion.git legion
ensure_vendor https://github.com/github/gh-stack.git gh-stack
ensure_vendor https://github.com/DataDog/pup.git pup
ensure_vendor https://github.com/getsentry/sentry-for-ai.git sentry-for-ai 2c34b9a2ecff03005d381e60013d7d5849801a62
ensure_vendor https://github.com/getsentry/cli.git sentry-cli 33028c2ac93e027ce3faa9045efc91d895deae1a
ensure_vendor https://github.com/sjawhar/time-tracker.git time-tracker

# Native skill discovery: both OpenCode and Claude Code read skills from ~/.claude/skills/<name>/SKILL.md.
# Symlink each source dir under ~/.claude/skills/ so both tools find them with zero plugin code.
# (The bridge still scans the same dirs for disable-model-invocation handling and command registration.)
mkdir -p "${HOME}/.claude/skills"
ensure_link "${DOTFILES_DIR}/plugins/sjawhar/skills"                              "${HOME}/.claude/skills/sjawhar"
ensure_link "${DOTFILES_DIR}/vendor/legion/skills"                                 "${HOME}/.claude/skills/legion"
# sentry-for-ai ships ~25 per-platform SDK skills; Sami wants only the Python SDK
# (sentry-cli is its own vendor below). Curate: real dir + one link, replacing the
# old whole-dir symlink if present.
[ -L "${HOME}/.claude/skills/sentry-for-ai" ] && rm "${HOME}/.claude/skills/sentry-for-ai"
mkdir -p "${HOME}/.claude/skills/sentry-for-ai"
ensure_link "${DOTFILES_DIR}/vendor/sentry-for-ai/skills/sentry-python-sdk"       "${HOME}/.claude/skills/sentry-for-ai/sentry-python-sdk"
for stale in "${HOME}/.claude/skills/sentry-for-ai"/*; do
    [ -L "$stale" ] && [ "$(basename "$stale")" != "sentry-python-sdk" ] && rm "$stale"
done
ensure_link "${DOTFILES_DIR}/vendor/ghost-wispr/.opencode/skills"                 "${HOME}/.claude/skills/ghost-wispr"

# Checkout-backed team skill pools for OpenCode only (linked only on machines
# that have the checkout). OMP gets these via skills-sources.json ompSkillsDir
# entries (omp/extensions/dotfiles-skills.ts) — never via this config dir.
[ -d "${HOME}/core-ops/skills" ] && ensure_link "${HOME}/core-ops/skills" "${OPENCODE_DIR}/skills/core-ops"
[ -d "${HOME}/core-context/skills" ] && ensure_link "${HOME}/core-context/skills" "${OPENCODE_DIR}/skills/core-context"

# Remove legacy OpenCode-specific symlinks that are now redundant. The bridge
# handles agent/command registration; native ~/.claude/skills/ discovery handles skills.
# compound-engineering is managed separately below (curated command exposure).
for legacy_skill in sjawhar skill-creator using-jj legion github linear sentry-for-ai ghost-wispr; do
    [ -L "${OPENCODE_DIR}/skills/${legacy_skill}" ] && rm "${OPENCODE_DIR}/skills/${legacy_skill}"
done
for legacy_cmd_link in sjawhar sentry-for-ai plan-review.md; do
    target="${OPENCODE_DIR}/commands/${legacy_cmd_link}"
    [ -L "$target" ] && rm "$target"
done

# compound-engineering stays under OPENCODE_DIR/skills — it intentionally exposes
# skills as commands (workaround for OpenCode not honoring Claude's
# disable-model-invocation field). Curate: real dir + per-skill links, replacing
# the old whole-dir symlink. Skip list mirrors omp's skills.ignoredSkills
# entries in omp/config.yml (compound-engineering exclusions).
CE_DIR="${OPENCODE_DIR}/skills/compound-engineering"
[ -L "$CE_DIR" ] && rm "$CE_DIR"
mkdir -p "$CE_DIR"
for dir in "${DOTFILES_DIR}/vendor/compound-engineering/skills"/*/; do
    dir="${dir%/}"
    name=$(basename "$dir")
    case "$name" in
        ce-commit-push-pr|ce-debug|ce-work|ce-worktree|lfg)
            [ -L "${CE_DIR}/${name}" ] && rm "${CE_DIR}/${name}" ;;
        *)
            ensure_link "$dir" "${CE_DIR}/${name}" ;;
    esac
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

# OMO unified config (read from ~/.omo/omo.jsonc since the OMO 4.19 config unification)
mkdir -p "${HOME}/.omo"
ensure_link "${DOTFILES_DIR}/opencode/omo.jsonc" "${HOME}/.omo/omo.jsonc"
