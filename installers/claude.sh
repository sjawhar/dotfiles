#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_command claude "curl -fsSL https://claude.ai/install.sh | bash"

# Claude plugin registration is handled natively by Claude Code:
# - `.claude-plugin/marketplace.json` at dotfiles root declares this repo as the `sjawhar` marketplace.
# - `plugins/sjawhar/.claude-plugin/plugin.json` declares the `sjawhar` plugin.
# - Install once interactively with `/plugin install sjawhar@sjawhar` in Claude Code.
# - Claude watches skill/command/agent dirs live; no symlinking needed.
#
# We no longer symlink individual skills / agents into `.claude/{skills,agents}/` —
# Claude discovers them through the marketplace plugin instead.

# Remove legacy symlinks from earlier installer versions. Safe because content now flows through the marketplace.
[ -L "${DOTFILES_DIR}/.claude/agents" ] && rm "${DOTFILES_DIR}/.claude/agents"
if [ -d "${DOTFILES_DIR}/.claude/skills" ] && [ ! -L "${DOTFILES_DIR}/.claude/skills" ]; then
    # Previously populated with per-skill symlinks; content now lives in plugins/sjawhar/skills
    # via the marketplace. Safe to remove the staging dir.
    rm -rf "${DOTFILES_DIR}/.claude/skills"
fi

# Vendor clones that Claude plugins / OpenCode bridge rely on.
ensure_vendor https://github.com/intellectronica/agent-skills.git agent-skills

if [ -t 0 ]; then
    echo
    echo "If you haven't installed the sjawhar plugin in Claude Code yet, run:"
    echo "  /plugin install sjawhar@sjawhar"
    echo "from inside a Claude Code session. The sjawhar marketplace is already declared by"
    echo "${DOTFILES_DIR}/.claude-plugin/marketplace.json."
fi
