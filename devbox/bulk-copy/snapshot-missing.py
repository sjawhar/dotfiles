#!/usr/bin/env python3
"""List the snapshot-copy entries the target lacks or has with another size (dispatch://INFRA-993).

Usage: snapshot-missing.py KEPT_FIND TARGET OUT_LIST [THREADS]
KEPT_FIND: `KIND SIZE PATH` NUL records (the filtered snapshot listing). For each non-directory,
lstat TARGET/PATH (threads; read-only); it is missing when absent, of another type, or (regular
file) of another size. Writes those paths, plus every directory above them, to OUT_LIST
(NUL-separated) for `rsync --files-from`. A single rsync over all 33.8 M entries spent hours
building its file list; this narrows it to what is left.
"""
import os
import stat
import sys
from concurrent.futures import ThreadPoolExecutor

kept, target, out = sys.argv[1], sys.argv[2].encode(), sys.argv[3]
threads = int(sys.argv[4]) if len(sys.argv) > 4 else 64
recs = []
for r in open(kept, "rb").read().split(b"\0"):
    if r:
        k, s, p = r.split(b" ", 2)
        if k != b"d":
            recs.append((k, int(s), p))


def check(batch):
    miss = []
    for k, s, p in batch:
        try:
            st = os.lstat(os.path.join(target, p))
        except FileNotFoundError:
            miss.append((p, s))
            continue
        m = st.st_mode
        if k == b"f" and (not stat.S_ISREG(m) or st.st_size != s):
            miss.append((p, s))
        elif k == b"l" and not stat.S_ISLNK(m):
            miss.append((p, s))
    return miss


B = 20000
batches = [recs[i:i + B] for i in range(0, len(recs), B)]
missing = []
with ThreadPoolExecutor(threads) as ex:
    for m in ex.map(check, batches):
        missing += m
dirs = set()
for p, _ in missing:
    d = os.path.dirname(p)
    while d and d not in dirs:
        dirs.add(d)
        d = os.path.dirname(d)
with open(out, "wb") as fh:
    for d in sorted(dirs):
        fh.write(d + b"\0")
    for p, _ in missing:
        fh.write(p + b"\0")
print(f"checked={len(recs)} missing={len(missing)} bytes={sum(s for _, s in missing)} dirs={len(dirs)}")
