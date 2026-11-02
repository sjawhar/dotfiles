#!/usr/bin/env python3
"""Split a `find -printf '%y %s %P\\0'` listing into tar shard lists (dispatch://INFRA-993 step 3).

Writes <out>/<tree>/:
  s0001.list ...  NUL-separated paths relative to the root (regular files, symlinks, others)
  zdirs.list      every directory (extracted last, so owners, modes and mtimes end right)
  manifest.tsv    shard, entries, estimated tar bytes
  expected.tsv    per top-level entry: files (non-dirs), bytes (apparent size)
Paths are kept when they match --include (if given) and no --exclude. Both are path prefixes
relative to the root; a prefix matches itself and everything below it.
"""
import argparse
import collections
import os

p = argparse.ArgumentParser()
p.add_argument("listing")
p.add_argument("tree")
p.add_argument("--out", default="lists")
p.add_argument("--include", action="append", default=[])
p.add_argument("--exclude", action="append", default=[])
p.add_argument("--shard-bytes", type=float, default=6.0, help="GiB per shard")
p.add_argument("--shard-entries", type=int, default=250_000)
p.add_argument("--keep", action="store_true",
               help="keep existing s*.list shards, skip their paths, number new shards after them")
p.add_argument("--partial", action="store_true",
               help="listing is still growing: drop its last (maybe cut) record, write no zdirs or expected.tsv")
p.add_argument("--depth", type=int, default=1, help="path components that name a group in expected.tsv")
a = p.parse_args()


def under(path, prefixes):
    return any(path == x or path.startswith(x + "/") for x in prefixes)


limit = int(a.shard_bytes * 2**30)
outdir = os.path.join(a.out, a.tree)
os.makedirs(outdir, exist_ok=True)
done_paths, old_manifest = set(), []
for f in sorted(os.listdir(outdir)):
    full = os.path.join(outdir, f)
    if a.keep and f.startswith("s") and f.endswith(".list"):
        with open(full, "rb") as fh:
            done_paths.update(x for x in fh.read().split(b"\0") if x)
        continue
    if a.keep and f == "manifest.tsv":
        with open(full) as fh:
            old_manifest = [ln.rstrip("\n").split("\t") for ln in fh if ln.startswith("s")]
    os.unlink(full)

def records(path):
    """NUL-terminated records, streamed; a --partial listing's cut last record is dropped."""
    rest = b""
    with open(path, "rb") as fh:
        while chunk := fh.read(64 << 20):
            parts = (rest + chunk).split(b"\0")
            rest = parts.pop()
            yield from parts
    if rest and not a.partial:
        yield rest


# Shards follow the listing's order (find's directory order), so memory stays small.
expected = collections.defaultdict(lambda: [0, 0])
manifest = [(r[0], int(r[1]), int(r[2])) for r in old_manifest]
n = max((int(r[0][1:]) for r in manifest), default=0)  # kept shards may have gaps
cur, cur_bytes, n_items, n_dirs = [], 0, 0, 0


def flush():
    global n, cur, cur_bytes
    if not cur:
        return
    n += 1
    name = f"s{n:04d}"
    with open(os.path.join(outdir, name + ".list"), "wb") as fh:
        fh.write(b"\0".join(cur) + b"\0")
    manifest.append((name, len(cur), cur_bytes))
    cur, cur_bytes = [], 0


dirs_fh = None if a.partial else open(os.path.join(outdir, "zdirs.list"), "wb")
for rec in records(a.listing):
    if not rec:
        continue
    kind, size, path = rec.split(b" ", 2)
    spath = path.decode("utf-8", "surrogateescape")
    if not path or (a.include and not under(spath, a.include)) or under(spath, a.exclude):
        continue
    parts = spath.split("/")
    if kind != b"d" or len(parts) >= a.depth:  # a directory above the group depth is no group
        e = expected["/".join(parts[: a.depth])]
    if kind == b"d":
        n_dirs += 1
        if dirs_fh:
            dirs_fh.write(path + b"\0")
        continue
    size = int(size)
    e[0] += 1
    e[1] += size if kind == b"f" else 0
    if path in done_paths:
        continue
    n_items += 1
    tar_bytes = 512 + (-(-size // 512) * 512 if kind == b"f" else 0)
    if cur and (cur_bytes + tar_bytes > limit or len(cur) >= a.shard_entries):
        flush()
    cur.append(path)
    cur_bytes += tar_bytes
flush()
if dirs_fh:
    dirs_fh.close()
    manifest.append(("zdirs", n_dirs, 512 * n_dirs))
with open(os.path.join(outdir, "manifest.tsv"), "w") as fh:
    for row in manifest:
        fh.write("\t".join(map(str, row)) + "\n")
if not a.partial:
    with open(os.path.join(outdir, "expected.tsv"), "w", encoding="utf-8", errors="surrogateescape") as fh:
        for top in sorted(expected):
            fh.write(f"{top}\t{expected[top][0]}\t{expected[top][1]}\n")
total = sum(m[2] for m in manifest)
print(f"{a.tree}: {len(manifest)} shards ({len(manifest) - len(old_manifest)} new), {n_items} new entries, "
      f"{n_dirs} dirs, {total / 2**30:.1f} GiB tar")
