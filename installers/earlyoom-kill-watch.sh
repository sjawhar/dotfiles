#!/bin/bash
# Start the earlyoom kill watch (scripts/earlyoom-kill-watch) as a user service.
#
# Standalone installer (like omp-root-hang-watch.sh) — not sourced by install.sh. Run it on a
# machine whose earlyoom kills agent sessions' processes: the watch reads THIS machine's
# earlyoom journal and /proc.
#
# Why this exists: on 2026-11-02 earlyoom sent SIGTERM 51 times on sami-rocqperf by 15:23Z.
# Each killed tool exited 143 with no message, and no session was told why.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

name=earlyoom-kill-watch
unit="$HOME/.dotfiles/omp/$name.service"
link="$HOME/.config/systemd/user/$name.service"

if [[ -f /.dockerenv ]] || [[ "$(systemd-detect-virt 2>/dev/null)" == docker ]]; then
    echo "$name: this is an agentbox; the host systemd cannot see its ~/.config/systemd. Run $0 from a host shell." >&2
    exit 1
fi
command -v python3 >/dev/null || { echo "$name: python3 not on PATH; the watch is a python3 script" >&2; exit 1; }
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "$name: no envoy.json; the watch has no listener to send its notices to" >&2
    exit 1
}
systemctl cat earlyoom.service >/dev/null 2>&1 || { echo "$name: no earlyoom.service on this machine; nothing to watch" >&2; exit 1; }
# journalctl exits 0 and prints nothing when this user may not read the system journal.
[[ -n "$(journalctl -u earlyoom -n 1 -o cat 2>/dev/null)" ]] || {
    echo "$name: $(id -un) cannot read earlyoom's journal; add it to the systemd-journal group" >&2
    exit 1
}
[[ -f "$unit" ]] || { echo "$name: $unit is missing; advance ~/.dotfiles to a main that has it" >&2; exit 1; }
# Respect a deliberate stop: a service someone disabled after it was started stays off.
if [[ -L "$link" ]] && ! systemctl --user is-enabled --quiet "$name.service" 2>/dev/null; then
    echo "$name: service is linked but disabled; leaving it off (enable it by hand to start it again)"
    exit 0
fi
mkdir -p /mnt/nvme/sami/tmp/earlyoom-kill-watch 2>/dev/null \
    || echo "NOTE: /mnt/nvme/sami/tmp is absent here; the state goes under \$XDG_STATE_HOME."

mkdir -p "$HOME/.config/systemd/user"
ensure_link "$unit" "$link"
systemctl --user daemon-reload
systemctl --user enable "$name.service"
systemctl --user restart "$name.service"
sleep 3
systemctl --user is-active --quiet "$name.service" || {
    echo "$name: service did not stay up; see journalctl --user -u $name" >&2
    exit 1
}
echo "$name: running ($(journalctl --user -u "$name" -n 1 -o cat 2>/dev/null))"
