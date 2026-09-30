#!/bin/bash
# Arm the daily count of Legion READY packets carrying the human brief (scripts/legion-brief-watch).
#
# Standalone installer (like omp-billing-watch.sh) — not sourced by install.sh. Run it on the one
# host whose user manager should send the go-notice: it reads production Dispatch with this host's
# ~/.config/opencode/envoy.json and publishes through this host's Envoy.
#
# Why this exists: the brief on Legion pull requests rolls out warn-first. A body without it gets a
# `brief-missing:` finding and a READY with `Warning: brief-missing`, and a follow-up pull request
# turns that into a refusal once real traffic shows no such warning. This timer is the one that
# watches the traffic and tells the coordinator (Envoy role agentc-1305) when the window can close.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The unit ships with a marker in place of the brief's merge instant, and the watch refuses that, or
# any --release-time that is not RFC 3339, on every run. Arming such a unit would show `armed` over
# a timer that can only fail, so the ExecStart line's value (not a comment's) must match the
# watch's own pattern (scripts/legion-brief-watch, EPOCH). It is read from the unit arm_user_timer
# links, the one in ~/.dotfiles, whichever checkout this installer runs from.
unit="$HOME/.dotfiles/legion/legion-brief-watch.service"
if ! grep -Eqs -- '^ExecStart=.* --release-time [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})( |$)' \
    "$unit"; then
    echo "legion-brief-watch: $unit has no RFC 3339 --release-time on its ExecStart line; write the brief's merge instant there (e.g. 2026-10-01T12:00:00Z), or advance ~/.dotfiles to a main that has the unit" >&2
    exit 1
fi

# The unit runs with PATH=/usr/local/bin:/usr/bin:/bin; the watch needs curl and jq there.
for tool in curl jq; do
    PATH=/usr/local/bin:/usr/bin:/bin command -v "$tool" >/dev/null || {
        echo "legion-brief-watch: $tool is not on the unit's PATH (/usr/local/bin:/usr/bin:/bin)" >&2
        exit 1
    }
done

arm_user_timer legion legion-brief-watch
