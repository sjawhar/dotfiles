#!/bin/bash
# Arm the one-minute omp provider-login watch (scripts/omp-login-watch).
#
# Standalone installer (like omp-memory-watch.sh) — not sourced by install.sh. Run it on a
# machine that runs omp sessions against the shared credential store: the watch reads THIS
# machine's ~/.omp/agent/agent.db, its /proc and its transcripts.
#
# Why this exists: on 2026-11-01T12:35:17Z the shared Anthropic credential was disabled by a
# failed OAuth refresh and every omp session on sami-rocqperf that used it stopped. Nothing
# told anybody for 3 h 45 min, until a person logged in again at 16:20:42Z (dispatch://INFRA-387).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

name=omp-login-watch
dropin="$HOME/.config/systemd/user/$name.service.d/dispatch-identity.conf"

command -v python3 >/dev/null || {
    echo "$name: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
command -v bun >/dev/null || {
    echo "$name: bun not on PATH; the dispatch CLI, this watch's only human path, runs under bun" >&2
    exit 1
}
[ -x "$HOME/.dotfiles/omp/plugins/node_modules/@sjawhar/pi-envoy/bin/dispatch" ] || {
    echo "$name: the dispatch CLI is missing; install the omp plugins first (omp/plugins)" >&2
    exit 1
}
[ -r "$HOME/.omp/agent/agent.db" ] || {
    echo "$name: no ~/.omp/agent/agent.db here; this machine has no shared credential store to watch" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "$name: no envoy.json; the watch has no listener to tell the box-health role through" >&2
    exit 1
}
mkdir -p /mnt/nvme/sami/tmp/omp-login-watch 2>/dev/null \
    || echo "NOTE: /mnt/nvme/sami/tmp is absent here; the watch keeps its state under ~/.local/state."

# The identity the Dispatch ask is opened as. The CLI refuses to act with no DISPATCH_SESSION_ID,
# and this watch is not a session: it gets one id of its own per machine, written once and kept,
# so every ask it opens comes from the same place and `dispatch open-asks` groups them. An
# existing id is never replaced — that would orphan the open asks it has to resolve.
if [ -f "$dropin" ]; then
    echo "$name: keeping $(grep -h DISPATCH_SESSION_ID "$dropin")"
else
    identity="$(python3 -c 'import uuid; print("01" + uuid.uuid4().hex[2:8] + "-" + uuid.uuid4().hex[:4] + "-7" + uuid.uuid4().hex[:3] + "-8" + uuid.uuid4().hex[:3] + "-" + uuid.uuid4().hex[:12])')"
    mkdir -p "$(dirname "$dropin")"
    printf '[Service]\nEnvironment=DISPATCH_SESSION_ID=%s\n' "$identity" > "$dropin"
    echo "$name: asks are opened as $identity (dispatch://INFRA-387)"
fi

# The OnFailure handler of the watch's unit. It is linked before the timer is armed, because
# systemd refuses nothing for a missing OnFailure target -- it would simply stay silent, which
# is the failure this handler exists to end.
ensure_link "$HOME/.dotfiles/omp/$name-failed.service" \
    "$HOME/.config/systemd/user/$name-failed.service" || exit 1

ensure_box_health_owner "$name" || exit 1
arm_user_timer omp "$name"
