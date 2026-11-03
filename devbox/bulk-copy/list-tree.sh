#!/usr/bin/env bash
# Fresh listing of a tree for the bulk copy (dispatch://INFRA-993 step 3), run as root on the old
# box. Prunes the directories named by `path` rules (relative to ROOT) in the given rule files,
# so cut trees are not walked. Writes `KIND SIZE PATH` NUL records to OUT (on /mnt/durable).
# Usage: list-tree.sh ROOT OUT RULEFILE...
set -eu
ROOT=$1 OUT=$(realpath -m "$2"); shift 2
prunes=$(mktemp)
for f in "$@"; do awk -v r="$ROOT/" '$1=="path" && index($2, r)==1 {print substr($2, length(r)+1)}' "$f"; done \
  | sort -u >"$prunes"
cd "$ROOT"
# find -prune via a test: python decides prune per directory from the set (thousands of rules).
python3 - "$prunes" "$OUT.tmp" <<'PY'
import os, stat, sys
prunes = set(x for x in open(sys.argv[1], "rb").read().split(b"\n") if x)
out = open(sys.argv[2], "wb", buffering=1 << 20)
stack = [b""]
while stack:
    d = stack.pop()
    try:
        it = os.scandir(d or b".")
    except OSError as e:
        sys.stderr.write(f"unreadable {d!r}: {e}\n")
        continue
    with it:
        for e in it:
            rel = (d + b"/" + e.name) if d else e.name
            if rel in prunes:
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            m = st.st_mode
            k = b"d" if stat.S_ISDIR(m) else b"f" if stat.S_ISREG(m) else b"l" if stat.S_ISLNK(m) else b"o"
            out.write(k + b" " + str(st.st_size).encode() + b" " + rel + b"\0")
            if k == b"d" and st.st_dev == os.stat(".").st_dev:
                stack.append(rel)
out.close()
PY
mv "$OUT.tmp" "$OUT"
rm -f "$prunes"
