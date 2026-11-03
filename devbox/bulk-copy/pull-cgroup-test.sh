#!/usr/bin/env bash
# Test (sami-devbox, as root): does MemoryHigh + memory.reclaim before stop leave few inodes on a
# dying cgroup? Extracts a tar stream of SRC (about 6 GiB, many files) into a scratch target inside
# a test unit, samples its memory, reclaims, stops it, then counts inode_switch_wbs for 60 s.
# Usage: pull-cgroup-test.sh SRC_DIR
set -u
SRC=$1 U=devbox-copy-pulltest T=/mnt/scratch/devbox-pulltest
CG=/sys/fs/cgroup/system.slice/$U.service
rm -rf "$T"; mkdir -p "$T"
dying0=$(awk '/^nr_dying_descendants/{print $2}' /sys/fs/cgroup/cgroup.stat)
systemd-run --unit=$U -p MemoryHigh=16G /bin/sh -c "tar -C '$(dirname "$SRC")' -cf - '$(basename "$SRC")' | tar -C '$T' -xpf - --numeric-owner"
peak=0 pslab=0 pfile=0
while systemctl is-active -q $U; do
  cur=$(cat $CG/memory.current 2>/dev/null || echo 0)
  f=$(awk '$1=="file"{print $2}' $CG/memory.stat 2>/dev/null); s=$(awk '$1=="slab_reclaimable"{print $2}' $CG/memory.stat 2>/dev/null)
  (( cur > peak )) && peak=$cur; (( ${f:-0} > pfile )) && pfile=$f; (( ${s:-0} > pslab )) && pslab=$s
  sleep 2
done
echo "extract done: files=$(find "$T" -xdev | wc -l) bytes=$(du -sb "$T" | cut -f1)"
echo "during extract: memory.current peak=$peak file peak=$pfile slab_reclaimable peak=$pslab"
# The unit ended by itself; repeat the test with a held unit so the reclaim-then-stop path runs.
systemd-run --unit=$U-held -p MemoryHigh=16G /bin/sh -c "find '$T' -xdev -type f -print0 | xargs -0 cat >/dev/null; sleep 600"
sleep 5; while [ "$(pgrep -fc "xargs -0 cat")" != 0 ]; do sleep 2; done
CH=/sys/fs/cgroup/system.slice/$U-held.service
echo "held unit before reclaim: memory.current=$(cat $CH/memory.current) $(grep -E '^(file|slab_reclaimable) ' $CH/memory.stat | tr '\n' ' ')"
for i in 1 2 3 4 5; do echo 64G > $CH/memory.reclaim 2>/dev/null; (( $(cat $CH/memory.current) < 268435456 )) && break; done
echo "held unit after reclaim: memory.current=$(cat $CH/memory.current) $(grep -E '^(file|slab_reclaimable) ' $CH/memory.stat | tr '\n' ' ')"
systemctl stop $U-held
echo "dying cgroups before=$dying0 after-stop=$(awk '/^nr_dying_descendants/{print $2}' /sys/fs/cgroup/cgroup.stat)"
timeout 90 bpftrace -e 'tracepoint:writeback:inode_switch_wbs { @n = count(); } interval:s:60 { exit(); }' 2>&1 | grep -E '@n|Attaching' || echo "bpftrace: no inode_switch_wbs events"
echo "dying cgroups 60s later=$(awk '/^nr_dying_descendants/{print $2}' /sys/fs/cgroup/cgroup.stat)"
rm -rf "$T"
