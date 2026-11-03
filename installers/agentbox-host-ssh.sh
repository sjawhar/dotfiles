#!/bin/bash
# The key agent boxes on this machine use to reach the host's own sshd, and the host's pinned key
# (devbox/install.sh runs this on a Google VM, sami-devbox, dispatch://INFRA-993).
#
# A box's connection to the host's own tailnet address reaches OpenSSH, not Tailscale SSH, so a
# box needs a key the host's authorized_keys accepts. This makes, for host <h> (`hostname -s`):
#   ~/.ssh/<h>, ~/.ssh/<h>.pub   an ed25519 key made here when absent; never copied from elsewhere
#   ~/.ssh/authorized_keys       one line for that key, accepted only from the agentbox Docker
#                                network's subnet (read from Docker, never hard-coded)
#   ~/.ssh/known_hosts.<h>       the host's ed25519 key under its short name, tailnet name and
#                                tailnet IPv4
# agentbox/mounts gives boxes the three files; agentbox/box-ssh-config writes their ssh entry.
#
# Google's guest agent owns the keys it writes into authorized_keys, each after a "# Added by
# Google" line. This adds its own line at the end, and rewrites the file only to replace a line of
# this key whose subnet is out of date. Safe to run again.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

NETWORK=agentbox # scripts/agentbox's NETWORK
host="$(hostname -s)"
ssh_dir="$HOME/.ssh"
key="$ssh_dir/$host"
auth="$ssh_dir/authorized_keys"
pinned="$ssh_dir/known_hosts.$host"

install -d -m 0700 "$ssh_dir"
if [[ ! -e "$key" ]]; then
    ssh-keygen -q -t ed25519 -N '' -C "agentbox boxes on $host -> $host sshd" -f "$key"
    echo "agentbox-host-ssh: made $key"
elif [[ ! -s "$key.pub" ]]; then
    ssh-keygen -y -f "$key" >"$key.pub"
fi
read -r key_type key_b64 _ <"$key.pub"
pub="$(head -1 "$key.pub")"

# The network scripts/agentbox makes, made the same way when no box has started yet.
docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
subnet="$(docker network inspect "$NETWORK" --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}')"
subnet="${subnet%% *}"
[[ "$subnet" =~ ^[0-9.]+/[0-9]+$ ]] || { echo "ERROR: agentbox-host-ssh: no IPv4 subnet on Docker network $NETWORK: '$subnet'" >&2; exit 1; }
want="from=\"$subnet\",no-agent-forwarding,no-X11-forwarding $pub"

[[ -e "$auth" ]] || install -m 0600 /dev/null "$auth"
if ! grep -qxF "$want" "$auth"; then
    if grep -qF " $key_type $key_b64" "$auth"; then
        tmp="$(mktemp "$ssh_dir/.authorized_keys.XXXXXX")"
        grep -vF " $key_type $key_b64" "$auth" >"$tmp" || true
        printf '%s\n' "$want" >>"$tmp"
        chmod 600 "$tmp"
        mv -f "$tmp" "$auth"
        echo "agentbox-host-ssh: $auth: rewrote $key's line (accepted from $subnet)"
    else
        [[ ! -s "$auth" || "$(tail -c 1 "$auth")" == "" ]] || echo >>"$auth"
        printf '%s\n' "$want" >>"$auth"
        echo "agentbox-host-ssh: $auth: added $key, accepted from $subnet"
    fi
fi

dns="$(tailscale status --self --json | jq -r '.Self.DNSName | rtrimstr(".")')"
ip4="$(tailscale ip -4 | head -1)"
[[ -n "$dns" && -n "$ip4" ]] || { echo "ERROR: agentbox-host-ssh: no tailnet name or IPv4 (tailscale up?)" >&2; exit 1; }
read -r host_type host_b64 _ </etc/ssh/ssh_host_ed25519_key.pub
pin="$host,$dns,$ip4 $host_type $host_b64"
if [[ "$(cat "$pinned" 2>/dev/null)" != "$pin" ]]; then
    printf '%s\n' "$pin" >"$pinned.new"
    chmod 644 "$pinned.new"
    mv -f "$pinned.new" "$pinned"
    echo "agentbox-host-ssh: wrote $pinned"
fi
