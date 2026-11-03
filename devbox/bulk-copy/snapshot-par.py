#!/usr/bin/env python3
"""Split the rest of the snapshot copy list into parts for parallel rsync (dispatch://INFRA-993).

Usage: snapshot-par.py KEPT_LIST CURRENT_PATH SKIP_FRACTION PARTS OUTDIR
The running single rsync is at CURRENT_PATH (relative). Entries after it, starting SKIP_FRACTION
of the remainder further on (so the single stream does not catch up with a part still running),
are cut into PARTS lists at top-level-directory-friendly boundaries (whole depth-2 groups), each
with the parent directories its entries need. Writes OUTDIR/partNN.list (NUL-separated).
"""
import os
import sys

lst, cur, skip, parts, out = sys.argv[1], sys.argv[2].encode(), float(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
d = [x for x in open(lst, "rb").read().split(b"\0") if x]
i = d.index(cur)
rest = d[i + 1:]
start = int(len(rest) * skip)
tail = rest[start:]
os.makedirs(out, exist_ok=True)
size = -(-len(tail) // parts)
k = 0
for n in range(parts):
    chunk = tail[n * size:(n + 1) * size]
    if not chunk:
        break
    with open(f"{out}/part{n:02d}.list", "wb") as fh:
        fh.write(b"\0".join(chunk) + b"\0")
    k += 1
print(f"position {i} of {len(d)}; single stream keeps {start} entries; {len(tail)} entries in {k} parts")
