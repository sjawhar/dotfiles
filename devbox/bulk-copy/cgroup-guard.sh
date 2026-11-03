#!/usr/bin/env bash
# Keep the inodes charged to a bulk copy's cgroup few, so the cgroup never dies holding millions.
#
#   cgroup-guard.sh run -- CMD [ARG...]   run CMD; every $GUARD_INTERVAL s, reclaim the cgroup when its
#                                         slab is over $GUARD_SLAB_HIGH; when CMD ends (or on INT/TERM,
#                                         passed on to CMD), drain the cgroup before exiting with CMD's code
#   cgroup-guard.sh drain                 drain this process's cgroup now
#   cgroup-guard.sh check                 reclaim once if the slab is over $GUARD_SLAB_HIGH
#
# Why (dispatch://INFRA-993): when a cgroup ends, the kernel moves every inode it wrote to the
# parent's writeback (cleanup_offline_cgwb). Before torvalds f6988c90671e (v7.3) that move re-walks
# every queued inode on each pass under wb->list_lock. On sami-devbox, 7.0.0-1014-gcp, a copy unit
# that ended holding ~15 M inodes switched 57-200 inodes/s and slowed down over time. Every umount
# then hung in cgroup_writeback_umount, and only a reset cleared it (2026-11-02 and 2026-11-03).
# MemoryHigh alone does not prevent it: 16G of slab is ~13 M XFS inodes. Reclaim while the cgroup is
# alive evicts the inodes instead, with no switch.
#
# Run CMD under this once per cgroup (at the top of the copy, not per worker): every guard reclaims
# the whole cgroup. Writes to memory.reclaim need root; the guard uses sudo -n when it is not root.
set -u
GUARD_INTERVAL=${GUARD_INTERVAL:-30}
GUARD_SLAB_HIGH=${GUARD_SLAB_HIGH:-$((1024 ** 3))}  # ~800k XFS inodes; a dead wb of this size drains in minutes
GUARD_DRAIN_TO=${GUARD_DRAIN_TO:-$((256 * 1024 ** 2))}
GUARD_WRITE_TIMEOUT=${GUARD_WRITE_TIMEOUT:-120}
GUARD_ROOT=${GUARD_ROOT:-/sys/fs/cgroup}

cg() {  # this process's cgroup v2 directory (GUARD_CG overrides, for tests)
  if [[ -n ${GUARD_CG:-} ]]; then echo "$GUARD_CG"; return; fi
  local line
  line=$(grep -m1 '^0::' /proc/self/cgroup) || return 1
  echo "$GUARD_ROOT${line#0::}"
}
stat_of() { awk -v k="$2" '$1 == k {print $2; exit}' "$1/memory.stat"; }

# Ask for what the cgroup holds, not more: a request larger than what is left makes the write retry
# for a long time (2026-11-03 test: 20 x 64G writes ran past 25 minutes).
reclaim() {
  local dir=$1 cur
  cur=$(cat "$dir/memory.current") || return 1
  if [[ -w $dir/memory.reclaim ]]; then
    echo "$cur" | timeout "$GUARD_WRITE_TIMEOUT" tee "$dir/memory.reclaim" >/dev/null 2>&1
  else
    echo "$cur" | timeout "$GUARD_WRITE_TIMEOUT" sudo -n tee "$dir/memory.reclaim" >/dev/null 2>&1
  fi
}

check() {
  local dir slab
  dir=$(cg) || return 0
  slab=$(stat_of "$dir" slab_reclaimable) || return 0
  [[ -n $slab ]] && (( slab > GUARD_SLAB_HIGH )) || return 0
  reclaim "$dir"
  echo "cgroup-guard: $dir slab_reclaimable $slab > $GUARD_SLAB_HIGH; after reclaim $(stat_of "$dir" slab_reclaimable)" >&2
}

drain() {
  local dir _
  dir=$(cg) || { echo "cgroup-guard: no cgroup v2 entry; nothing drained" >&2; return 0; }
  for _ in $(seq 1 20); do
    (( $(cat "$dir/memory.current") < GUARD_DRAIN_TO )) && break
    reclaim "$dir"
  done
  echo "cgroup-guard: drained $dir: memory.current=$(cat "$dir/memory.current") file=$(stat_of "$dir" file) slab_reclaimable=$(stat_of "$dir" slab_reclaimable)" >&2
}

run() {
  [[ ${1:-} == -- ]] && shift
  (( $# )) || { echo "usage: $0 run -- CMD [ARG...]" >&2; exit 2; }
  # Without job control, bash gives a background job /dev/null as stdin; the explicit redirect
  # keeps ours, so a command that reads stdin (rsync --server under --rsync-path) still works.
  "$@" <&0 &
  local child=$! nap rc stopping=""
  trap 'stopping=1; kill -TERM "$child" 2>/dev/null' INT TERM
  # Wait for whichever ends first, the command or the nap, so the guard exits as soon as the
  # command does, not at the end of the current nap (2026-11-03: one rsync per small tree cost
  # ~30 s each). A signalled command is a zombie until bash reaps it, and `kill -0` still finds
  # it, so the trap's flag ends the loop; `wait "$child"` below returns its status either way.
  while [[ -z $stopping ]] && kill -0 "$child" 2>/dev/null; do
    check
    sleep "$GUARD_INTERVAL" &
    nap=$!
    wait -n "$child" "$nap" 2>/dev/null
    kill "$nap" 2>/dev/null
  done
  wait "$child"; rc=$?
  trap - INT TERM
  drain
  exit "$rc"
}

case ${1:-} in
run) shift; run "$@" ;;
drain) drain ;;
check) check ;;
*) echo "usage: $0 run -- CMD [ARG...] | drain | check" >&2; exit 2 ;;
esac
