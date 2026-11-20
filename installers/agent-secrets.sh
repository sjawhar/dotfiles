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
# so. A restart can cost a live session its enrollment and its grants. At start, the new helper
# reads the session records that the running helper saved (by default
# ~/.config/agent-secrets/sessions.json):
#   - A live session that the running helper recorded with its key (a session registered with
#     --session-id) is pinned again with that key. It keeps its enrollment and its grants.
#   - A live session recorded with no key is pinned again with a fresh key. The new helper
#     revokes its old enrollment, so its grants are gone and it must request them again.
#   - A record with no boot id reads as a process that is gone, and the new helper does not pin
#     that session again. It revokes the record's enrollment when the record names no session
#     id. legion-envoy-v18.1.0 records no session id, no key and no boot id for any session, so a
#     restart from that version costs every live session its enrollment and its grants.
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
helper_restarted=0
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
        echo "WARNING: agent-secrets-helper NOT restarted -- the new version or unit is installed, but 'agent-secrets-helper sessions' did not answer (${why}), so it is not known whether host sessions are registered. A restart revokes the enrollment and the grants of each live session that the running helper recorded without its key. The next install retries." >&2
        echo "  To restart anyway: systemctl --user restart agent-secrets-helper.service" >&2
    elif [ -n "$sessions" ] && [ "${AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS:-}" != 1 ]; then
        n="$(printf '%s\n' "$sessions" | wc -l)"
        echo "WARNING: agent-secrets-helper NOT restarted -- the new version or unit is installed, but a restart keeps the enrollment and the grants only of a session that the running helper recorded with its key (a session registered with --session-id), and revokes those of every other session. ${n} host session(s) are registered:" >&2
        printf '%s\n' "$sessions" | while IFS= read -r line; do printf '    %s\n' "$line"; done >&2
        echo "  Wait for them to end, or re-run with AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS=1." >&2
    elif systemctl --user try-restart agent-secrets-helper.service 2>/dev/null; then
        echo "$installed" > "$MARKER"
        helper_restarted=1
    else
        echo "WARNING: agent-secrets-helper did not restart (systemctl --user status agent-secrets-helper); the next install retries." >&2
    fi
fi
# login-status asks the helper. `enable --now` and `try-restart` return before the new helper's
# socket listens, and login-status asked before then fails for that reason alone; so after this
# run started or restarted the helper, wait for it to answer `sessions` first, up to
# AGENT_SECRETS_HELPER_SOCKET_WAIT seconds (default 10).
if [ "$helper_started" -eq 1 ] || [ "$helper_restarted" -eq 1 ]; then
    if [ "$helper_started" -eq 1 ]; then how=start; else how=restart; fi
    wait_s="${AGENT_SECRETS_HELPER_SOCKET_WAIT:-10}"
    if [[ ! "$wait_s" =~ ^([0-9]+([.][0-9]+)?|[.][0-9]+)$ ]] || [[ ! "$wait_s" =~ [1-9] ]]; then
        echo "agent-secrets: AGENT_SECRETS_HELPER_SOCKET_WAIT must be a positive number of seconds; using 10 s" >&2
        wait_s=10
    fi
    # shellcheck disable=SC2016 # The child shell expands this command string.
    if ! timeout "$wait_s" bash -c 'until timeout 2 "$0" sessions >/dev/null 2>&1; do sleep 0.2; done' "$HELPER_BIN"; then
        echo "agent-secrets: the helper did not answer on its socket within ${wait_s} s after its ${how} (systemctl --user status agent-secrets-helper); its login state was not read"
        exit 0
    fi
fi
# login-status prints its state word (none, pending, denied, expired, issued) on stdout and why on
# stderr, and exits 0 only for issued; a client that cannot ask the helper prints only an error on
# stderr. The state is read from stdout alone: stderr can also carry mise's auto-install progress
# (scripts/agent-secrets-session). A failed read is printed as it came, state first. `none` gets
# one more line. On a helper whose env.local (its second EnvironmentFile) names a machine identity,
# `none` means its first trade of that identity has not succeeded: the helper listens after 5 s
# without a credential when the broker is slow. Elsewhere `none` means no machine login has run,
# and agent-secrets-login names the operator for the first one.
errfile="$(mktemp)" || errfile=""
if state="$(agent-secrets machine login-status 2>"${errfile:-/dev/null}")"; then rc=0; else rc=$?; fi
why=""
if [ -n "$errfile" ]; then why="$(<"$errfile")"; rm -f "$errfile"; fi
if [ "$rc" -ne 0 ]; then
    if [ -n "$state" ] && [ -n "$why" ]; then out="$(printf '%s\n%s' "$state" "$why")"; else out="${state}${why}"; fi
    echo "agent-secrets: machine login-status exited ${rc}: ${out}"
    if [ "$state" = none ]; then
        # Like agent-secrets-local.sh, source env.local in a child shell, without an inherited
        # identity. Only its final value selects the hint; no other settings reach this shell.
        # shellcheck disable=SC2016 # The child shell expands this command string.
        if env -u AGENT_SECRETS_MACHINE_IDENTITY bash -c '. "$1" >/dev/null 2>&1; [ -n "${AGENT_SECRETS_MACHINE_IDENTITY-}" ]' agent-secrets-identity "${CONF_DIR}/env.local"; then
            echo "  the helper proves this machine's identity (AGENT_SECRETS_MACHINE_IDENTITY in ~/.config/agent-secrets/env.local) and holds no launcher credential from it yet: journalctl --user -u agent-secrets-helper"
        else
            echo "  run: agent-secrets-login <email>"
        fi
    fi
fi
