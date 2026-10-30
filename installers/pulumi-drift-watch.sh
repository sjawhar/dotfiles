#!/bin/bash
# Arm the daily Pulumi drift report (scripts/pulumi-drift-watch).
#
# Standalone installer (like load-swap-watch.sh and omp-memory-watch.sh) — not sourced by
# install.sh. Run it only on a machine that holds the Google login the previews need, which
# today is sami-rocqperf: the watch previews theorem-labs/agent-infra's stacks against the
# live cloud, and a machine without that login would report a failure every day.
#
# Why this exists: on 2026-10-30 a Legion worker pod found, through nine failing assertions,
# that agent-infra 904aecc3 had merged at 00:34Z declaring an IAM role nobody applied. The
# same preview showed six more creates from a pull request merged seven hours earlier. Nothing
# measured the gap between what main declares and what is live.
# scripts/pulumi-drift-watch.md has the timeline and what the watch does and does not do.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v python3 >/dev/null || {
    echo "pulumi-drift-watch: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
command -v pulumi >/dev/null || {
    echo "pulumi-drift-watch: no pulumi on PATH; the watch runs pulumi preview" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "pulumi-drift-watch: no envoy.json; the watch has no listener to send its report through" >&2
    exit 1
}
if ! gcloud --configuration=theorem auth print-access-token >/dev/null 2>&1; then
    echo "pulumi-drift-watch: the theorem gcloud configuration mints no access token here." >&2
    echo "  Every preview would fail and report a failure, which is noise, not a measurement." >&2
    exit 1
fi

# A unit someone deliberately stopped stays stopped. An installer that silently re-arms a
# watch undoes a decision nobody recorded (scripts/AGENTS.md).
if systemctl --user list-unit-files pulumi-drift-watch.timer >/dev/null 2>&1 \
    && [ "$(systemctl --user is-enabled pulumi-drift-watch.timer 2>/dev/null)" = "disabled" ] \
    && [ "$(systemctl --user is-active pulumi-drift-watch.timer 2>/dev/null)" != "active" ]; then
    echo "pulumi-drift-watch: the timer is inactive and disabled; leaving it alone."
    echo "  Someone turned it off. Re-arm it deliberately:"
    echo "    systemctl --user enable --now pulumi-drift-watch.timer"
    exit 0
fi

arm_user_timer omp pulumi-drift-watch
