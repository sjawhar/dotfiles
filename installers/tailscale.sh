#!/bin/bash
# Tailscale on a Google VM devbox (sami-devbox), joined as Sami's own node: `tailscale up` prints
# a login URL, and Sami signs in there with his own account, as on his other devices. The node
# carries no tag, so it reaches what his login reaches through the tailnet policy's members'
# grants (dispatch://INFRA-1099; agent-infra bootstrap/README.md, "sami-devbox", "The tailnet").
# No auth key and no client id is stored anywhere.
#
# Root-level role installer: devbox/gce-host.sh sources it with sudo. TAILSCALE_HOSTNAME defaults
# to this machine's short host name, the name the tailnet policy's hosts entries give sami-devbox.
#
# Idempotent: the package is installed only when the pinned version is not, and `tailscale up`
# runs only when the node is not already up, untagged, under that name. A node still tagged from
# an earlier join signs in again (--force-reauth) and drops its tags (--advertise-tags=).
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The .deb from Tailscale's own Ubuntu pool, pinned.
# The sha256 is the one Tailscale's signed apt index (dists/noble/main/binary-amd64/Packages)
# lists for this file, read on 2026-11-02.
TAILSCALE_VERSION=1.104.4
TAILSCALE_DEB_SHA256=8768973def76dd530b53a0fe74ca396252fff050f955aeebfabd33c77960625d

((EUID == 0)) || { echo "tailscale.sh: run as root (devbox/gce-host.sh)" >&2; exit 1; }

if [[ "$(dpkg-query -W -f='${Version}' tailscale 2>/dev/null || true)" == "$TAILSCALE_VERSION" ]]; then
    echo "tailscale.sh: tailscale $TAILSCALE_VERSION already installed"
else
    ts_tmp="$(mktemp -d)"
    ts_deb="$ts_tmp/tailscale_${TAILSCALE_VERSION}_amd64.deb"
    curl -fsSL --retry 3 -o "$ts_deb" \
        "https://pkgs.tailscale.com/stable/ubuntu/pool/tailscale_${TAILSCALE_VERSION}_amd64.deb"
    echo "$TAILSCALE_DEB_SHA256  $ts_deb" | sha256sum -c --quiet -
    # apt-get, not dpkg -i, so the package's dependency (iptables) comes with it.
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$ts_deb"
    rm -rf "$ts_tmp"
    echo "tailscale.sh: installed tailscale $TAILSCALE_VERSION"
fi
# The package enables tailscaled; a machine without systemd running (a container) has no daemon.
if [[ -d /run/systemd/system ]]; then
    systemctl enable --now tailscaled.service
fi

ts_host="${TAILSCALE_HOSTNAME:-$(hostname -s)}"
ts_state="$(tailscale status --self --json 2>/dev/null \
    | jq -r '[.BackendState, .Self.HostName, ((.Self.Tags // []) | join(","))] | join(" ")' 2>/dev/null || true)"
read -r ts_backend ts_name ts_tags <<<"$ts_state"
if [[ "$ts_backend" == Running && "$ts_name" == "$ts_host" && -z "$ts_tags" ]]; then
    echo "tailscale.sh: already on the tailnet as $ts_host, untagged"
else
    # --ssh: Tailscale SSH is how the laptop and Sami's other devices log in (the policy's SSH
    # rule for his own devices). --advertise-tags= with no value: the node carries no tag.
    ts_up=(tailscale up --hostname="$ts_host" --ssh --advertise-tags=)
    if [[ -n "$ts_tags" ]]; then
        ts_up+=(--force-reauth)
    fi
    echo "tailscale.sh: sign in as sami@theorem.dev at the login URL tailscale up prints below"
    "${ts_up[@]}"
    echo "tailscale.sh: joined the tailnet as $ts_host, untagged"
fi
