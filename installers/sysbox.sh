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
# Idempotent: installs unless the pinned version is fully installed (status `install ok installed`).
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# 0.7.1 is the version sami-rocqperf runs. The sha256 is the one the release notes of
# github.com/nestybox/sysbox v0.7.1 list for this file, read on 2026-11-02.
SYSBOX_VERSION=0.7.1
SYSBOX_DEB_SHA256=9d6d5484f980d0a17f86c492c1262015c2afb66280bdb97215b79fde6a0261c5

((EUID == 0)) || { echo "sysbox.sh: run as root (devbox/gce-host.sh)" >&2; exit 1; }

# The package's fully installed status reads `install ok installed 0.7.1.linux`.
if [[ "$(dpkg-query -W -f='${Status} ${Version}' sysbox-ce 2>/dev/null || true)" == "install ok installed ${SYSBOX_VERSION}".* ]]; then
    echo "sysbox.sh: sysbox-ce $SYSBOX_VERSION already installed"
else
    command -v docker >/dev/null || { echo "sysbox.sh: install Docker first (installers/docker.sh)" >&2; exit 1; }
    sb_tmp="$(mktemp -d)"
    sb_deb="$sb_tmp/sysbox-ce_${SYSBOX_VERSION}.linux_amd64.deb"
    # The checked file itself, never apt's copy of it (install_checked_deb, installers/lib.sh).
    if curl -fsSL --retry 3 -o "$sb_deb" \
        "https://github.com/nestybox/sysbox/releases/download/v${SYSBOX_VERSION}/sysbox-ce_${SYSBOX_VERSION}.linux_amd64.deb" \
        && install_checked_deb "$sb_deb" "$SYSBOX_DEB_SHA256"; then
        rm -rf -- "$sb_tmp"
    else
        sb_status=$?
        rm -rf -- "$sb_tmp"
        exit "$sb_status"
    fi
    echo "sysbox.sh: installed sysbox-ce $SYSBOX_VERSION"
fi
sysbox-runc --version
