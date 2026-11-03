#!/usr/bin/env bash
# Finish the snapshot copy from missing.list (snapshot-missing.py): 16 rsync streams over disjoint
# parts, all under ONE cgroup-guard (the unit's cgroup), then the verify. Run as root in unit
# devbox-copy-snapcopy on sami-devbox (dispatch://INFRA-993 step 3). The directories go first, in
# one pass, so later streams find their parents.
set -euo pipefail
C=/mnt/nvme/sami/cutover
L=$C/snapshot-copy
P=$L/finish
MNT=/mnt/durable-snap
rm -rf "$P"; mkdir -p "$P"
python3 - "$L/missing.list" "$P" <<'PY'
import sys
recs = [r for r in open(sys.argv[1], "rb").read().split(b"\0") if r]
import os
dirs = [r for r in recs if os.path.isdir(os.path.join(b"/mnt/durable-snap", r)) and not os.path.islink(os.path.join(b"/mnt/durable-snap", r))]
dset = set(dirs)
files = [r for r in recs if r not in dset]
open(f"{sys.argv[2]}/dirs.list", "wb").write(b"\0".join(dirs) + b"\0")
n = 16; size = -(-len(files) // n)
for i in range(n):
    part = files[i * size:(i + 1) * size]
    if part:
        open(f"{sys.argv[2]}/part{i:02d}.list", "wb").write(b"\0".join(part) + b"\0")
print(f"dirs={len(dirs)} files={len(files)}")
PY
run() { rsync -aHAX --numeric-ids --from0 --files-from="$1" "$MNT/" /mnt/durable/ >"$1.out" 2>"$1.err"; echo "rc=$?" >>"$1.out"; }
export -f run; export MNT
run "$P/dirs.list"
ls "$P"/part*.list | xargs -P 16 -I{} bash -c 'run {}'
grep -h '^rc=' "$P"/*.out | sort | uniq -c
echo "writes done $(date -u +%FT%TZ)"
