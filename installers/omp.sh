#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# omp (oh-my-pi) - the binary is mise-managed ("github:sjawhar/oh-my-pi" fork
# release in mise.toml, built by the fork's fork-release.yml workflow); the
# shim at shims/omp injects secrets and envoy env. This installer wires config.

OMP_AGENT_DIR="${HOME}/.omp/agent"
mkdir -p "$OMP_AGENT_DIR" "${HOME}/.omp/agent/prompts"

# Config, agents, and extension entries are canonical in dotfiles (same layout
# idea as ~/.config/opencode -> dotfiles/opencode).
ensure_link "${DOTFILES_DIR}/omp/config.yml"  "${OMP_AGENT_DIR}/config.yml"
ensure_link "${DOTFILES_DIR}/omp/models.yml"  "${OMP_AGENT_DIR}/models.yml"
ensure_link "${DOTFILES_DIR}/omp/mcp.json"    "${OMP_AGENT_DIR}/mcp.json"
ensure_link "${DOTFILES_DIR}/omp/agents"      "${OMP_AGENT_DIR}/agents"
ensure_link "${DOTFILES_DIR}/omp/extensions"  "${OMP_AGENT_DIR}/extensions"

# Extension entries are symlinks into repo checkouts (legion, knives, secrets);
# warn for any target missing on this machine.
for link in "${DOTFILES_DIR}"/omp/extensions/*; do
    [ -L "$link" ] || continue
    [ -e "$(readlink -f "$link" 2>/dev/null || true)" ] || \
        echo "omp: extension $(basename "$link") -> missing target ($(readlink "$link")); clone that repo" >&2
done

# Skills farm: OMP discovers .claude/marketplace/opencode skills natively, but
# some sources (opencode-claude-bridge cache, vendor pools) are outside its
# discovery. ~/.omp/agent/skills/ is a flat dir of symlinks built from the
# manifest; link what resolves on this machine, skip the rest (re-run after
# OpenCode has run once so the bridge cache exists).
if [ -f "${DOTFILES_DIR}/omp/skills-farm.manifest" ]; then
    mkdir -p "${HOME}/.omp/agent/skills"
    missing=0
    while IFS=$'\t' read -r name target; do
        [ -n "$name" ] || continue
        expanded="${target/#\~/$HOME}"
        if [ -e "$expanded" ]; then
            ensure_link "$expanded" "${HOME}/.omp/agent/skills/${name}"
        else
            missing=$((missing + 1))
        fi
    done < "${DOTFILES_DIR}/omp/skills-farm.manifest"
    [ "$missing" -gt 0 ] && echo "omp: ${missing} manifest skills had no local source yet" >&2
fi

# Prompt templates (shared command sources synced as symlinks).
"${DOTFILES_DIR}/scripts/omp-sync-prompts" || echo "omp: prompt sync reported issues" >&2
if [ -d "${DOTFILES_DIR}/omp/prompts" ]; then
    for f in "${DOTFILES_DIR}"/omp/prompts/*; do
        ensure_link "$f" "${HOME}/.omp/agent/prompts/$(basename "$f")"
    done
fi
