#!/bin/bash
# Arm the omp auth-broker on this machine (omp/auth-broker.service), and point
# this machine's omp sessions at it.
#
# Standalone installer (like hawk-token-refresh.sh) — not sourced by install.sh.
# Run it on a machine whose omp sessions sign in to a model provider with an
# OAuth login rather than an API key, and where more than a couple of sessions
# run at once.
#
# Why this exists: without a broker, every session refreshes the shared login
# itself. Anthropic's refresh tokens are single use, so one refresh that wins its
# exchange and loses its write leaves a spent token behind and the next refresh
# disables the login for every session on the machine. That is what emptied this
# box at 07:20Z on 2026-10-25 (dispatch://INFRA-387). With the broker, one process
# refreshes and the sessions only ask it for an access token.
#
# The client setting is machine-local on purpose: it names a port on this
# machine's loopback, so it goes in ~/.omp/agent/config.yml, never in the
# committed overlay that every machine reads. To undo it:
#   omp config reset auth.broker.url
#   systemctl --user disable --now omp-auth-broker.service
#
# The broker keeps its logins in a database of its own (~/.omp/auth-broker), signed in once with
# scripts/omp-broker-login, so it never waits on the lock of the agent.db the sessions write; the
# earlier unit, auth-broker.service on 18777, shared that file and stalled under load
# (dispatch://INFRA-387). Sessions are moved to it only once it holds a live Anthropic login.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BROKER_URL="http://127.0.0.1:18778"
BROKER_DB="$HOME/.omp/auth-broker/agent.db"

arm_user_service omp omp-auth-broker

# Wait for the listener before the client setting goes in, so a session started
# in between does not find a URL nothing answers.
for _ in {1..20}; do
    if OMP_AUTH_BROKER_URL="$BROKER_URL" omp auth-broker status >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

OMP_AUTH_BROKER_URL="$BROKER_URL" omp auth-broker status >/dev/null || {
    echo "auth-broker: serving, but $BROKER_URL does not answer; leaving sessions as they are" >&2
    exit 1
}

# A session pointed at a broker with no login reaches no model, so nothing moves until the broker
# holds one (non-secret columns only).
live="$(sqlite3 "$BROKER_DB" "SELECT count(*) FROM auth_credentials WHERE provider = 'anthropic' AND disabled_cause IS NULL" 2>/dev/null || echo 0)"
[[ "$live" -ge 1 ]] || {
    echo "auth-broker: the broker holds no live Anthropic login; run omp-broker-login, then this again; leaving sessions as they are" >&2
    exit 1
}

omp config set auth.broker.url "$BROKER_URL"
# Prove a client with no broker variables of its own reaches it: that is what a
# new session will do.
env -u OMP_AUTH_BROKER_URL -u OMP_AUTH_BROKER_TOKEN omp auth-broker status
echo "auth-broker: sessions started from now on get their model login from $BROKER_URL"
