#!/bin/bash
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# --- agent-secrets: the host helper of the secrets broker (sjawhar/legion, AGENTC-393) -------
# Mirrors installers/secretsd.sh: mise owns the binaries (mise.toml [tools.agent-secrets]; the
# release tarball ships bin/agent-secrets and bin/agent-secrets-helper), the user unit is
# linked and enabled, and a restart happens only when the pinned version or the unit changed —
# and never while host sessions are registered unless the operator says so, because the
# helper's session keys live in memory: a restart re-pins every live session with a fresh key
# and revokes its old enrollment, so every grant those sessions hold is gone (they re-request).
HELPER_MISE_DIR="$(mise where agent-secrets 2>/dev/null || true)"
HELPER_BIN="${HELPER_MISE_DIR:+${HELPER_MISE_DIR}/bin/agent-secrets-helper}"
UNIT_SRC="${DOTFILES_DIR}/agent-secrets/agent-secrets-helper.service"
UNIT_DST="${HOME}/.config/systemd/user/agent-secrets-helper.service"
CONF_DIR="${HOME}/.config/agent-secrets"
# What the running helper was last (re)started on: "<version> <sha256 of the unit>".
MARKER="${CONF_DIR}/helper.installed"

if [ ! -x "$HELPER_BIN" ]; then
    echo "WARNING: ${HELPER_BIN} is absent; run 'mise install agent-secrets' to fetch the pinned release."
    echo "  Skipping agent-secrets-helper wiring until then."
else
    version="$(basename "$HELPER_MISE_DIR")"
    installed="${version} $(sha256sum "$UNIT_SRC" | cut -d' ' -f1)"
    mkdir -p "${HOME}/.config/systemd/user" "$CONF_DIR"
    chmod 700 "$CONF_DIR"
    ensure_link "$UNIT_SRC" "$UNIT_DST"
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now agent-secrets-helper.service 2>/dev/null \
        || echo "NOTE: could not enable agent-secrets-helper (no user systemd session here?) — enable it on the target machine."
    if [ "$(cat "$MARKER" 2>/dev/null)" != "$installed" ]; then
        # Only when the binary version or the unit changed. `sessions` is answered by the
        # running helper (the old version); an empty answer, or no helper, means nothing to lose.
        sessions="$(timeout 10 "$HELPER_BIN" sessions 2>/dev/null || true)"
        if [ -n "$sessions" ] && [ "${AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS:-}" != 1 ]; then
            n="$(printf '%s\n' "$sessions" | wc -l)"
            echo "agent-secrets-helper: NOT restarting -- the new version is installed but ${n} host session(s) are registered, and a restart re-pins them with fresh keys: every grant they hold is revoked and they must re-request:" >&2
            printf '%s\n' "$sessions" | while IFS= read -r line; do printf '    %s\n' "$line"; done >&2
            echo "agent-secrets-helper: wait for them to end, or re-run with AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS=1." >&2
            exit 1
        fi
        systemctl --user try-restart agent-secrets-helper.service 2>/dev/null || true
        echo "$installed" > "$MARKER"
    fi
    if [ ! -s "${CONF_DIR}/launcher.token" ] || [ ! -s "${CONF_DIR}/operator" ]; then
        echo "agent-secrets: no launcher credential on this host yet — run: agent-secrets-login <github-login>"
    fi
fi
