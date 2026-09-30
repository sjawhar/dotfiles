#!/bin/bash
# Arm the daily AskGate readout: omp/advisor-watch.{service,timer} run
# `scripts/advisor-report readout --check gate --check trial --notify notifications.role.agentc-1305`
# at 06:00 UTC. The readout applies every KILL it computes itself and messages the Envoy role
# agentc-1305 with `scripts/envoy notify`; docs/advisor-report.md has the rules. An envoy from
# before `notify` answers it as an unknown command and sends nothing, so that run fails loudly
# and this installer needs no probe for it.
#
# Standalone installer (like omp-billing-watch.sh) -- not sourced by install.sh. Run it on the
# host whose ~/.omp holds the sessions and stats.db the readout reads. Until
# `scripts/advisor-report arm gate` records a launch, the gate check is a no-finding.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The unit runs the shared checkout (%h/.dotfiles) under the user manager's PATH, not this shell's,
# so every check binds to that checkout whichever checkout this runs from; arm_user_timer links the
# units from it too.
shared="$HOME/.dotfiles"
report="$shared/scripts/advisor-report"
[[ -x "$report" ]] || {
    echo "advisor-watch: $report is missing; advance ~/.dotfiles to a main that has it" >&2
    exit 1
}
unit_env="$(systemctl --user show-environment)"
unit_path="$(sed -n 's/^PATH=//p' <<<"$unit_env")"
for tool in python3 curl jq; do
    PATH="$unit_path" command -v "$tool" >/dev/null || {
        echo "advisor-watch: $tool is not on the user manager's PATH ($unit_path); the readout needs it" >&2
        exit 1
    }
done
PATH="$unit_path" python3 -c 'import yaml' || {
    echo "advisor-watch: the user manager's python3 has no PyYAML; scripts/advisor-report needs it" >&2
    exit 1
}

arm_user_timer omp advisor-watch
