#!/bin/bash
# Arm the 5-minute off-box watch of sami-devbox (scripts/devbox-offbox-watch).
#
# Standalone installer (like omp-root-hang-watch.sh) — not sourced by install.sh. Run it on a
# machine that is NOT the one being watched; today that is sami-rocqperf.
#
# Why this exists: on 2026-11-04 sami-devbox stalled from about 16:40Z and needed a cloud reset at
# 18:05:53Z. Every session, every in-session loop and every watcher ON it died together, and
# nothing off the box noticed for an hour and a half (dispatch://INFRA-993/ask/d13b86a5). A watch
# of a machine has to run somewhere else, so this one refuses to arm on the machine it watches.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

name=devbox-offbox-watch
host="${DEVBOX_OFFBOX_HOST:-sami-devbox}"

command -v python3 >/dev/null || {
    echo "$name: python3 not on PATH; the watch is a python3 script" >&2
    exit 1
}
if [ "$(hostname -s)" = "$host" ]; then
    echo "$name: this IS $host. A watch of a machine has to run on another one." >&2
    echo "  Run this on sami-rocqperf, or set DEVBOX_OFFBOX_HOST to the machine to watch." >&2
    exit 1
fi
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "$name: no envoy.json; the watch has no listener to send its alerts through" >&2
    exit 1
}
# Everything it does on the watched machine goes over ssh, with no password and no prompt.
if ! ssh -o BatchMode=yes -o ConnectTimeout=20 "$host" true 2>/dev/null; then
    echo "$name: ssh -o BatchMode=yes $host does not work from here; fix that first" >&2
    exit 1
fi
# The alerts about a dead sami-devbox must not travel through sami-devbox. Envoy's hub is a
# tailnet node of its own; say which one, so a later move of the hub onto the devbox is noticed.
hub="$(python3 - <<'EOF'
import json, os, pathlib, urllib.parse
path = os.environ.get("ENVOY_CONFIG_FILE") or str(
    pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config") / "legion/envoy.json")
try:
    url = (json.loads(pathlib.Path(path).read_text()).get("envoy") or {}).get("url") or ""
except (OSError, ValueError):
    url = ""
print(urllib.parse.urlsplit(url).hostname or "")
EOF
)"
case "$hub" in
    "" | 127.0.0.1 | localhost) echo "NOTE: envoy.json names no hub of its own ($hub); an alert about $host may not get out while $host is down." ;;
    *"$host"*) echo "WARNING: Envoy's hub is $hub, which is the machine being watched. Its alerts will die with it." >&2 ;;
    *) echo "$name: Envoy's hub is $hub, which is not $host, so its alerts survive $host." ;;
esac

# Respect a deliberate stop: a timer someone disabled after it was armed stays off.
if [ -L "$HOME/.config/systemd/user/$name.timer" ] \
    && ! systemctl --user is-enabled --quiet "$name.timer" 2>/dev/null; then
    echo "$name: timer is linked but disabled; leaving it off (enable it by hand to re-arm)"
    exit 0
fi

ensure_box_health_owner "$name" || exit 1
arm_user_timer omp "$name"
