#!/usr/bin/env python3
"""Plan one rocqperf -> sami-devbox delta pass, tree by tree (dispatch://INFRA-993 step 3).

Usage: delta-plan.py ROOT CLASSES_TSV MOVE_TABLE DEVBOX_BUSY > plan.tsv
ROOT: /mnt/nvme/sami or /mnt/durable/sami (or /mnt/durable). One row per top-level entry of ROOT:
  copy|skip <TAB> path <TAB> reason
Rules (Main, 2026-11-03 18:1xZ):
 (a) owners from the move table, read fresh: a tree whose owner session (from the scope classes'
     /proc evidence) has moved is skipped; a tree with no known owner is copied unless sami-devbox
     shows writes in it after the 13:33Z reset (DEVBOX_BUSY lists those trees);
 (b) never copy into a path a running sami-devbox session has as cwd or holds open (DEVBOX_BUSY
     lists those too).
DEVBOX_BUSY: lines `TREE<TAB>why`, made on sami-devbox by delta-busy.sh.
"""
import collections
import os
import re
import sys

root, classes, table, busy_file = sys.argv[1:5]
moved = set()
for ln in open(table, errors="replace"):
    if not ln.startswith("|"):
        continue
    ids = re.findall(r"`(01a[0-9a-f]{5}-[0-9a-f]{4})`", ln)
    cells = [c.strip() for c in ln.split("|")]
    if ids and len(cells) > 4 and cells[4].startswith(("moved", "boxed")) and "moved" in cells[4]:
        moved.update(ids)
owners = collections.defaultdict(set)
if root.startswith("/mnt/nvme/sami") and os.path.exists(classes):
    for ln in open(classes, errors="replace"):
        f = ln.rstrip("\n").split("\t")
        if len(f) < 4 or f[0] == "class":
            continue
        top = f[2].split("/", 1)[0]
        for sid in re.findall(r"\((01a[0-9a-f]{5}-[0-9a-f]{4})\)", f[3]):
            owners[top].add(sid)
busy = {}
for ln in open(busy_file, errors="replace"):
    t, _, why = ln.rstrip("\n").partition("\t")
    if t:
        busy[t] = why
for name in sorted(os.listdir(root)):
    p = os.path.join(root, name)
    mv = sorted(owners[name] & moved)
    if mv:
        print(f"skip\t{p}\towner moved: {','.join(mv)}")
    elif name in busy:
        print(f"skip\t{p}\tsami-devbox: {busy[name]}")
    else:
        why = "owner not moved: " + ",".join(sorted(owners[name])) if owners[name] else "no known owner, no writes on sami-devbox since 13:33Z"
        print(f"copy\t{p}\t{why}")
