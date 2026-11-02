#!/usr/bin/env python3
"""Compare a copied tree on sami-devbox with the source listing (dispatch://INFRA-993 step 3).

expected.tsv (from mkshards.py): group, files (non-directories), bytes (regular files).
Walks TARGET/<group> for each group (in parallel; run as root to read every file), counts the
same way, prints one line per group that differs and a total. Exit 1 when any group differs.
Usage: verify.py EXPECTED_TSV TARGET [--jobs N]
"""
import argparse
import os
import stat
import sys
from concurrent.futures import ProcessPoolExecutor


def walk(path):
    files = size = 0
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return 0, 0
    if not stat.S_ISDIR(st.st_mode):
        return 1, st.st_size if stat.S_ISREG(st.st_mode) else 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                        continue
                    files += 1
                    if e.is_file(follow_symlinks=False):
                        size += e.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    return files, size


p = argparse.ArgumentParser()
p.add_argument("expected")
p.add_argument("target")
p.add_argument("--jobs", type=int, default=64)
a = p.parse_args()
rows = []
with open(a.expected, encoding="utf-8", errors="surrogateescape") as fh:
    for ln in fh:
        g, n, b = ln.rstrip("\n").split("\t")
        rows.append((g, int(n), int(b)))
paths = [os.path.join(a.target, g) for g, _, _ in rows]
with ProcessPoolExecutor(a.jobs) as ex:
    got = list(ex.map(walk, paths, chunksize=1))
bad = 0
tn = tb = gn = gb = 0
for (g, n, b), (fn, fb) in zip(rows, got):
    tn, tb, gn, gb = tn + n, tb + b, gn + fn, gb + fb
    if (n, b) != (fn, fb):
        bad += 1
        print(f"DIFF\t{g}\tsource files={n} bytes={b}\ttarget files={fn} bytes={fb}")
print(f"TOTAL\tgroups={len(rows)} differ={bad}\tsource files={tn} bytes={tb}\ttarget files={gn} bytes={gb}")
sys.exit(1 if bad else 0)
