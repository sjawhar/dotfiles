#!/bin/bash
set -euo pipefail

# --- agent-spend: model-spend records from every harness (theorem-labs/agent-infra spend/SETUP.md, INFRA-165) ---------
# Runs agent-infra's spend/client/agent-spend-setup at AGENT_SPEND_SETUP_REF, fetched from GitHub (the company's
# installer, not a copy of it here), in user mode: it reads the collector's token with this machine's gcloud, and makes
# omp, Claude Code and Codex send one record per model call, however each is started. The person is agent-spend/person;
# the project defaults to `shared` (scripts/agent-spend-env.sh tags each session with its own); AGENT_SPEND_LANE, when
# set (devbox/install.sh sets the host name), becomes theorem.lane. The fetched program and these arguments stay in
# ~/.cache/agent-spend, so scripts/agent-spend-env.sh can run the same setup again at a session's start when the token
# is missing (gcloud could not read it at install time). A rotation is a rerun of this file. The setup runs without
# CLAUDE_CONFIG_DIR: .bashrc points that at the tracked .claude/ here, which is shared by every machine and must hold
# no machine's path or token (the setup refuses a folder in a git work tree anyway); that settings.json carries the
# same telemetry keys by hand, and a helper command that runs $HOME/.config/agent-spend/otel-headers-helper, which the
# setup writes on each machine; where the setup never ran it exits 1, so Claude Code sends nothing. Sourced by
# install.sh, so it never exits.
AGENT_SPEND_SETUP_REF=88bd7e0de87842632c4691cc5ed4e3869cd9414c

agent_spend_setup() {
    local dotfiles="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
    local cache="${XDG_CACHE_HOME:-$HOME/.cache}/agent-spend" person_file setup args=()
    person_file="$dotfiles/agent-spend/person" setup="$cache/agent-spend-setup"
    if [[ ! -s "$person_file" ]]; then
        echo "NOTE: no $person_file; skipping agent-spend (sessions on this machine export no spend records)."
        return 0
    fi
    if ! command -v python3 > /dev/null 2>&1; then
        echo "NOTE: no python3 here; skipping agent-spend (sessions on this machine export no spend records)."
        return 0
    fi
    mkdir -p "$cache"
    if [[ ! -s "$setup" || "$(cat "$cache/ref" 2> /dev/null)" != "$AGENT_SPEND_SETUP_REF" ]]; then
        if ! gh api "repos/theorem-labs/agent-infra/contents/spend/client/agent-spend-setup?ref=$AGENT_SPEND_SETUP_REF" \
            -H 'Accept: application/vnd.github.raw' > "$setup.new" 2> /dev/null || [[ ! -s "$setup.new" ]]; then
            rm -f "$setup.new"
            echo "NOTE: could not fetch agent-infra's agent-spend-setup at $AGENT_SPEND_SETUP_REF (gh); skipping agent-spend."
            return 0
        fi
        mv "$setup.new" "$setup"
        printf '%s\n' "$AGENT_SPEND_SETUP_REF" > "$cache/ref"
    fi
    args=(--person "$(< "$person_file")")
    [[ -s "${HOME}/.config/agent-spend/default-project" ]] || args+=(--project shared)
    [[ -z "${AGENT_SPEND_LANE:-}" ]] || args+=(--lane "$AGENT_SPEND_LANE")
    printf '%s\n' "${args[@]}" > "$cache/args"
    if ! env -u CLAUDE_CONFIG_DIR python3 "$setup" "${args[@]}" < /dev/null; then
        echo "NOTE: agent-spend-setup did not finish (above); sessions here retry it when they start."
    fi
}
agent_spend_setup
