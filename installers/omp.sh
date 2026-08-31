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
ensure_link "${DOTFILES_DIR}/omp/WATCHDOG.md" "${OMP_AGENT_DIR}/WATCHDOG.md"
ensure_link "${DOTFILES_DIR}/omp/agents"      "${OMP_AGENT_DIR}/agents"

# Extensions: jj-snapshot is dotfiles-owned; everything else is an OMP plugin
# installed from GitHub. The pins live in the committed omp/plugins/package.json
# (same idea as opencode.json's git-pinned plugin entries); bun install
# materializes them. Manage pins with omp plugin install/upgrade - the
# resulting package.json/lockfile changes get committed here.
if [ -L "${OMP_AGENT_DIR}/extensions" ]; then rm "${OMP_AGENT_DIR}/extensions"; fi
mkdir -p "${OMP_AGENT_DIR}/extensions"
ensure_link "${DOTFILES_DIR}/omp/extensions/jj-snapshot.ts" "${OMP_AGENT_DIR}/extensions/jj-snapshot.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/dotfiles-skills.ts" "${OMP_AGENT_DIR}/extensions/dotfiles-skills.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/session-env.ts" "${OMP_AGENT_DIR}/extensions/session-env.ts"
ensure_link "${DOTFILES_DIR}/omp/plugins" "${HOME}/.omp/plugins"
(cd "${DOTFILES_DIR}/omp/plugins" && bun install) || echo "omp: plugin install failed; re-run after fixing git auth" >&2
# The envoy extension installs from npm (@sjawhar/pi-legion-envoy). The old
# git-pinned legion monorepo entry is gone from package.json, but bun install
# does not prune its leftover directory — and OMP discovers extensions by
# walking node_modules for omp.extensions, so a leftover copy double-loads the
# extension and delivers every envoy message twice.
rm -rf "${DOTFILES_DIR}/omp/plugins/node_modules/legion"

# Skills: pools are declared once in skills-sources.json (shared with the
# OpenCode dotfiles-bridge); omp/extensions/dotfiles-skills.ts feeds them to
# OMP via resources_discover at session start and /reload-plugins. The old
# flat symlink farm (scripts/omp-sync-skills -> ~/.omp/agent/skills) is
# retired: live sessions snapshotted farm paths, so every re-sync prune broke
# them mid-flight.
# Convergence: prune leftover farm symlinks on machines that predate the
# retirement (real user-authored skill dirs in ~/.omp/agent/skills survive).
[ -d "${HOME}/.omp/agent/skills" ] && find "${HOME}/.omp/agent/skills" -maxdepth 1 -type l -delete


# Prompt templates (shared command sources synced as symlinks).
"${DOTFILES_DIR}/scripts/omp-sync-prompts" || echo "omp: prompt sync reported issues" >&2
if [ -d "${DOTFILES_DIR}/omp/prompts" ]; then
    for f in "${DOTFILES_DIR}"/omp/prompts/*; do
        ensure_link "$f" "${HOME}/.omp/agent/prompts/$(basename "$f")"
    done
fi
