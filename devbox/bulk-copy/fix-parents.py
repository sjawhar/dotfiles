#!/usr/bin/env python3
"""Give implied parent directories the owner of their nearest real parent (dispatch://INFRA-993).

Usage: fix-parents.py TARGET START_EPOCH < names   (run as root; names = `tar -tv`-free name list,
one per line, as `tar -xv` prints them)
`tar -x` makes a missing parent directory owned by root (the extracting user) when the shard does
not carry that directory; the tree's last shard (zdirs) sets every directory's real owner, hours
later. Until then a root-owned directory blocks the owner's work (2026-11-03: an agentbox move
was refused). For each parent of an extracted name: a directory owned by root and changed at or
after START_EPOCH gets the owner and group of its nearest ancestor that is not such a directory.
Prints the count it changed.
"""
import os
import sys

target, start = sys.argv[1].rstrip("/"), float(sys.argv[2])
seen, changed = set(), 0
for line in sys.stdin.buffer:
    name = line.rstrip(b"\n").rstrip(b"/")
    parent = os.path.dirname(name)
    chain = []
    while parent and parent not in seen:
        seen.add(parent)
        chain.append(parent)
        parent = os.path.dirname(parent)
    for p in reversed(chain):  # top-down, so a parent is fixed before its children
        full = os.path.join(target.encode(), p)
        try:
            st = os.lstat(full)
        except OSError:
            continue
        if st.st_uid != 0 or st.st_ctime < start - 1 or not os.path.isdir(full):
            continue
        anc = os.path.dirname(full)
        while anc and anc != b"/":
            a = os.lstat(anc)
            if a.st_uid != 0 or a.st_ctime < start - 1:
                break
            anc = os.path.dirname(anc)
        a = os.lstat(anc)
        if a.st_uid != 0:
            os.lchown(full, a.st_uid, a.st_gid)
            changed += 1
print(changed)
