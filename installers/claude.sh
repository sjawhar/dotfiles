#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ensure_command claude "curl -fsSL https://claude.ai/install.sh | bash"

ensure_clone https://github.com/intellectronica/agent-skills.git "${DOTFILES_DIR}/vendor/agent-skills"

[ -L "${DOTFILES_DIR}/.claude/skills" ] && rm "${DOTFILES_DIR}/.claude/skills"
mkdir -p "${DOTFILES_DIR}/.claude/skills"

if [ -d "${DOTFILES_DIR}/plugins/sjawhar/skills" ]; then
    for skill_dir in "${DOTFILES_DIR}/plugins/sjawhar/skills"/*/; do
        [ -d "$skill_dir" ] || continue
        skill_name=$(basename "$skill_dir")
        ensure_link "../../plugins/sjawhar/skills/${skill_name}" "${DOTFILES_DIR}/.claude/skills/${skill_name}"
    done
fi

if [ -d "${DOTFILES_DIR}/vendor/agent-skills/skills/notion-api" ]; then
    ensure_link "../../vendor/agent-skills/skills/notion-api" "${DOTFILES_DIR}/.claude/skills/notion-api"
fi

[ -d "${DOTFILES_DIR}/.claude/agents" ] && ! [ -L "${DOTFILES_DIR}/.claude/agents" ] && rm -rf "${DOTFILES_DIR}/.claude/agents"
ensure_link "../plugins/sjawhar/agents" "${DOTFILES_DIR}/.claude/agents"
