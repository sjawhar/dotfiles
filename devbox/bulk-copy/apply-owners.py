#!/usr/bin/env python3
"""Set source owners on copied entries that tar left root-owned (dispatch://INFRA-993 step 3).

Usage: apply-owners.py LIST   (root; LIST = NUL-separated `UID GID PATH` records made on the old
box by stat-ing each root-owned target path's source). An entry is changed only while it is still
owned by root, so a later extract or a person's chown wins. Prints counts.
"""
import os
import sys

changed = skipped = gone = 0
for rec in open(sys.argv[1], "rb").read().split(b"\0"):
    if not rec:
        continue
    uid, gid, path = rec.split(b" ", 2)
    try:
        if os.lstat(path).st_uid != 0:
            skipped += 1
            continue
        os.lchown(path, int(uid), int(gid))
        changed += 1
    except FileNotFoundError:
        gone += 1
print(f"changed={changed} already-not-root={skipped} gone={gone}")
