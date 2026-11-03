#!/usr/bin/env python3
"""no-offbox check for one shard list, with path-scoped fixture exceptions (dispatch://INFRA-993).

Usage: offbox-gate.py NO_OFFBOX ROOT LIST EXCEPT_FILE  (run as root; exit 0 = may ship)
Runs `NO_OFFBOX --root ROOT --from LIST -0`. A refusal passes only when every refused line names a
marker (not a pattern or an unreadable dir), and each marker is an EMPTY `.no-offbox` under a
`path` in EXCEPT_FILE. An empty marker there is a test fixture (LANE-C, 2026-11-03 04:51Z: every
real marker carries reason text); a marker with text still refuses. Prints no-offbox's output and
the decision.
"""
import os
import re
import subprocess
import sys

tool, root, lst, except_file = sys.argv[1:5]
prefixes = []
if os.path.exists(except_file):
    for ln in open(except_file):
        kind, _, arg = ln.strip().partition(" ")
        if kind == "path":
            prefixes.append(arg.rstrip("/") + "/")
r = subprocess.run([tool, "--root", root, "--from", lst, "-0"], capture_output=True, text=True)
sys.stdout.write(r.stdout + r.stderr)
if r.returncode == 0:
    sys.exit(0)
if r.returncode != 1:
    print(f"offbox-gate: no-offbox exit {r.returncode}: refuse")
    sys.exit(2)
markers = set()
for ln in (r.stdout + r.stderr).splitlines():
    if "REFUSED" not in ln:
        continue
    m = re.search(r": marker (/.+)$", ln)
    if not m:
        print(f"offbox-gate: refusal is not a marker: {ln}")
        sys.exit(1)
    markers.add(m.group(1))
for mk in sorted(markers):
    ok = any(mk.startswith(p) for p in prefixes) and os.path.basename(mk) == ".no-offbox" and os.path.getsize(mk) == 0
    if not ok:
        print(f"offbox-gate: refuse, marker {mk} is not an empty fixture under an exception")
        sys.exit(1)
print(f"offbox-gate: pass, {len(markers)} empty fixture marker(s) under an exception")
sys.exit(0)
