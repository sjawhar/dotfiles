#!/usr/bin/env python3
"""Drop excluded entries from a NUL-separated shard list (dispatch://INFRA-993 step 3).

Usage: exclude-filter.py ROOT EXCLUDE_FILE < list > filtered
List entries are relative to ROOT. EXCLUDE_FILE lines: `path P` (absolute, P and below),
`component C` (a path component equal to C), `name S` (a component containing S, any case).
Dropped entries go to stderr, one per line, so each shard keeps a record.
"""
import os
import sys

args = [x for x in sys.argv[1:] if x != "--records"]
records = "--records" in sys.argv  # input is a find listing of `KIND SIZE PATH` records
root, rule_files = args[0], args[1:]
paths, comps, names = set(), set(), []
for rules in rule_files:  # EXCLUDE, then optional extra rule files (e.g. a scope cut list)
    with open(rules) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            kind, _, arg = ln.partition(" ")
            if kind == "path":
                paths.add(arg.rstrip("/").encode())
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
    parts = rel.split(b"/")
    if comps.intersection(parts):
        return True
    # A path rule matches the entry or any directory above it: test each ancestor in the set.
    full = rootb
    if full in paths:
        return True
    for p in parts:
        full += b"/" + p
        if full in paths:
            return True
    low = rel.lower()
    return any(n in low for n in names)


data = sys.stdin.buffer.read().split(b"\0")
out = sys.stdout.buffer
for rec in data:
    if not rec:
        continue
    rel = rec.split(b" ", 2)[2] if records else rec
    if rel and excluded(rel):
        sys.stderr.buffer.write(b"EXCLUDED " + rel + b"\n")
    else:
        out.write(rec + b"\0")
