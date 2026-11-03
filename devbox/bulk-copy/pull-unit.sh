#!/usr/bin/env bash
# The one persistent puller on sami-devbox (dispatch://INFRA-993 step 3), and its safe stop.
#   pull-unit.sh start   start devbox-copy-pull (both pull loops) under cgroup-guard.sh, MemoryHigh=16G:
#                        the guard keeps the unit's slab (its inodes) under 1 GiB as it goes and
#                        drains the cgroup whenever the loops end
#   pull-unit.sh stop    drain the unit's cgroup to under 256 MiB, then stop it, so the dying
#                        cgroup holds few inodes (2026-11-02 and 11-03: a copy cgroup that died with
#                        millions of charged inodes livelocked cgroup writeback; sync and umount hung)
set -eu
U=devbox-copy-pull CG=/sys/fs/cgroup/system.slice/devbox-copy-pull.service
cd /mnt/nvme/sami/cutover
case ${1:-} in
start)
  sudo systemctl reset-failed $U 2>/dev/null || true
  sudo systemd-run --uid=sami --gid=sami --unit=$U -p MemoryHigh=16G --setenv=HOME=/home/sami \
    --setenv=PATH="$PATH" --working-directory="$PWD" \
    ./cgroup-guard.sh run -- /bin/bash -c "./pull.sh 12 trees-rocqperf.tsv & ./pull.sh 16 trees-leanperf.tsv & wait" ;;
stop)
  GUARD_CG=$CG ./cgroup-guard.sh drain
  echo "before stop: memory.current=$(cat $CG/memory.current) $(grep -E '^(file|slab_reclaimable) ' $CG/memory.stat | tr '\n' ' ')"
  sudo systemctl stop $U ;;
*) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
