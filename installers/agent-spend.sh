#!/bin/bash
set -euo pipefail

# --- agent-spend: the model-spend collector's token (theorem-labs/agent-infra spend/SETUP.md) ---------------------
# scripts/agent-session exports each omp and Claude Code session's per-call usage records once agent-spend/endpoint
# exists and ~/.config/agent-spend/token holds the collector's bearer token. The token is the Secret Manager secret
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
