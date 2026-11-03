#!/usr/bin/env python3
"""On sami-devbox, as root: the top-level trees under each ROOT that a delta pass must not write
into (dispatch://INFRA-993 step 3). A tree is busy when a running process has its cwd or an open
file there, or when a file in it was written after SINCE (sessions moved here write them; the
first file found is enough). Output: ROOT/TREE<TAB>why.
Usage: delta-busy.py SINCE_EPOCH ROOT...
"""
import os
import sys

since = float(sys.argv[1])
roots = [r.rstrip("/") for r in sys.argv[2:]]
why = {}
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    links = [f"/proc/{pid}/cwd"]
    try:
        links += [f"/proc/{pid}/fd/{fd}" for fd in os.listdir(f"/proc/{pid}/fd")]
    except OSError:
        pass
    for link in links:
        try:
            t = os.readlink(link)
        except OSError:
            continue
        for r in roots:
            if t.startswith(r + "/"):
                top = r + "/" + t[len(r) + 1:].split("/", 1)[0]
                why.setdefault(top, f"in use by pid {pid}")


def newer(d):
    dev = os.lstat(d).st_dev
    stack = [d]
    while stack:
        x = stack.pop()
        try:
            it = os.scandir(x)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if e.is_dir(follow_symlinks=False):
                    if st.st_dev == dev:
                        stack.append(e.path)
                elif st.st_mtime > since:
                    return e.path
    return None


from concurrent.futures import ThreadPoolExecutor

tops = [f"{r}/{n}" for r in roots for n in sorted(os.listdir(r))]
tops = [t for t in tops if t not in why and os.path.isdir(t) and not os.path.islink(t)]
with ThreadPoolExecutor(64) as ex:
    for t, f in zip(tops, ex.map(newer, tops)):
        if f:
            why[t] = f"written after the cut-off: {f[len(t) + 1:]}"
for top in sorted(why):
    print(f"{top.rsplit('/', 1)[1]}\t{why[top]}")
