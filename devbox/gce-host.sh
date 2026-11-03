#!/bin/bash
# gce-host.sh: the root-level host layer of a Google Cloud VM devbox (sami-devbox,
# dispatch://INFRA-993), run once with sudo by the provisioning runbook BEFORE install.sh, and safe
# to run again. It refuses any machine that is not a Google VM (scripts/on-google-vm).
#
#   1. the apt packages the dotfiles' installers and scripts expect and a bare Ubuntu 24.04
#      image lacks;
#   2. devbox/gce-disks and its unit, enabled and run now: state disk at /mnt/nvme, cold disk at
#      /mnt/durable, /home and Docker's, containerd's and Sysbox's data bound from the state disk,
#      the local-SSD RAID with its swap at /mnt/scratch; and the drop-in that keeps the user
#      manager and Docker from starting before it (devbox/gce-disks-ready.conf);
#   3. Docker (installers/docker.sh, for the user), then Sysbox (installers/sysbox.sh), which
#      registers itself with Docker;
#   4. Tailscale, joined by workload identity (installers/tailscale.sh; needs TAILSCALE_CLIENT_ID);
#   5. systemd lingering for the user, so user services outlive the last login;
#   6. sysstat, whose `sar` history is how the spec's acceptance A4 is measured.
#
# Usage: sudo TAILSCALE_CLIENT_ID=<id> devbox/gce-host.sh
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../installers/lib.sh"

DEVBOX_DIR="${DOTFILES_DIR}/devbox"
# uid 1000, the image's first user, whom the guest agent made from the instance's ssh key; the
# unit's ordering and drop-in name user@1000.service.
DEVBOX_UID=1000

die() {
    echo "gce-host.sh: $*" >&2
    exit 1
}

((EUID == 0)) || die "run with sudo"
"${DOTFILES_DIR}/scripts/on-google-vm" || die "not a Google Compute Engine VM (scripts/on-google-vm); this layer is for one"
devbox_user="$(getent passwd "$DEVBOX_UID" | cut -d: -f1)"
[[ -n "$devbox_user" ]] || die "no user with uid $DEVBOX_UID"

# --- 1. packages ---------------------------------------------------------------------------------
# mdadm, xfsprogs, nvme-cli: devbox/gce-disks (the VM's startup script installs them too, but this
#   layer must not depend on that having run);
# unzip, tmux, inotify-tools, libpcsclite1, jq: installers that call them, or apt-get them with a
#   sudo prompt, before mise has run (jj.sh, tmux.sh, secretsd.sh, claude.sh, lib.sh ensure_json);
# socat, e2fsprogs: scripts/agentbox's host relays and box disk images;
# build-essential, pkg-config, libssl-dev: a mise tool that falls back to `cargo install`;
# rsync, sqlite3, zstd, git-lfs: the /home and data copies of the cutover, and lanes' everyday use;
# python3-pip, python3-venv: the N6 farm's `python3 -m pip install --target` (oss-verifier
#   bin/ossv-n6-farm) and lanes' venvs; Ubuntu's python3 has neither;
# opam, m4, bubblewrap, libgmp-dev: lanes that build Rocq and OCaml with opam (m4 and libgmp-dev
#   for its packages, bwrap for its sandbox), as on sami-rocqperf;
# gdb, stress-ng, keyutils, libuv1-dev, postgresql-client: lanes' debugging, load runs, kernel
#   keyring, libuv builds and psql to the shared Postgres, as on sami-rocqperf;
# xvfb, qemu-utils, qemu-system-x86: lanes' headless X and VM images and runs, as on sami-rocqperf;
# the Chrome libraries: agent-browser's Chrome for Testing on the host, which its installer only
#   downloads (installers/agent-browser.sh).
export DEBIAN_FRONTEND=noninteractive
# Lists first: a fresh image ships none, and the headers' meta package is looked up below.
apt-get update -qq
packages=(
    mdadm xfsprogs nvme-cli
    unzip tmux inotify-tools libpcsclite1 jq
    socat e2fsprogs
    build-essential pkg-config libssl-dev
    rsync sqlite3 zstd git-lfs
    python3-pip python3-venv
    opam m4 bubblewrap libgmp-dev
    gdb stress-ng keyutils libuv1-dev postgresql-client
    xvfb qemu-utils qemu-system-x86
    sysstat
    libnss3 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 libgbm1 libxkbcommon0
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libasound2t64 libpango-1.0-0 libcairo2
)
# Sysbox mounts the host's kernel headers into each box and its package warns when they are
# missing; the flavour's meta package (linux-headers-gcp here, linux-headers-azure on
# sami-rocqperf) keeps them in step with kernel upgrades, the exact one covers the running kernel.
kernel_flavour="$(uname -r)"
kernel_flavour="${kernel_flavour##*-}"
packages+=("linux-headers-$(uname -r)")
if apt-cache show "linux-headers-${kernel_flavour}" >/dev/null 2>&1; then
    packages+=("linux-headers-${kernel_flavour}")
fi
missing=()
for p in "${packages[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || missing+=("$p")
done
if ((${#missing[@]})); then
    apt-get install -y --no-install-recommends "${missing[@]}"
else
    echo "gce-host.sh: every package already installed"
fi

# --- 2. the disks ---------------------------------------------------------------------------------
# The VM's startup script (agent-infra bootstrap/samidevbox.go) formats a blank state or cold disk
# at every boot; gce-disks never formats one and fails on a disk without XFS. On the first boot
# that script may still be running when this runs, so wait for it.
for name in state cold; do
    dev="/dev/disk/by-id/google-$name"
    for _ in $(seq 600); do
        [[ "$(blkid -p -s TYPE -o value "$dev" 2>/dev/null || true)" == xfs ]] && break
        sleep 1
    done
    [[ "$(blkid -p -s TYPE -o value "$dev" 2>/dev/null || true)" == xfs ]] \
        || die "$dev holds no XFS after 10 min: has the VM's startup script run? (journalctl -u google-startup-scripts)"
done

# Copied, not linked: the dotfiles live in /home, which this very unit mounts, so at boot systemd
# could not follow a link into them; and the bind would hide a link's target on the boot disk.
# A changed script or unit takes effect at the restart below.
home_was_bound=""
mountpoint -q /home && home_was_bound=1
install -m 755 "$DEVBOX_DIR/gce-disks" /usr/local/sbin/gce-disks
install -m 644 "$DEVBOX_DIR/gce-disks.service" /etc/systemd/system/gce-disks.service
for unit in "user@${DEVBOX_UID}.service" docker.service containerd.service sysbox-mgr.service; do
    install -d -m 755 "/etc/systemd/system/${unit}.d"
    install -m 644 "$DEVBOX_DIR/gce-disks-ready.conf" "/etc/systemd/system/${unit}.d/50-gce-disks-ready.conf"
done
systemctl daemon-reload
systemctl enable gce-disks.service
# restart, not start: it re-runs the script, which changes only what is missing, so an updated
# script applies now. No unit Requires= it, so nothing else restarts with it.
systemctl restart gce-disks.service || {
    journalctl -u gce-disks.service -b --no-pager -n 50 >&2
    die "gce-disks.service failed (above)"
}
# findmnt takes one mount point per call: two or more positional arguments are a usage error.
for m in /mnt/nvme /mnt/durable /mnt/scratch /home; do findmnt -no TARGET,SOURCE,FSTYPE --mountpoint "$m"; done
swapon --show

# --- 3. Docker, then Sysbox -----------------------------------------------------------------------
# installers/docker.sh is a user installer: USER names whom it adds to the docker group.
USER="$devbox_user" bash "${DOTFILES_DIR}/installers/docker.sh"
# shellcheck source=installers/sysbox.sh
source "${DOTFILES_DIR}/installers/sysbox.sh"
systemctl is-active --quiet docker.service || die "docker.service is not active"
systemctl is-active --quiet sysbox.service || die "sysbox.service is not active"
docker info --format 'docker: root {{.DockerRootDir}}, runtimes {{range $k, $v := .Runtimes}}{{$k}} {{end}}'

# --- 4. Tailscale ---------------------------------------------------------------------------------
# shellcheck source=installers/tailscale.sh
source "${DOTFILES_DIR}/installers/tailscale.sh"

# --- 5. lingering ---------------------------------------------------------------------------------
# The user's services (the forward server, the secrets helper, every devbox timer) die with the
# last login session without it (AGENTS.md, "Environment Facts").
loginctl enable-linger "$devbox_user"

# The operator's root, independent of google-guest-agent: the agent puts a user in its
# google-sudoers group while that user has an ssh key in the instance metadata and takes them out
# again, and on 2026-11-02 sami lost sudo over Tailscale SSH at 16:54Z while still in the group
# for `gcloud compute ssh` logins. The host-operator session needs root (spec R2).
sudoers=/etc/sudoers.d/90-sami-devbox-operator
printf '%s\n' "# $devbox_user, the devbox operator (devbox/gce-host.sh, dispatch://INFRA-993)." \
    "$devbox_user ALL=(ALL:ALL) NOPASSWD:ALL" >"$sudoers.new"
chmod 440 "$sudoers.new"
visudo -cqf "$sudoers.new" || die "sudoers: $sudoers.new does not parse"
mv -f "$sudoers.new" "$sudoers"

# --- 6. sysstat -----------------------------------------------------------------------------------
# Ubuntu installs sysstat with its collector off; A4 reads 7 days of `sar` history.
sed -i 's/^ENABLED="false"/ENABLED="true"/' /etc/default/sysstat
systemctl enable --now sysstat.service

if [[ -z "$home_was_bound" ]]; then
    echo "gce-host.sh: /home is now the state disk's. Processes started before (this login, the user manager) still see the boot disk's copy: reboot, or log out and back in, before running install.sh."
fi
echo "--- gce-host.sh complete ---"
