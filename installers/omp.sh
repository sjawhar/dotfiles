#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# omp (oh-my-pi) - the binary is mise-managed ("github:sjawhar/oh-my-pi" fork
# release in mise.toml, built by the fork's fork-release.yml workflow); the
# shim at shims/omp injects secrets and envoy env. This installer wires config.

OMP_AGENT_DIR="${HOME}/.omp/agent"
mkdir -p "$OMP_AGENT_DIR" "${HOME}/.omp/agent/prompts"

# Config and agents are canonical in dotfiles (same layout idea as
# ~/.config/opencode -> dotfiles/opencode).
ensure_link "${DOTFILES_DIR}/omp/config.yml"  "${OMP_AGENT_DIR}/config.yml"
ensure_link "${DOTFILES_DIR}/omp/models.yml"  "${OMP_AGENT_DIR}/models.yml"
ensure_link "${DOTFILES_DIR}/omp/mcp.json"    "${OMP_AGENT_DIR}/mcp.json"
ensure_link "${DOTFILES_DIR}/omp/agents"      "${OMP_AGENT_DIR}/agents"

# Extensions: dotfiles-owned ones link straight into omp/extensions/; the
# repo-backed ones point at their checkouts under $HOME, so the links are
# created here (per machine) rather than committed. Warn when a checkout is
# missing instead of silently skipping.
if [ -L "${OMP_AGENT_DIR}/extensions" ]; then rm "${OMP_AGENT_DIR}/extensions"; fi
mkdir -p "${OMP_AGENT_DIR}/extensions"
ensure_link "${DOTFILES_DIR}/omp/extensions/jj-snapshot.ts" "${OMP_AGENT_DIR}/extensions/jj-snapshot.ts"
REPO_EXTENSIONS=(
  "envoy.ts:${HOME}/legion/default/packages/envoy-omp-extension/extensions/envoy.ts"
  "knives-omp.ts:${HOME}/knives/default/omp/extensions/knives.ts"
  "secretsd-omp.ts:${HOME}/secrets/default/omp/extensions/secretsd.ts"
)
for pair in "${REPO_EXTENSIONS[@]}"; do
    name="${pair%%:*}"; target="${pair#*:}"
    if [ -e "$target" ]; then
        ensure_link "$target" "${OMP_AGENT_DIR}/extensions/${name}"
    else
        echo "omp: extension ${name} -> missing checkout (${target}); clone that repo and re-run" >&2
    fi
done

# Skills: OMP discovers <skills-dir>/<name>/SKILL.md; the flat farm is built
# by scanning dotfiles plugins/ + vendor/ at runtime (portable, no manifest).
"${DOTFILES_DIR}/scripts/omp-sync-skills" || echo "omp: skill sync reported issues" >&2

# Prompt templates (shared command sources synced as symlinks).
"${DOTFILES_DIR}/scripts/omp-sync-prompts" || echo "omp: prompt sync reported issues" >&2
if [ -d "${DOTFILES_DIR}/omp/prompts" ]; then
    for f in "${DOTFILES_DIR}"/omp/prompts/*; do
        ensure_link "$f" "${HOME}/.omp/agent/prompts/$(basename "$f")"
    done
fi
