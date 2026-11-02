#!/usr/bin/env python3
"""Drop excluded entries from a NUL-separated shard list (dispatch://INFRA-993 step 3).

Usage: exclude-filter.py ROOT EXCLUDE_FILE < list > filtered
List entries are relative to ROOT. EXCLUDE_FILE lines: `path P` (absolute, P and below),
`component C` (a path component equal to C), `name S` (a component containing S, any case).
Dropped entries go to stderr, one per line, so each shard keeps a record.
"""
import os
import sys

root, rules = sys.argv[1], sys.argv[2]
paths, comps, names = [], set(), []
with open(rules) as fh:
    for ln in fh:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        kind, _, arg = ln.partition(" ")
        if kind == "path":
            paths.append(arg.rstrip("/").encode())
        elif kind == "component":
            comps.add(arg.encode())
        elif kind == "name":
            names.append(arg.lower().encode())
        else:
            sys.exit(f"bad rule: {ln}")
if not (paths or comps or names):
    sys.exit("exclusion file has no rules")
rootb = root.rstrip("/").encode()


def excluded(rel):
    full = os.path.join(rootb, rel)
    if any(full == p or full.startswith(p + b"/") for p in paths):
        return True
    parts = rel.split(b"/")
    if comps.intersection(parts):
        return True
    low = rel.lower()
    return any(n in low for n in names)


data = sys.stdin.buffer.read().split(b"\0")
out = sys.stdout.buffer
for rel in data:
    if not rel:
        continue
    if excluded(rel):
        sys.stderr.buffer.write(b"EXCLUDED " + rel + b"\n")
    else:
        out.write(rel + b"\0")
