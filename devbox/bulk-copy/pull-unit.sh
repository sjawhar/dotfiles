#!/usr/bin/env bash
# The one persistent puller on sami-devbox (dispatch://INFRA-993 step 3), and its safe stop.
#   pull-unit.sh start   start devbox-copy-pull (both pull loops), MemoryHigh=16G, so reclaim keeps
#                        its page cache and inodes bounded as it goes
#   pull-unit.sh stop    reclaim the unit's memory until under 256 MiB, then stop it, so the dying
#                        cgroup holds few inodes (2026-11-02: stopping a pull unit with millions of
#                        charged inodes livelocked cgroup writeback; sync and umount hung)
set -eu
U=devbox-copy-pull CG=/sys/fs/cgroup/system.slice/devbox-copy-pull.service
cd /mnt/nvme/sami/cutover
case ${1:-} in
start)
  sudo systemctl reset-failed $U 2>/dev/null || true
  sudo systemd-run --uid=sami --gid=sami --unit=$U -p MemoryHigh=16G --setenv=HOME=/home/sami \
    --setenv=PATH="$PATH" --working-directory="$PWD" \
    /bin/bash -c "./pull.sh 12 trees-rocqperf.tsv & ./pull.sh 16 trees-leanperf.tsv & wait" ;;
stop)
  # Ask for what the cgroup holds, not a fixed 64G: a request larger than what is left makes the
  # write retry for a long time (2026-11-03 test: 20 x 64G writes ran past 25 minutes).
  for i in $(seq 1 20); do
    cur=$(cat $CG/memory.current)
    (( cur < 268435456 )) && break
    echo "$cur" | timeout 120 sudo tee $CG/memory.reclaim >/dev/null 2>&1 || true
  done
  echo "before stop: memory.current=$(cat $CG/memory.current) $(grep -E '^(file|slab_reclaimable) ' $CG/memory.stat | tr '\n' ' ')"
  sudo systemctl stop $U ;;
*) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
