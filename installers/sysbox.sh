#!/bin/bash
# Sysbox CE, the container runtime every agent box runs under (scripts/agentbox, RUNTIME
# sysbox-runc): root in a box is an unprivileged user namespace on the host, and the box runs its
# own dockerd.
#
# Root-level role installer: devbox/gce-host.sh sources it with sudo, after Docker, because the
# package's install script registers sysbox-runc in /etc/docker/daemon.json and restarts dockerd;
# installed before Docker, Docker would not know the runtime. Ubuntu 24.04 with its 6.8 or later
# kernel is supported from 0.7.1 (its release notes).
#
# Idempotent: installs only when the pinned version is not installed.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# 0.7.1 is the version sami-rocqperf runs. The sha256 is the one the release notes of
# github.com/nestybox/sysbox v0.7.1 list for this file, read on 2026-11-02.
SYSBOX_VERSION=0.7.1
SYSBOX_DEB_SHA256=9d6d5484f980d0a17f86c492c1262015c2afb66280bdb97215b79fde6a0261c5

((EUID == 0)) || { echo "sysbox.sh: run as root (devbox/gce-host.sh)" >&2; exit 1; }

# The package version reads 0.7.1.linux.
if [[ "$(dpkg-query -W -f='${Version}' sysbox-ce 2>/dev/null || true)" == "${SYSBOX_VERSION}".* ]]; then
    echo "sysbox.sh: sysbox-ce $SYSBOX_VERSION already installed"
else
    command -v docker >/dev/null || { echo "sysbox.sh: install Docker first (installers/docker.sh)" >&2; exit 1; }
    sb_tmp="$(mktemp -d)"
    sb_deb="$sb_tmp/sysbox-ce_${SYSBOX_VERSION}.linux_amd64.deb"
    curl -fsSL --retry 3 -o "$sb_deb" \
        "https://github.com/nestybox/sysbox/releases/download/v${SYSBOX_VERSION}/sysbox-ce_${SYSBOX_VERSION}.linux_amd64.deb"
    echo "$SYSBOX_DEB_SHA256  $sb_deb" | sha256sum -c --quiet -
    # apt-get, not dpkg -i, so the package's dependencies (jq, fuse and the like) come with it.
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$sb_deb"
    rm -rf "$sb_tmp"
    echo "sysbox.sh: installed sysbox-ce $SYSBOX_VERSION"
fi
sysbox-runc --version
