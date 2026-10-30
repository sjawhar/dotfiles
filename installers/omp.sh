#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# omp (oh-my-pi) - the binary is mise-managed ("github:sjawhar/oh-my-pi" fork
# release in mise.toml, built by the fork's fork-release.yml workflow); the
# shim at shims/omp sets up the session environment. This installer wires config.

OMP_AGENT_DIR="${HOME}/.omp/agent"
mkdir -p "$OMP_AGENT_DIR"

# Agents and the other config files are canonical in dotfiles and linked.
# config.yml is not:
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
# and shims/omp never mirrors models.yml into a named profile (a client profile
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
ensure_link "${DOTFILES_DIR}/omp/keybindings.yml" "${OMP_AGENT_DIR}/keybindings.yml"
# omp loads <agent dir>/hooks/<pre|post>/*.ts; post/jj-snapshot.ts is the one jj snapshotter.
ensure_link "${DOTFILES_DIR}/omp/hooks"       "${OMP_AGENT_DIR}/hooks"

# Extensions: dotfiles-owned sources are linked here; everything else is an OMP
# plugin installed from GitHub. The pins live in the committed
# omp/plugins/package.json; bun install materializes them. Manage pins with omp plugin
# install/upgrade - the resulting package.json/lockfile changes get committed here.
# jj snapshotting is omp/hooks/post/jj-snapshot.ts alone (see omp/AGENTS.md);
# a stale extension copy is pruned so no second, awaited snapshotter loads.
if [ -L "${OMP_AGENT_DIR}/extensions" ]; then rm "${OMP_AGENT_DIR}/extensions"; fi
mkdir -p "${OMP_AGENT_DIR}/extensions"
rm -f "${OMP_AGENT_DIR}/extensions/jj-snapshot.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/dotfiles-skills.ts" "${OMP_AGENT_DIR}/extensions/dotfiles-skills.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/session-env.ts" "${OMP_AGENT_DIR}/extensions/session-env.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/session-project.ts" "${OMP_AGENT_DIR}/extensions/session-project.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/compaction-reminder.ts" "${OMP_AGENT_DIR}/extensions/compaction-reminder.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/viewport.ts" "${OMP_AGENT_DIR}/extensions/viewport.ts"
# Machines set up before the rename hold a now-dangling link under the old name.
rm -f "${OMP_AGENT_DIR}/extensions/fullscreen.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/attention-queue.ts" "${OMP_AGENT_DIR}/extensions/attention-queue.ts"
# AskGate moved into @sjawhar/pi-envoy; machines set up before that hold a dangling link to it.
rm -f "${OMP_AGENT_DIR}/extensions/askgate.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/refusal-cooldown.ts" "${OMP_AGENT_DIR}/extensions/refusal-cooldown.ts"
# served-model-notice was removed on 2026-10-29 (Sami: "just delete this notice entirely");
# machines set up before that hold a dangling link to it.
rm -f "${OMP_AGENT_DIR}/extensions/served-model-notice.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/running-script-guard.ts" "${OMP_AGENT_DIR}/extensions/running-script-guard.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/decision-review-gate.ts" "${OMP_AGENT_DIR}/extensions/decision-review-gate.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/path-guard.ts" "${OMP_AGENT_DIR}/extensions/path-guard.ts"
ensure_link "${DOTFILES_DIR}/omp/extensions/span-account.ts" "${OMP_AGENT_DIR}/extensions/span-account.ts"
# The experiments extension: four session features, each behind its own gate (omp/AGENTS.md).
# Only the entry is linked; its sibling modules resolve from the link's real path.
ensure_link "${DOTFILES_DIR}/omp/extensions/experiments/index.ts" "${OMP_AGENT_DIR}/extensions/experiments.ts"
ensure_link "${DOTFILES_DIR}/omp/plugins" "${HOME}/.omp/plugins"
(cd "${DOTFILES_DIR}/omp/plugins" && bun install) || echo "omp: plugin install failed; re-run after fixing git auth" >&2
# The envoy and Legion extensions install from npm (@sjawhar/pi-envoy, @sjawhar/pi-legion). The old
# git-pinned legion monorepo entry is gone from package.json, but bun install
# does not prune its leftover directory — and OMP discovers extensions by
# walking node_modules for omp.extensions, so a leftover copy double-loads the
# extension and delivers every envoy message twice.
rm -rf "${DOTFILES_DIR}/omp/plugins/node_modules/legion"
# Marketplace plugins install at user scope, at the version the committed omp-plugins.lock.json
# records. The install record (installed_plugins.json) is machine-local; the lock entry is not, and
# omp refuses to install a plugin whose name the lock already holds ("Runtime package name ...
# conflicts") while exiting 0. So on a machine without the install, the stale lock entry is dropped
# first; the install writes it back at the end of the map, and when that is the only difference the
# committed bytes are put back so the checkout stays clean. An install behind the lock (another
# machine upgraded and committed the lock) refreshes the marketplace catalog, which omp otherwise
# keeps from the day it was added, and upgrades. An install that ends away from the lock is reported.
marketplace_plugin_version() {
    jq -r --arg p "$1" '.plugins[$p][0].version // empty' "${DOTFILES_DIR}/omp/plugins/installed_plugins.json" 2>/dev/null || true
}
install_marketplace_plugin() {
    local marketplace="$1" plugin="$2" lock="${DOTFILES_DIR}/omp/plugins/omp-plugins.lock.json" want have
    omp plugin marketplace list | grep -q "$marketplace" || omp plugin marketplace add "$marketplace"
    want=$(jq -r --arg n "${plugin%@*}" '.plugins[$n].version // empty' "$lock")
    have=$(marketplace_plugin_version "$plugin")
    if [ -z "$have" ]; then
        cp "$lock" "${lock}.saved"
        jq --arg name "${plugin%@*}" 'del(.plugins[$name])' "${lock}.saved" > "$lock"
        omp plugin install "$plugin"
        if cmp -s <(jq -S . "$lock") <(jq -S . "${lock}.saved"); then
            mv "${lock}.saved" "$lock"
        else
            rm "${lock}.saved"
        fi
    elif [ -n "$want" ] && [ "$have" != "$want" ]; then
        omp plugin marketplace update "${plugin#*@}"
        omp plugin upgrade "$plugin"
    fi
    have=$(marketplace_plugin_version "$plugin")
    if [ -z "$have" ]; then
        echo "omp: installing ${plugin} failed" >&2
    elif [ -n "$want" ] && [ "$have" != "$want" ]; then
        echo "omp: ${plugin} is ${have}, but omp-plugins.lock.json records ${want}" >&2
    fi
}
# theorem-labs/verification-skills: Theorem's verification skills, which repos that copied them dropped.
install_marketplace_plugin theorem-labs/verification-skills verification-skills@verification-skills
# slack@claude-plugins-official brings the Slack skills (slack-messaging, slack-search, ...) written
# for the tools of the slack server in omp/mcp.json. Claude Code installs it from .claude/settings.json,
# but omp reads Claude's plugin registry only when enabledProviders lists claude-plugins, so omp holds
# its own install.
install_marketplace_plugin anthropics/claude-plugins-official slack@claude-plugins-official

# Skills: pools are declared once in skills-sources.json;
# omp/extensions/dotfiles-skills.ts feeds them to
# OMP via resources_discover at session start and /reload-plugins. The old
# flat symlink farm (scripts/omp-sync-skills -> ~/.omp/agent/skills) is
# retired: live sessions snapshotted farm paths, so every re-sync prune broke
# them mid-flight.
# Convergence: prune leftover farm symlinks on machines that predate the
# retirement (real user-authored skill dirs in ~/.omp/agent/skills survive).
[ -d "${HOME}/.omp/agent/skills" ] && find "${HOME}/.omp/agent/skills" -maxdepth 1 -type l -delete

# Vendor checkouts behind the skills-sources.json pools.
# Our fork of compound-engineering: `sami` branch = the compound-engineering-v*
# release tag plus fork-local patches (ce-simplify-code reviews on top-tier
# GPT agents, not the Sonnet class; ce-babysit-pr's pr-snapshot reads a PR
# stack its token cannot see as no stack). Pinned to a commit on that branch
# (currently 8a776c13, compound-engineering-v3.32.0 + those patches, adopted
# per dispatch://INFRA-245). Bump: rebase `sami` onto the new tag in the vendor
# checkout, run its `bun run test`, push, re-pin here; check every
# omp/config.yml skills.ignoredSkills name still exists upstream, and diff the
# skill set for new arrivals.
ensure_vendor https://github.com/sjawhar/compound-engineering-plugin.git compound-engineering 8a776c13a08eea0d6485c26c9bc96ea84d5a6cc6
if [ -d "${DOTFILES_DIR}/vendor/compound-engineering/.git" ]; then
    git -C "${DOTFILES_DIR}/vendor/compound-engineering" remote get-url upstream &>/dev/null || \
        git -C "${DOTFILES_DIR}/vendor/compound-engineering" remote add upstream https://github.com/EveryInc/compound-engineering-plugin.git
fi
# Every's Compound Writing: 33 cw-* writing skills, entry skill cw-scribe.
# Pinned to upstream HEAD at adoption (8fd0ec88, 2026-10-30). Bump: re-pin
# here and diff the skill set for new arrivals and name collisions with the
# ce-* and plugins/sjawhar skills.
ensure_vendor https://github.com/EveryInc/compound-writing.git compound-writing 8fd0ec88c00976cf0274cb76552dc7ad9405ca92
ensure_vendor https://github.com/github/gh-stack.git gh-stack
ensure_vendor https://github.com/DataDog/pup.git pup
# pup ships ~11 dd-* skills (plus 50 agents, pruned from omp/agents); only
# dd-pup is wanted — the rest swamp session context. Converge fresh clones.
find "${DOTFILES_DIR}/vendor/pup/skills" -mindepth 1 -maxdepth 1 -type d -name 'dd-*' ! -name 'dd-pup' -exec rm -rf {} +
# Pinned: upstreams restructured after these commits (skills moved under
# packages/); skills-sources.json expects this layout.
ensure_vendor https://github.com/getsentry/sentry-for-ai.git sentry-for-ai 2c34b9a2ecff03005d381e60013d7d5849801a62
ensure_vendor https://github.com/getsentry/cli.git sentry-cli 33028c2ac93e027ce3faa9045efc91d895deae1a
# typesafe-ai/skills is a 2-commit, single-skill repo (skills/typesafe-ai/SKILL.md);
# pinned so reflect's jev-turn-label.py (plugins/sjawhar/skills/reflect/) keeps the
# API reference its design decisions cite, not whatever the docs say later. Bump:
# re-pin here and re-check jev-turn-label.py's criteria against the live docs.
ensure_vendor https://github.com/typesafe-ai/skills.git typesafe-ai 65a39f393687675ce170e6094757de20370365b9
ensure_vendor https://github.com/sjawhar/time-tracker.git time-tracker
# Pulumi's own agent skills (vendor skill for tool facts, dispatch://INFRA-26).
ensure_vendor https://github.com/pulumi/agent-skills.git pulumi-agent-skills 2517e28e4bc029424e9c9ee48016394296be0f6a

# GitHub CLI's own "gh" skill (reference for driving `gh`, dispatch://INFRA-26).
# `gh skill install` places it in ~/.agents/skills (user scope), which OMP's
# built-in "agents" discovery provider already reads natively (on by default) -
# no skills-sources.json entry needed. This block only materializes/pins it:
# pinned to the cli/cli v2.98.0 tag commit, matching mise.toml's
# `gh = "2.98.0"` pin (a newer skill documents flags, e.g. `--attach`, that an
# older installed `gh` rejects) - re-pin here whenever mise.toml's gh version
# moves. Non-fatal when `gh` is absent (agent boxes without it) or `gh skill`
# is unavailable.
GH_SKILL_PIN="a255baf71d13fe5947a4eb7ad521ffd412d64cee"
GH_SKILL_FILE="${HOME}/.agents/skills/gh/SKILL.md"
if command -v gh &>/dev/null; then
    gh_skill_present=0
    if gh skill list 2>/dev/null | awk -F'\t' '$1=="gh"{f=1} END{exit !f}'; then
        gh_skill_present=1
    fi
    gh_skill_pinned=""
    if [ -f "$GH_SKILL_FILE" ]; then
        gh_skill_pinned=$(grep -m1 '^\s*github-pinned:' "$GH_SKILL_FILE" | awk '{print $2}') || true
    fi
    if [ "$gh_skill_present" != "1" ] || [ "$gh_skill_pinned" != "$GH_SKILL_PIN" ]; then
        gh skill install cli/cli gh --agent universal --scope user --pin "$GH_SKILL_PIN" --force || true
    fi
fi

# agent-c skills required by instructions that apply outside agent-c, linked
# per-skill into the farm skills-sources.json names (pointing at agent-c's whole
# skills directory would publish ~100 skills). Linked only where agent-c is checked out.
AGENT_C_GLOBAL_SKILLS="${HOME}/src/agent-c/.claude/skills"
OMP_AGENT_C_FARM="${HOME}/.omp/skill-farms/agent-c"
for global_skill in maintaining-inspect; do
    if [ -d "${AGENT_C_GLOBAL_SKILLS}/${global_skill}" ]; then
        mkdir -p "${OMP_AGENT_C_FARM}"
        ensure_link "${AGENT_C_GLOBAL_SKILLS}/${global_skill}" "${OMP_AGENT_C_FARM}/${global_skill}"
    else
        [ -L "${OMP_AGENT_C_FARM}/${global_skill}" ] && rm "${OMP_AGENT_C_FARM}/${global_skill}"
    fi
done


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
