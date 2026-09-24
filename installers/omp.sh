#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# omp (oh-my-pi) - the binary is mise-managed ("github:sjawhar/oh-my-pi" fork
# release in mise.toml, built by the fork's fork-release.yml workflow); the
# shim at shims/omp injects secrets and envoy env. This installer wires config.

OMP_AGENT_DIR="${HOME}/.omp/agent"
mkdir -p "$OMP_AGENT_DIR"

# Agents and the other config files are canonical in dotfiles and linked (same
# layout idea as ~/.config/opencode -> dotfiles/opencode). config.yml is not:
# shims/omp loads the committed file as a read-only PI_CONFIG_FILES overlay,
# and ~/.omp/agent/config.yml is the machine's own real file, where omp's
# runtime writes (/model picks, prompt answers) land. Converging the earlier
# layout (that path a link into the repo) copies the content in place: a
# running session re-reads this file before every subagent spawn, and one
# started under the link has no overlay to fall back on, so an empty file
# would strip its roles mid-session. The overlay wins on the duplicated keys.
# A fresh machine gets an empty mapping, so omp does not run its
# settings.json/agent.db migration into the file.
if [ -L "${OMP_AGENT_DIR}/config.yml" ]; then
    cp --remove-destination "$(readlink -f "${OMP_AGENT_DIR}/config.yml")" "${OMP_AGENT_DIR}/config.yml"
fi
[ -e "${OMP_AGENT_DIR}/config.yml" ] || echo '{}' > "${OMP_AGENT_DIR}/config.yml"
# models.yml is universal catalog patches; a machine's own provider routing
# (gateway baseUrl, `!command` apiKey) goes in the gitignored
# omp/models.local.yml and is merged over it here, straight into the file omp
# reads. A real file, not a link: no committed file holds the merged content,
# and scripts/ompo never mirrors models.yml into profiles (a client profile
# must not inherit the default's gateway routing).
MODELS_OUT="${OMP_AGENT_DIR}/models.yml"
# Converge the earlier layout: a link to a built copy inside the repo.
[ -L "$MODELS_OUT" ] && rm "$MODELS_OUT"
rm -f "${DOTFILES_DIR}/omp/models.generated.yml"
if [ -f "${DOTFILES_DIR}/omp/models.local.yml" ]; then
    yq eval-all '. as $item ireduce ({}; . * $item)' \
        "${DOTFILES_DIR}/omp/models.yml" "${DOTFILES_DIR}/omp/models.local.yml" > "${MODELS_OUT}.new"
else
    cp "${DOTFILES_DIR}/omp/models.yml" "${MODELS_OUT}.new"
fi
if cmp -s "${MODELS_OUT}.new" "$MODELS_OUT" 2>/dev/null; then
    rm -f "${MODELS_OUT}.new"
else
    mv "${MODELS_OUT}.new" "$MODELS_OUT"
fi
ensure_link "${DOTFILES_DIR}/omp/mcp.json"    "${OMP_AGENT_DIR}/mcp.json"
ensure_link "${DOTFILES_DIR}/omp/lsp.json"    "${OMP_AGENT_DIR}/lsp.json"
ensure_link "${DOTFILES_DIR}/omp/WATCHDOG.md" "${OMP_AGENT_DIR}/WATCHDOG.md"
ensure_link "${DOTFILES_DIR}/omp/WATCHDOG.yml" "${OMP_AGENT_DIR}/WATCHDOG.yml"
ensure_link "${DOTFILES_DIR}/omp/agents"      "${OMP_AGENT_DIR}/agents"

# Extensions: dotfiles-owned sources are linked here; everything else is an OMP
# plugin installed from GitHub. The pins live in the committed
# omp/plugins/package.json (same idea as opencode.json's git-pinned plugin
# entries); bun install materializes them. Manage pins with omp plugin
# install/upgrade - the resulting package.json/lockfile changes get committed here.
# jj snapshotting is omp/hooks/post/jj-snapshot.ts alone (see omp/AGENTS.md);
# a stale extension copy is pruned so no second, awaited snapshotter loads.
if [ -L "${OMP_AGENT_DIR}/extensions" ]; then rm "${OMP_AGENT_DIR}/extensions"; fi
mkdir -p "${OMP_AGENT_DIR}/extensions"
rm -f "${OMP_AGENT_DIR}/extensions/jj-snapshot.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/dotfiles-skills.ts" "${OMP_AGENT_DIR}/extensions/dotfiles-skills.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/session-env.ts" "${OMP_AGENT_DIR}/extensions/session-env.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/compaction-reminder.ts" "${OMP_AGENT_DIR}/extensions/compaction-reminder.ts"
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


# Prompt templates (slash commands). OMP loads them from exactly two hardcoded
# dirs (prompt-templates.ts loadPromptTemplates: agentDir/prompts and
# cwd/.omp/prompts) with no settings key and no plugin hook, and Bun.Glob does
# not descend into symlinked subdirs — so the files must physically sit in one
# directory. Linking the whole dir to the single command source means a new
# command is live immediately, with no sync step to forget. The retired
# scripts/omp-sync-prompts farmed per-file links from three namespaces; legion
# is headless orchestration where a user-only command is incoherent, and the
# sentry commands were unwanted, so one source is all there is.
# Converge machines still carrying the farm: drop its symlinks, then rmdir —
# which fails harmlessly if a real user-authored prompt file is in there.
if [ -d "${OMP_AGENT_DIR}/prompts" ] && [ ! -L "${OMP_AGENT_DIR}/prompts" ]; then
    find "${OMP_AGENT_DIR}/prompts" -maxdepth 1 -type l -delete
    rmdir "${OMP_AGENT_DIR}/prompts" 2> /dev/null \
        || echo "omp: ${OMP_AGENT_DIR}/prompts still has real files; move them and re-run to adopt the symlink" >&2
fi
ensure_link "${DOTFILES_DIR}/plugins/sjawhar/commands" "${OMP_AGENT_DIR}/prompts"
