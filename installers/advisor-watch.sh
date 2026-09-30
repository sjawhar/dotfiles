#!/bin/bash
# Arm the daily AskGate readout: omp/advisor-watch.{service,timer} run
# `scripts/advisor-report readout --check gate --check trial --notify notifications.role.agentc-1305`
# at 06:00 UTC. The readout applies every KILL it computes itself and messages the Envoy role
# agentc-1305; docs/advisor-report.md has the rules.
#
# Standalone installer (like omp-billing-watch.sh) -- not sourced by install.sh. Run it on the
# host whose ~/.omp holds the sessions and stats.db the readout reads. Until
# `scripts/advisor-report arm gate` records a launch, the gate check is a no-finding.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Must run on the host, not inside an agentbox: a box's ~/.config/systemd is the container's own,
# so `systemctl enable` there writes a unit the host manager never loads (omp-billing-watch.sh).
if [[ -f /.dockerenv ]] || [[ "$(systemd-detect-virt 2>/dev/null)" == docker ]]; then
    echo "advisor-watch: this is an agentbox; the host systemd cannot see its ~/.config/systemd." >&2
    echo "  Run installers/advisor-watch.sh from a host shell instead." >&2
    exit 1
fi

# The unit runs the shared checkout's scripts (%h/.dotfiles), whichever checkout this runs from,
# under the user manager's PATH, not this shell's.
report="$HOME/.dotfiles/scripts/advisor-report"
envoy="$HOME/.dotfiles/scripts/envoy"
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
# The readout publishes with `envoy send --source envoy`. An envoy from before `--source` would
# publish that to the topic "--source"; asked `send --source` alone, one that has the flag refuses
# it by name and one that does not prints its usage, and neither sends. Captured before the grep:
# under pipefail, `grep -q` quitting early can SIGPIPE the writer and fail a probe that passed.
probe="$("$envoy" send --source 2>&1)" || true
grep -q -- '--source accepts only envoy' <<<"$probe" || {
    echo "advisor-watch: $envoy has no 'send --source envoy' (it answered: $probe); advance ~/.dotfiles" >&2
    exit 1
}

mkdir -p ~/.config/systemd/user
ensure_link "${DOTFILES_DIR}/omp/advisor-watch.service" ~/.config/systemd/user/advisor-watch.service
ensure_link "${DOTFILES_DIR}/omp/advisor-watch.timer" ~/.config/systemd/user/advisor-watch.timer

systemctl --user daemon-reload
systemctl --user enable --now advisor-watch.timer

# Prove the timer is scheduled: `enable --now` on a timer whose unit fails to load exits 0 on some
# systemd versions. Captured before the grep, for the same SIGPIPE reason as the probe above.
listed="$(systemctl --user list-timers --all advisor-watch.timer)"
grep -q advisor-watch <<<"$listed" || {
    echo "advisor-watch: timer did not register; see systemctl --user status advisor-watch.timer" >&2
    exit 1
}
echo "advisor-watch: armed ($(systemctl --user show -p NextElapseUSecRealtime --value advisor-watch.timer))"
