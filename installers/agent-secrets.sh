#!/bin/bash
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# --- agent-secrets: the host helper of the secrets broker (sjawhar/legion, AGENTC-393) -------
# The root install.sh runs this through installers/agent-secrets-local.sh where
# ~/.config/agent-secrets/env.local sets AGENT_SECRETS_URL; devbox/install.sh runs it on the
# devbox. A machine without the helper's unit keeps secretsd alone: its shells get no
# AGENT_SECRETS_HELPER_SOCK (.bashrc) and its sessions are not registered
# (scripts/agent-secrets-session).
# Mirrors installers/secretsd.sh: mise owns the binaries (mise.toml [tools.agent-secrets]; the
# release tarball ships bin/agent-secrets and bin/agent-secrets-helper), the user unit is linked
# and enabled, and a restart happens only when the pinned version, the unit, its wrapper or its
# broker.env changed — and never while host sessions are registered unless the operator says
# so, because the helper's session keys live in memory: a restart re-pins every live session
# with a fresh key and revokes its old enrollment, so every grant those sessions hold is gone
# (they re-request).
HELPER_MISE_DIR="$(mise where agent-secrets 2>/dev/null || true)"
HELPER_BIN="${HELPER_MISE_DIR:+${HELPER_MISE_DIR}/bin/agent-secrets-helper}"
UNIT_SRC="${DOTFILES_DIR}/agent-secrets/agent-secrets-helper.service"
UNIT_DST="${HOME}/.config/systemd/user/agent-secrets-helper.service"
CONF_DIR="${HOME}/.config/agent-secrets"
# What the running helper was last (re)started on: "<version> <sha256 of unit, wrapper, env>".
MARKER="${CONF_DIR}/helper.installed"

if [ ! -x "$HELPER_BIN" ]; then
    echo "WARNING: ${HELPER_BIN:-agent-secrets-helper} is absent; run 'mise install agent-secrets' to fetch the pinned release."
    echo "  Skipping agent-secrets-helper wiring until then."
    exit 0
fi
version="$(basename "$HELPER_MISE_DIR")"
installed="${version} $(cat "$UNIT_SRC" "${DOTFILES_DIR}/agent-secrets/agent-secrets-helper" "${DOTFILES_DIR}/agent-secrets/broker.env" | sha256sum | cut -d' ' -f1)"
mkdir -p "${HOME}/.config/systemd/user" "$CONF_DIR"
chmod 700 "$CONF_DIR"
ensure_link "$UNIT_SRC" "$UNIT_DST"
systemctl --user daemon-reload 2>/dev/null || true
# An unchanged running helper needs no enable or start call; a stopped helper must be started.
# Only a run that found the helper inactive and then started it with `enable --now` counts as
# having started it: on an active but disabled helper, `enable --now` enables the unit and
# leaves the running process alone, so that process still runs the files it was started on.
helper_active=0
if systemctl --user is-active --quiet agent-secrets-helper.service 2>/dev/null; then
    helper_active=1
fi
helper_started=0
if [ "$helper_active" -eq 0 ] \
    || ! systemctl --user is-enabled --quiet agent-secrets-helper.service 2>/dev/null; then
    if ! systemctl --user enable --now agent-secrets-helper.service 2>/dev/null; then
        echo "NOTE: could not enable agent-secrets-helper (no user systemd session here?) — enable it on the target machine."
    elif [ "$helper_active" -eq 0 ]; then
        helper_started=1
    fi
fi
recorded="$(cat "$MARKER" 2>/dev/null || true)"
if [ "$recorded" != "$installed" ] && [ "$helper_started" -eq 1 ]; then
    # This run started the helper, so it already runs the installed version, unit, wrapper and
    # broker.env: record them with no `sessions` call and no restart. `enable --now` returns
    # before the new helper's socket listens, so `sessions` may not answer yet, and how fast it
    # answers does not matter: there is nothing to restart.
    echo "$installed" > "$MARKER"
elif [ "$recorded" != "$installed" ]; then
    # Only when the binary version or the helper's files changed and this run did not start the
    # helper. `sessions` is answered by the running helper (the old version). A restart needs
    # `sessions` to exit 0 with no output; a failed or timed-out answer is treated as
    # possibly-registered sessions, so the helper is not restarted, whatever
    # AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS says, and the old marker stays for the next
    # install to retry.
    if sessions="$(timeout 10 "$HELPER_BIN" sessions 2>/dev/null)"; then rc=0; else rc=$?; fi
    if [ "$rc" -ne 0 ]; then
        if [ "$rc" -eq 124 ]; then why="exit status 124: it timed out after 10 s"; else why="exit status ${rc}"; fi
        echo "WARNING: agent-secrets-helper NOT restarted -- the new version or unit is installed but 'agent-secrets-helper sessions' did not answer (${why}), so whether host sessions are registered is unknown, and a restart would revoke every grant they hold; the next install retries." >&2
        echo "  To restart anyway: systemctl --user restart agent-secrets-helper.service" >&2
    elif [ -n "$sessions" ] && [ "${AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS:-}" != 1 ]; then
        n="$(printf '%s\n' "$sessions" | wc -l)"
        echo "WARNING: agent-secrets-helper NOT restarted -- the new version or unit is installed but ${n} host session(s) are registered, and a restart re-pins them with fresh keys: every grant they hold is revoked and they must re-request:" >&2
        printf '%s\n' "$sessions" | while IFS= read -r line; do printf '    %s\n' "$line"; done >&2
        echo "  Wait for them to end, or re-run with AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS=1." >&2
    elif systemctl --user try-restart agent-secrets-helper.service 2>/dev/null; then
        echo "$installed" > "$MARKER"
    else
        echo "WARNING: agent-secrets-helper did not restart (systemctl --user status agent-secrets-helper); the next install retries." >&2
    fi
fi
if ! agent-secrets machine login-status >/dev/null 2>&1; then
    echo "agent-secrets: no live launcher credential on this host yet — run: agent-secrets-login <email>"
fi
