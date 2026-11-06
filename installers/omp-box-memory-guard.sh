#!/bin/bash
# The box memory guard: swap, reclaim settings and a bound on the user's slice. Role installer,
# run from devbox/install.sh on a machine that runs agent sessions; there is nothing to arm on a
# laptop that only SSHes in.
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
#   3. MemoryMax on user-<uid>.slice, so the user's sessions together can never take the RAM the
#      system's daemons need.
#   3b. MemoryHigh/MemoryMax on each session-<N>.scope (big machines only), so no single
#      unbounded login can reach that slice ceiling on its own -- one did on sami-devbox on
#      2026-11-06, at 684 GiB, and earlyoom answered with ~500 SIGTERMs across the fleet.
# Below them earlyoom (agent-infra's guard, `-s 100,100`) is the last-resort net, and
# scripts/earlyoom-kill-watch tells each session what it killed. A further part once planned, a
# daemon that shed the largest session (scripts/omp-box-memory-guard), never ran past a halted
# dry-run bake: four open defects from 2026-10-27, a false shed on a box with 135 GiB free among
# them (git show 2d52488). It was removed on 2026-11-03, and step 4 below takes its unit off a
# machine that has it.
#
# Everything it does is idempotent and survives a reboot: a hand `echo` into cgroupfs or a
# `swapon` at a shell is exactly how this box got where it was.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

SWAP_DIR="${OMP_SWAP_DIR:-/mnt/nvme/swap}"
SWAP_FILE="${SWAP_DIR}/swapfile"
SLICE_DROPIN=/etc/systemd/system/user-.slice.d/60-omp-memory-guard.conf
SESSION_DROPIN=/etc/systemd/system/session-.scope.d/60-omp-memory-guard.conf
# A login session scope is capped only on a machine big enough for the absolute numbers in
# devbox/session-scope-memory-guard.conf to be a real bound well under the slice ceiling; that
# file's header has the reasoning.
SESSION_DROPIN_MIN_KB=$((512 * 1024 * 1024))
SYSCTL_FILE=/etc/sysctl.d/70-omp-memory-guard.conf

mem_total_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)

# --- preconditions -----------------------------------------------------------------------
[ -r /sys/fs/cgroup/cgroup.controllers ] || {
    echo "omp-box-memory-guard: no cgroup v2 at /sys/fs/cgroup" >&2
    exit 1
}
# earlyoom must stay armed once there is swap. earlyoom acts only when memory AND free swap are
# both under their thresholds; its swap default is 10%, which a swap file keeps satisfied until
# the box is deep into an incident, so adding swap disarms earlyoom unless its drop-in sets a
# non-binding -s (theorem-labs/agent-infra PR #602 sets `-s 100,100`). If earlyoom is running
# here without such an -s, creating swap would silently remove the last-resort net, so refuse to
# until that PR has landed on this box. A box with no earlyoom (nothing to disarm) is fine.
earlyoom_swap_ok() {
    systemctl is-active -q earlyoom.service 2>/dev/null || return 0 # no earlyoom: nothing to disarm
    local args
    args=$(tr '\0' ' ' < "/proc/$(systemctl show -p MainPID --value earlyoom.service)/cmdline" 2>/dev/null) || return 1
    # Armed means an -s whose SIGTERM threshold is 100%: free swap is then never the blocker.
    [[ "$args" == *" -s 100,"* || "$args" == *" -s 100 "* ]]
}
swap_wanted=1
if ! earlyoom_swap_ok; then
    swap_wanted=
    echo "omp-box-memory-guard: earlyoom is running without an '-s 100,...' that keeps it armed" >&2
    echo "  with swap; NOT creating swap (it would disarm earlyoom). Land theorem-labs/agent-infra" >&2
    echo "  PR #602 on this box first, then re-run. The slice memory.max and the guard still install." >&2
fi

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
# is here for everything else: the login scope's cold pages, idle services, and the box-wide
# reclaim headroom the kswapd watermarks (part 2) work against.
# On sami-devbox the swap is devbox/gce-disks' 256 GiB file on the local-SSD scratch
# (/mnt/scratch/swapfile), remade at every boot because a stop erases those disks; a second file
# on the state disk would cost Hyperdisk IOPS the lanes need, and an fstab line here would race
# the unit that mounts it. So the unit's presence skips this part.
if [ -n "$swap_wanted" ] && [ -e /etc/systemd/system/gce-disks.service ]; then
    echo "omp-box-memory-guard: swap is devbox/gce-disks' (/mnt/scratch/swapfile); no swap file here"
    swapon --show
elif [ -n "$swap_wanted" ] && [ -d "$(dirname "$SWAP_DIR")" ]; then
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
elif [ -n "$swap_wanted" ]; then
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
slice_max=$(systemctl show "user-$(id -u).slice" -p MemoryMax --value 2>/dev/null || echo unknown)
echo "user-$(id -u).slice: MemoryMax=${slice_max}"
if [ "$slice_max" = infinity ] || [ "$slice_max" = unknown ]; then
    echo "omp-box-memory-guard: the slice did not take its MemoryMax; check ${SLICE_DROPIN}" >&2
    exit 1
fi

# --- 3b. each login session's ceiling ---------------------------------------------------------
# The slice's 92% is an aggregate, and one unbounded login can reach it alone: on 2026-11-06 a
# single plain-ssh session scope on sami-devbox held 684 GiB and earlyoom answered with ~500
# SIGTERMs across the fleet. logind's session-<N>.scope has no limit of its own, so give every
# one of them a MemoryHigh/MemoryMax through the prefix drop-in directory session-.scope.d/.
#
# `systemctl daemon-reload` applies the drop-in to the scopes that are ALREADY RUNNING, not
# only to the next login: measured on sami-devbox 2026-11-06T20:14Z, session-10548.scope,
# started long before the file existed, showed MemoryMax=256G straight after the reload. So
# installing this on a box where a live session is already over the ceiling -- exactly the
# situation the guard exists for -- would hard-cap that session at install time and have the
# kernel OOM-kill the work inside it. Refuse instead, name the session, and write nothing: a
# person then looks at what is holding the memory, and a re-run installs once it is below.
session_cap_over=
if [ "$mem_total_kb" -ge "$SESSION_DROPIN_MIN_KB" ]; then
    session_cap=$(awk -F= '/^MemoryMax=/ {print $2}' "${DOTFILES_DIR}/devbox/session-scope-memory-guard.conf")
    session_cap_bytes=$(numfmt --from=iec "$session_cap")
    while read -r scope; do
        [ -n "$scope" ] || continue
        current=$(systemctl show "$scope" -p MemoryCurrent --value 2>/dev/null || true)
        [[ "$current" =~ ^[0-9]+$ ]] || continue # [not set], infinity, or a scope that just ended
        if [ "$current" -gt "$session_cap_bytes" ]; then
            session_cap_over="${session_cap_over}  ${scope}: $((current / 1073741824)) GiB"$'\n'
        fi
    done < <(systemctl list-units --type=scope --all --plain --no-legend 'session-*.scope' 2>/dev/null | awk '{print $1}')
fi
if [ "$mem_total_kb" -lt "$SESSION_DROPIN_MIN_KB" ]; then
    echo "omp-box-memory-guard: $((mem_total_kb / 1048576)) GiB of RAM; no session-scope cap here"
elif [ -n "$session_cap_over" ]; then
    echo "omp-box-memory-guard: NOT installing ${SESSION_DROPIN}." >&2
    echo "  A live login session is already over the ${session_cap} ceiling, and daemon-reload would" >&2
    echo "  apply it to that running scope at once, OOM-killing the work inside it:" >&2
    printf '%s' "$session_cap_over" >&2
    echo "  Find what is holding the memory there, get it under ${session_cap}, then re-run this." >&2
elif ! sudo diff -q "${DOTFILES_DIR}/devbox/session-scope-memory-guard.conf" "$SESSION_DROPIN" >/dev/null 2>&1; then
    echo "Installing ${SESSION_DROPIN}..."
    sudo mkdir -p "$(dirname "$SESSION_DROPIN")"
    sudo install -m 0644 "${DOTFILES_DIR}/devbox/session-scope-memory-guard.conf" "$SESSION_DROPIN"
    sudo systemctl daemon-reload
fi
# Report, never fail on, what the session this installer runs in has.
if [ "$mem_total_kb" -ge "$SESSION_DROPIN_MIN_KB" ] && [ -z "$session_cap_over" ]; then
    session_scope=$(awk -F/ '/^0::/ {for (i = 1; i <= NF; i++) if ($i ~ /^session-[0-9]+\.scope$/) print $i}' /proc/self/cgroup)
    if [ -n "$session_scope" ]; then
        echo "$session_scope: MemoryMax=$(systemctl show "$session_scope" -p MemoryMax --value 2>/dev/null || echo unknown) (every live session scope takes this at the daemon-reload above, not at its next login)"
    fi
fi

# --- 4. the retired shed daemon --------------------------------------------------------------
# scripts/omp-box-memory-guard and its unit were removed on 2026-11-03 (see the header). A machine
# that installed the unit keeps a link to a file that no longer exists; take it off. After its
# false kill of 2026-10-27 16:13:51Z the unit ran only in --dry-run, and on sami-rocqperf it had
# been inactive since that day's halted bake.
if [ -n "${container:-}" ] || [ -f /.dockerenv ]; then
    exit 0
fi
retired_link="$HOME/.config/systemd/user/omp-box-memory-guard.service"
if [ -L "$retired_link" ] || [ -e "$retired_link" ]; then
    systemctl --user disable --now omp-box-memory-guard.service >/dev/null 2>&1 || true
    rm -f "$retired_link"
    systemctl --user daemon-reload
    echo "omp-box-memory-guard: removed the retired shed daemon's unit link ${retired_link}"
fi
