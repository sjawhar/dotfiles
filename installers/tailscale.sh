#!/bin/bash
# Tailscale on a Google VM devbox (sami-devbox), joined with Tailscale's workload identity
# federation: tailscaled presents the VM's own Google identity token, requested for the
# federated identity's audience, and gets a node key for the one tag that identity may mint. No
# auth key is stored anywhere (dispatch://INFRA-993, "Network and Tailscale"; agent-infra
# bootstrap/samidevbox.go declares the identity, and bootstrap/README.md the `tailscale up` shape
# other Google VMs use).
#
# Root-level role installer: devbox/gce-host.sh sources it with sudo. Needs TAILSCALE_CLIENT_ID,
# the identity's client id, agent-infra bootstrap's stack output `samiDevboxTailnetClientId` (not
# a secret); Tailscale generates the audience from it. TAILSCALE_HOSTNAME and TAILSCALE_TAG
# default to this machine's short host name and tag:<that name>, the names the identity and the
# tailnet policy give sami-devbox.
#
# Idempotent: the package is installed only when the pinned version is not, and `tailscale up`
# runs only when the node is not already up under that name and tag.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The .deb from Tailscale's own Ubuntu pool, pinned. 1.94.0 is the first release whose
# `tailscale up --audience` fetches the cloud identity token itself (bootstrap/README.md).
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
ts_tag="${TAILSCALE_TAG:-tag:$ts_host}"
ts_state="$(tailscale status --self --json 2>/dev/null \
    | jq -r '[.BackendState, .Self.HostName, ((.Self.Tags // []) | join(","))] | join(" ")' 2>/dev/null || true)"
if [[ "$ts_state" == "Running $ts_host $ts_tag" ]]; then
    echo "tailscale.sh: already on the tailnet as $ts_host with $ts_tag"
else
    if [[ -z "${TAILSCALE_CLIENT_ID:-}" ]]; then
        echo "tailscale.sh: TAILSCALE_CLIENT_ID is not set. It is agent-infra bootstrap's stack output samiDevboxTailnetClientId (not a secret); rerun with it set." >&2
        exit 1
    fi
    # ephemeral=false: the node stays when tailscaled stops, as a devbox must across a reboot.
    # --ssh: Tailscale SSH is how the laptop logs in (the policy's SSH rule for the tag).
    tailscale up \
        --hostname="$ts_host" \
        --advertise-tags="$ts_tag" \
        --ssh \
        --accept-dns=true \
        --client-id="${TAILSCALE_CLIENT_ID}?ephemeral=false&preauthorized=true" \
        --audience="api.tailscale.com/${TAILSCALE_CLIENT_ID}"
    echo "tailscale.sh: joined the tailnet as $ts_host with $ts_tag"
fi
