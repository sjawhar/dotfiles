#!/bin/bash
# The box memory guard: swap, reclaim settings, a bound on the user's slice, and the shed
# daemon (scripts/omp-box-memory-guard). Role installer, run from devbox/install.sh on a
# machine that runs agent sessions; there is nothing to arm on a laptop that only SSHes in.
#
# Why it exists: sami-rocqperf wedged on 2026-10-27. Available memory fell from 65,039 MiB at
# 08:45:45Z to 22,521 MiB at 08:48:36Z with swap 0 of 0, the kernel's machine-wide OOM followed
# at 08:53:24Z, and from 09:00Z the box sat at 95-99% system cpu with 0% idle -- direct reclaim
# with nothing to reclaim -- until it was rebooted at 13:56Z. Per-session MemoryMax scopes did
# not protect it: they sum to about four times the machine's RAM, and the sessions that held
# the memory had no scope at all (156 GiB of 193 GiB in one unlimited login scope).
#
# Four parts, in the order the kernel meets them:
#   1. swap on the big disk, so reclaim has somewhere to put cold anonymous memory;
#   2. reclaim sysctls, so kswapd starts before every core is in direct reclaim;
#   3. MemoryHigh/MemoryMax on user-<uid>.slice, so the user's sessions together can never take
#      the RAM the system's daemons need;
#   4. the shed daemon, which kills the largest single session when the first three are not
#      enough, after telling it and the INFRA lead over Envoy.
#
# Everything it does is idempotent and survives a reboot: a hand `echo` into cgroupfs or a
# `swapon` at a shell is exactly how this box got where it was.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

SWAP_DIR="${OMP_SWAP_DIR:-/mnt/nvme/swap}"
SWAP_FILE="${SWAP_DIR}/swapfile"
# The path the guard's own DEFAULT_STATE picks on a machine that has /mnt/nvme.
STATE_DIR="/mnt/nvme/sami/tmp/omp-box-memory-guard"
SLICE_DROPIN=/etc/systemd/system/user-.slice.d/60-omp-memory-guard.conf
SYSCTL_FILE=/etc/sysctl.d/70-omp-memory-guard.conf

mem_total_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)

# --- preconditions -----------------------------------------------------------------------
command -v python3 >/dev/null || {
    echo "omp-box-memory-guard: python3 not on PATH; the guard is a python3 script" >&2
    exit 1
}
[ -r /sys/fs/cgroup/cgroup.controllers ] || {
    echo "omp-box-memory-guard: no cgroup v2 at /sys/fs/cgroup" >&2
    exit 1
}
[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/legion/envoy.json" ] || {
    echo "omp-box-memory-guard: no envoy.json; the guard would have nowhere to send its alerts" >&2
    exit 1
}

# --- 1. swap -------------------------------------------------------------------------------
# A file on the NVMe array, not zram. zram buys capacity by compressing, and its pool lives in
# the very RAM the box is short of; this machine's scarce resource is RAM and its spare
# resource is a 3.4 TB array that sustains GB/s, so 64 GiB of real swap costs no RAM at all.
# Size: 64 GiB, or a quarter of the free space when that is less, so the swapfile can never be
# the thing that fills the disk. 64 GiB is about four minutes at the fastest sustained fall
# measured on this box, and more than the whole anonymous footprint of everything that is not
# an agent session.
#
# What swap does NOT do: a session started under `shims/omp` with OMP_MEMORY_MAX runs with
# MemorySwapMax=0, so swap never lets one session grow past its own ceiling. That is the right
# interaction and the shim should keep it -- a cap that could be paid for in swap would stop
# bounding the session's footprint and would thrash the array instead of killing one lane. Swap
# is here for everything else: the login scope's cold pages, idle services, and the reclaim
# headroom that keeps `memory.high` a slowdown rather than a stall.
if [ -d "$(dirname "$SWAP_DIR")" ]; then
    if ! swapon --show=NAME --noheadings | grep -qxF "$SWAP_FILE"; then
        # `sudo test`, not `[ -f ]`: the swap directory is 0700 root, so an unprivileged
        # stat of the file inside it says "no such file" and the install silently skips the
        # swapon and the fstab line -- which is how the first run here left 64 GiB of swap
        # on disk and none in use.
        if ! sudo test -f "$SWAP_FILE"; then
            free_kb=$(df --output=avail -k "$(dirname "$SWAP_DIR")" | tail -n1)
            want_gib=64
            quarter_gib=$((free_kb / 1024 / 1024 / 4))
            [ "$quarter_gib" -lt "$want_gib" ] && want_gib="$quarter_gib"
            if [ "$want_gib" -lt 8 ]; then
                echo "omp-box-memory-guard: only ${quarter_gib} GiB of swap would fit under $SWAP_DIR; skipping swap" >&2
            else
                echo "Creating ${want_gib} GiB of swap at ${SWAP_FILE}..."
                sudo mkdir -p "$SWAP_DIR"
                sudo chmod 700 "$SWAP_DIR"
                # fallocate leaves unwritten extents, which swapon refuses on some filesystems;
                # dd writes the blocks once and is the portable answer. ~1 minute on this array.
                sudo dd if=/dev/zero of="$SWAP_FILE" bs=1M count=$((want_gib * 1024)) status=none
                sudo chmod 600 "$SWAP_FILE"
                sudo mkswap "$SWAP_FILE" >/dev/null
            fi
        fi
        if sudo test -f "$SWAP_FILE"; then
            sudo swapon "$SWAP_FILE"
        fi
    fi
    # fstab, or the swap is gone at the next boot. nofail: a box that cannot mount the array
    # must still boot.
    if sudo test -f "$SWAP_FILE" && ! grep -qF "$SWAP_FILE" /etc/fstab; then
        echo "Recording ${SWAP_FILE} in /etc/fstab..."
        printf '%s none swap sw,nofail 0 0\n' "$SWAP_FILE" | sudo tee -a /etc/fstab >/dev/null
    fi
else
    echo "omp-box-memory-guard: $(dirname "$SWAP_DIR") is not on this machine; no swap file" >&2
fi

# --- 2. reclaim settings -------------------------------------------------------------------
# 0.4% of RAM for the kernel's own floor, capped at 1 GiB: enough that an atomic allocation
# inside writeback does not fail (it did, at 09:14:55Z), small enough to be noise on a big box.
min_free_kb=$((mem_total_kb * 4 / 1000))
[ "$min_free_kb" -gt 1048576 ] && min_free_kb=1048576
current_min_free=$(sysctl -n vm.min_free_kbytes)
[ "$min_free_kb" -lt "$current_min_free" ] && min_free_kb="$current_min_free"
sysctl_tmp=$(mktemp)
sed "s/@MIN_FREE_KB@/${min_free_kb}/" "${DOTFILES_DIR}/devbox/sysctl-memory-guard.conf" > "$sysctl_tmp"
if ! sudo diff -q "$sysctl_tmp" "$SYSCTL_FILE" >/dev/null 2>&1; then
    echo "Installing ${SYSCTL_FILE} (vm.min_free_kbytes=${min_free_kb})..."
    sudo install -m 0644 "$sysctl_tmp" "$SYSCTL_FILE"
    sudo sysctl -q -p "$SYSCTL_FILE"
fi
rm -f "$sysctl_tmp"

# --- 3. the slice's ceiling ------------------------------------------------------------------
# Percentages, not byte counts, so the same drop-in is right on a machine with other RAM.
if ! sudo diff -q "${DOTFILES_DIR}/devbox/user-slice-memory-guard.conf" "$SLICE_DROPIN" >/dev/null 2>&1; then
    echo "Installing ${SLICE_DROPIN}..."
    sudo mkdir -p "$(dirname "$SLICE_DROPIN")"
    sudo install -m 0644 "${DOTFILES_DIR}/devbox/user-slice-memory-guard.conf" "$SLICE_DROPIN"
    sudo systemctl daemon-reload
fi
# daemon-reload re-applies a slice's cgroup attributes to the live cgroup; prove it did.
slice_high=$(systemctl show "user-$(id -u).slice" -p MemoryHigh --value 2>/dev/null || echo unknown)
slice_max=$(systemctl show "user-$(id -u).slice" -p MemoryMax --value 2>/dev/null || echo unknown)
echo "user-$(id -u).slice: MemoryHigh=${slice_high} MemoryMax=${slice_max}"
if [ "$slice_high" = infinity ] || [ "$slice_high" = unknown ]; then
    echo "omp-box-memory-guard: the slice did not take its MemoryHigh; check ${SLICE_DROPIN}" >&2
    exit 1
fi

# --- 4. the shed daemon ----------------------------------------------------------------------
mkdir -p "$STATE_DIR" 2>/dev/null \
    || echo "NOTE: ${STATE_DIR} could not be created; pass --state to put the guard's state elsewhere."
if [ -n "${container:-}" ] || [ -f /.dockerenv ]; then
    echo "omp-box-memory-guard: inside a container; the guard belongs on the host" >&2
    exit 0
fi
mkdir -p "$HOME/.config/systemd/user"
ensure_link "$HOME/.dotfiles/omp/omp-box-memory-guard.service" \
    "$HOME/.config/systemd/user/omp-box-memory-guard.service"
systemctl --user daemon-reload
systemctl --user enable --now omp-box-memory-guard.service
systemctl --user is-active --quiet omp-box-memory-guard.service \
    || { echo "omp-box-memory-guard: the service did not start" >&2; exit 1; }
echo "omp-box-memory-guard: armed ($(swapon --show=NAME,SIZE --noheadings | tr '\n' ' '))"
