#!/usr/bin/env python3
"""Give root-owned parent directories the owner of their nearest non-root parent (dispatch://INFRA-993).

Usage: fix-parents.py TARGET START_EPOCH < names   (run as root; names as `tar -xv` prints them)
`tar -x` makes a missing parent directory owned by root when the shard does not carry that
directory; the tree's last shard (zdirs) sets every directory's real owner, hours later. Until then
a root-owned directory blocks the owner's work (2026-11-03: an agentbox move was refused). For each
parent of an extracted name that is a root-owned directory, this sets the owner and group of its
nearest ancestor that is not root-owned. It does not look at ctime: shards extract 12 at a time, so
a parent may come from another shard that started earlier (the first version skipped those and left
1.5 million root directories). The few directories root owns on the source get their owner back
from the zdirs shard. START_EPOCH is kept for the caller's interface. Prints the count it changed.
"""
import os
import sys

target = sys.argv[1].rstrip("/").encode()
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
        full = os.path.join(target, p)
        try:
            st = os.lstat(full)
        except OSError:
            continue
        if st.st_uid != 0 or not os.path.isdir(full) or os.path.islink(full):
            continue
        anc = os.path.dirname(full)
        while anc and anc != b"/" and os.lstat(anc).st_uid == 0:
            anc = os.path.dirname(anc)
        a = os.lstat(anc)
        if a.st_uid != 0:
            os.lchown(full, a.st_uid, a.st_gid)
            changed += 1
print(changed)
