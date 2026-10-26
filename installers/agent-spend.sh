#!/bin/bash
set -euo pipefail

# --- agent-spend: the model-spend collector's token (theorem-labs/agent-infra spend/SETUP.md) ---------------------
# scripts/agent-spend-env.sh (run by scripts/agent-session, shims/claude and shims/codex) exports each omp, Claude Code
# and Codex run's per-call usage records once agent-spend/endpoint exists and ~/.config/agent-spend/token holds the
# collector's bearer token. The token is the Secret Manager secret
# agent-spend-otel-token in theorem-agent-infra, readable by every theorem.dev account and by the box identity; it
# is fetched once and again only when absent, so a rotation is `rm ~/.config/agent-spend/token` and a rerun. A
# machine whose gcloud cannot read it gets no token and its sessions export nothing. Sourced by install.sh, so it
# never exits.
agent_spend_token() {
    local dir="${XDG_CONFIG_HOME:-$HOME/.config}/agent-spend" tmp
    [ ! -s "$dir/token" ] || return 0
    if ! command -v gcloud > /dev/null 2>&1; then
        echo "NOTE: no gcloud here; skipping the agent-spend token (sessions on this machine export no spend records)."
        return 0
    fi
    mkdir -p "$dir"
    chmod 700 "$dir"
    tmp="$(mktemp "$dir/token.XXXXXX")"
    # CLOUDSDK_CONFIG is honoured, so the box identity's config works where a person's login does not.
    if gcloud secrets versions access latest --secret=agent-spend-otel-token --project=theorem-agent-infra > "$tmp" 2> /dev/null \
        && [ -s "$tmp" ]; then
        chmod 600 "$tmp"
        mv "$tmp" "$dir/token"
        echo "agent-spend: token installed at $dir/token."
    else
        rm -f "$tmp"
        echo "NOTE: gcloud could not read secret agent-spend-otel-token in theorem-agent-infra; sessions here export no spend records."
    fi
}
agent_spend_token

# Codex CLI takes only OTEL_RESOURCE_ATTRIBUTES from the environment (shims/codex sets it through
# scripts/agent-spend-env.sh); its exporter must be in ~/.codex/config.toml with the token's text written out, since
# it expands no variable there (spend/SETUP.md step 2). This writes that block between marker comments, replacing
# what it wrote last time, so a rerun after a token rotation updates it. An [otel] section of the user's own is left
# alone, with a note. The token goes from its file into the config file, never onto a command line.
agent_spend_codex() {
    local token="${XDG_CONFIG_HOME:-$HOME/.config}/agent-spend/token" endpoint="${DOTFILES_DIR}/agent-spend/endpoint"
    local config="$HOME/.codex/config.toml" kept
    [ -s "$token" ] && [ -s "$endpoint" ] && command -v codex > /dev/null 2>&1 || return 0
    (umask 077 && mkdir -p "$HOME/.codex" && touch "$config")
    chmod 600 "$config"
    kept="$(awk '/^# agent-spend begin$/ { cut = 1 } !cut { print } /^# agent-spend end$/ { cut = 0 }' "$config")"
    if grep -q '^\[otel\]' <<< "$kept"; then
        echo "NOTE: $config has an [otel] section of its own; add the agent-spend exporter to it by hand (spend/SETUP.md step 2)."
        return 0
    fi
    (umask 077 && {
        [ -z "$kept" ] || printf '%s\n' "$kept"
        printf '# agent-spend begin\n[otel]\nexporter = { otlp-http = { endpoint = "%s/v1/logs", protocol = "binary", headers = { Authorization = "Bearer %s" } } }\n# agent-spend end\n' \
            "$(< "$endpoint")" "$(< "$token")"
    } > "$config.new") && mv "$config.new" "$config" && echo "agent-spend: Codex exporter written to $config."
}
agent_spend_codex
