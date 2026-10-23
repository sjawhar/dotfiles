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

# A default cld session reads .claude/envoy.json and has no Dispatch or Envoy without one
# (envoy/AGENTS.md): link it to the neutral file this machine's Dispatch and Envoy setup wrote.
envoy_json="${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json"
if [ -f "$envoy_json" ]; then
    ensure_link "$envoy_json" "${DOTFILES_DIR}/.claude/envoy.json"
fi

# cld asks for the claude-envoy channel with --channels, which Claude Code loads only when managed
# settings turn channels on and allowlist the plugin (agent-infra docs/joining.md). Managed settings
# are machine-wide, so this merges into a file that is already there rather than replacing it.
case "$(uname -s)" in
    Darwin) managed="/Library/Application Support/ClaudeCode/managed-settings.json" ;;
    *) managed=/etc/claude-code/managed-settings.json ;;
esac
channel='{"marketplace": "legion-plugins", "plugin": "claude-envoy"}'
current='{}'
[ -f "$managed" ] && current=$(jq . "$managed")
if ! jq -e --argjson c "$channel" '.channelsEnabled == true and any((.allowedChannelPlugins // [])[]; . == $c)' \
    <<<"$current" >/dev/null; then
    sudo mkdir -p "$(dirname "$managed")"
    jq --argjson c "$channel" '.channelsEnabled = true | .allowedChannelPlugins = ((.allowedChannelPlugins // []) + [$c] | unique)' \
        <<<"$current" | sudo tee "$managed" >/dev/null
    sudo chmod 644 "$managed"
    echo "Wrote $managed: Claude Code channels on, claude-envoy@legion-plugins allowlisted."
fi

if [ -t 0 ]; then
    echo
    echo "If you haven't installed the sjawhar plugin in Claude Code yet, run:"
    echo "  /plugin install sjawhar@sjawhar"
    echo "from inside a Claude Code session. The sjawhar marketplace is already declared by"
    echo "${DOTFILES_DIR}/.claude-plugin/marketplace.json."
fi
