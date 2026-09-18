#!/usr/bin/env python3
"""Compact a shared jj repo's operation store without stranding its workspaces.

Why this exists (measured 2026-09-19 on agent-c): every jj operation stores a full
view (all bookmarks, remote bookmarks, tags, heads, workspace commits). With ~180
workspaces and ~50 sessions the store took ~7k operations/day at 1-3 MB per view,
i.e. 10-25 GB/day; `.jj/repo/op_store` reached 187 GB. `jj op abandon ..X` plus
`jj util gc --expire=now` is the documented remedy, but on a shared store both halves
bite: `op abandon` rewrites the id of EVERY operation after X, so every other
workspace's recorded working-copy operation vanishes and its next command fails with
"Could not read working copy's operation. Run `jj workspace update-stale`" (which
then plants a RECOVERY COMMIT on top of @); and `util gc --expire=now` also runs
`git gc --prune=now` on the backing git repo, which can delete objects a concurrent
writer has written but not yet referenced.

What this does instead:
  1. Snapshot the reachable operation set and every workspace's recorded operation id.
  2. `jj op abandon ..X` where X is the newest operation older than --keep-days.
  3. Map old -> reparented operation by content (same view, metadata, predecessors;
     only the parents changed) and rewrite each workspace's `checkout` file under
     jj's own working-copy flock, so no workspace ever sees a missing operation.
  4. Concurrent operations committed during (2)-(3) merge the old chain back in
     ("reconcile divergent operations"); detect that and repeat from (2) on the
     re-attached old heads until an iteration completes clean, then hold for
     --settle-seconds with no re-attachment.
  5. Sweep: delete operation/view files unreachable from the heads whose mtime
     predates the sweep's own start (jj's `SimpleOpStore::gc` rule with
     keep_newer=now; a file written after the reachability walk is renewed by the
     rename that writes it, so it survives). The git repo is not touched.

Every mutation is ledgered (JSONL). Without --apply nothing changes.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

JJ_FLAGS = ["--ignore-working-copy", "--config", "fsmonitor.backend=none", "--color", "never"]


# ---------------------------------------------------------------- protobuf (wire level)

def _varint(b: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7


def proto_fields(b: bytes) -> dict[int, list[bytes | int]]:
    """Top-level fields; length-delimited values as bytes, varints as int."""
    out: dict[int, list[bytes | int]] = {}
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        fno, wt = key >> 3, key & 7
        if wt == 2:
            ln, i = _varint(b, i)
            out.setdefault(fno, []).append(b[i:i + ln])
            i += ln
        elif wt == 0:
            v, i = _varint(b, i)
            out.setdefault(fno, []).append(v)
        elif wt == 1:
            out.setdefault(fno, []).append(b[i:i + 8])
            i += 8
        elif wt == 5:
            out.setdefault(fno, []).append(b[i:i + 4])
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
    return out


def _encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_checkout(operation_id: bytes, workspace_name: bytes) -> bytes:
    # local_working_copy.proto: Checkout { bytes operation_id = 2; string workspace_name = 3; }
    return (_encode_varint((2 << 3) | 2) + _encode_varint(len(operation_id)) + operation_id
            + _encode_varint((3 << 3) | 2) + _encode_varint(len(workspace_name)) + workspace_name)


# ---------------------------------------------------------------- op store

class OpStore:
    def __init__(self, repo: Path):
        self.repo = repo
        self.store = repo / ".jj/repo"
        self.ops_dir = self.store / "op_store/operations"
        self.views_dir = self.store / "op_store/views"
        self.heads_dir = self.store / "op_heads/heads"
        self._cache: dict[str, dict[str, Any]] = {}

    def heads(self) -> list[str]:
        return sorted(p.name for p in self.heads_dir.iterdir())

    def read_op(self, op_id: str) -> dict[str, Any]:
        if op_id in self._cache:
            return self._cache[op_id]
        if set(op_id) == {"0"}:  # the root operation is virtual: no file, root view, no parents
            return {"view": "0" * len(op_id), "parents": [], "meta": b"", "preds": (), "millis": 0, "desc": "root"}
        f = proto_fields((self.ops_dir / op_id).read_bytes())
        meta = f.get(3, [b""])[0]
        assert isinstance(meta, bytes)
        mf = proto_fields(meta)
        start = mf.get(1, [b""])[0]
        millis = 0
        if isinstance(start, bytes):
            millis = int(proto_fields(start).get(1, [0])[0])  # Timestamp.millis_since_epoch
        view = f.get(1, [b""])[0]
        assert isinstance(view, bytes)
        desc = mf.get(3, [b""])[0]
        op = {
            "view": view.hex(),
            "parents": [p.hex() for p in f.get(2, []) if isinstance(p, bytes)],
            "meta": meta,
            "preds": tuple(sorted(p for p in f.get(4, []) if isinstance(p, bytes))),
            "millis": millis,
            "desc": desc.decode(errors="replace") if isinstance(desc, bytes) else "",
        }
        self._cache[op_id] = op
        return op

    def content_key(self, op_id: str) -> tuple[Any, ...]:
        op = self.read_op(op_id)
        return (op["view"], op["meta"], len(op["parents"]), op["preds"])

    def reachable(self, heads: list[str]) -> dict[str, dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        stack = list(heads)
        while stack:
            oid = stack.pop()
            if oid in seen:
                continue
            op = self.read_op(oid)
            seen[oid] = op
            stack.extend(p for p in op["parents"] if p not in seen)
        return seen


# ---------------------------------------------------------------- workspaces

def workspace_dirs(repo: Path) -> list[Path]:
    """Every working copy whose .jj/repo points at this store: the colocated git
    worktree list (covers /tmp without walking it) plus the conventional roots."""
    dirs: set[Path] = {repo}
    out = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                         capture_output=True, text=True).stdout
    dirs.update(Path(l.split(" ", 1)[1]) for l in out.splitlines() if l.startswith("worktree "))
    for root in [Path.home() / ".worktrees" / repo.name, repo / ".worktrees"]:
        if root.is_dir():
            for d in root.iterdir():
                dirs.add(d)
                if d.is_dir() and not d.is_symlink():
                    dirs.update(c for c in d.iterdir() if c.is_dir())
    store = (repo / ".jj/repo").resolve()
    found: list[Path] = []
    for d in sorted(dirs):
        rp = d / ".jj/repo"
        try:
            if not (d / ".jj/working_copy/checkout").is_file():
                continue
            if rp.is_dir():
                target = rp.resolve()
            elif rp.is_file():
                target = (d / ".jj" / rp.read_text().strip()).resolve()
            else:
                continue
        except OSError:
            continue
        if target == store:
            found.append(d)
    return found


def read_checkout(ws: Path) -> tuple[str, bytes]:
    f = proto_fields((ws / ".jj/working_copy/checkout").read_bytes())
    op = f[2][0]
    name = f.get(3, [b""])[0]
    assert isinstance(op, bytes) and isinstance(name, bytes)
    return op.hex(), name


def write_checkout_locked(ws: Path, new_op: str, expect_old: str, timeout_s: float = 10.0) -> str:
    """Rewrite the checkout op id under jj's working-copy lock. Returns a status word.

    Mirrors jj's `FileLock` (lib/src/lock/unix.rs): open-or-create the lock file, flock
    it, and if the inode was unlinked meanwhile (a holder's Drop removes the file
    before unlocking) start over; on release unlink first, then unlock."""
    state = ws / ".jj/working_copy"
    lock_path = state / "working_copy.lock"
    deadline = time.time() + timeout_s
    while True:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            if time.time() >= deadline:
                return "lock-busy"
            time.sleep(0.2)
            continue
        if os.fstat(fd).st_nlink == 0:
            os.close(fd)
            continue
        break
    try:
        cur, name = read_checkout(ws)
        if cur != expect_old:
            return "changed-under-us"  # the workspace moved on its own; re-evaluated next pass
        tmp = tempfile.NamedTemporaryFile(dir=state, delete=False)
        try:
            tmp.write(encode_checkout(bytes.fromhex(new_op), name))
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, state / "checkout")
        except BaseException:
            os.unlink(tmp.name)
            raise
        return "rewritten"
    finally:
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ---------------------------------------------------------------- driver

class Ledger:
    def __init__(self, path: str | None):
        self.fh = open(path, "a") if path else None

    def write(self, **kw: Any) -> None:
        kw["t"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        line = json.dumps(kw, default=str)
        print(line, file=sys.stderr)
        if self.fh:
            self.fh.write(line + "\n")
            self.fh.flush()


def jj(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["jj", "-R", str(repo), *JJ_FLAGS, *args], capture_output=True, text=True)


def pick_abandon_root(store: OpStore, heads: list[str], cutoff_millis: int) -> str | None:
    """Newest operation (by `jj op log` order from the current head) older than the
    cutoff. Its ancestors are what `jj op abandon ..X` drops. A current head is never
    chosen (jj refuses to abandon it), nor the virtual root."""
    r = jj(store.repo, "op", "log", "--no-graph", "-T", 'id ++ "\\n"')
    if r.returncode != 0:
        raise RuntimeError(f"jj op log failed: {r.stderr}")
    for oid in r.stdout.split():
        if oid in heads or set(oid) == {"0"}:
            continue
        if store.read_op(oid)["millis"] < cutoff_millis:
            return oid
    return None


def io_full_avg10() -> float:
    for line in Path("/proc/pressure/io").read_text().splitlines():
        if line.startswith("full"):
            return float(line.split()[1].split("=")[1])
    raise RuntimeError("no `full` line in /proc/pressure/io")


def jj_processes() -> dict[int, float]:
    """pid -> process start time (epoch seconds) for every running `jj` binary. Only
    `jj` reads the op store on this box (omp's jj plugin and knives shell out to it)."""
    out = subprocess.run(["pgrep", "-x", "jj"], capture_output=True, text=True).stdout.split()
    boot = 0.0
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("btime"):
            boot = float(line.split()[1])
    hz = os.sysconf("SC_CLK_TCK")
    procs: dict[int, float] = {}
    for pid in out:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            continue
        rest = stat.rsplit(")", 1)[1].split()  # fields after comm: state is [0], starttime is [19]
        if rest[0] in ("Z", "X"):
            continue  # a zombie holds no repo handle; an unreaped one lived here for hours (measured)
        procs[int(pid)] = boot + int(rest[19]) / hz
    return procs


def sweep(store: OpStore, ledger: Ledger, io_limit: float, min_age_s: float = 0.0) -> None:
    """Delete op/view files unreachable from the heads whose mtime predates the walk
    (jj's SimpleOpStore::gc rule with keep_newer=now), optionally only if older than
    min_age_s. Reachability is recomputed here, never trusted from an earlier step."""
    t0 = time.time()
    heads = store.heads()
    reach = store.reachable(heads)
    keep_views = {op["view"] for op in reach.values()}
    removed_ops = removed_views = 0
    bytes_ops = bytes_views = 0
    n = 0
    for d, keep, kind in ((store.ops_dir, set(reach), "op"), (store.views_dir, keep_views, "view")):
        for p in d.iterdir():
            if p.name in keep:
                continue
            try:
                st = p.stat()
                if st.st_mtime > t0 - min_age_s:
                    continue
                p.unlink()
            except FileNotFoundError:
                continue
            if kind == "op":
                removed_ops += 1
                bytes_ops += st.st_size
            else:
                removed_views += 1
                bytes_views += st.st_size
            n += 1
            if n % 500 == 0:
                while io_full_avg10() >= io_limit:
                    time.sleep(5)
    ledger.write(action="sweep", heads=[h[:12] for h in heads], reachable_ops=len(reach),
                 removed_ops=removed_ops, removed_views=removed_views,
                 freed_gb=round((bytes_ops + bytes_views) / 1e9, 2),
                 recovery="none: abandoned operations are gone by design; `jj undo` cannot reach past them")


def oldest_kept_op(store: OpStore, reach: dict[str, dict[str, Any]]) -> str | None:
    """The reparented chain's first operation (its only parent is the root). A workspace
    whose recorded operation fell inside the abandoned range is pointed here: jj then
    sees an ancestor of the current head and applies its ordinary freshness rule
    (tree unchanged -> fresh; changed by someone else -> the normal `jj workspace
    update-stale`), instead of failing on a sibling or a missing operation."""
    roots = [oid for oid, op in reach.items() if op["parents"] and all(set(p) == {"0"} for p in op["parents"])]
    return min(roots, key=lambda o: store.read_op(o)["millis"]) if roots else None


def fixup_workspaces(store: OpStore, mapping: dict[str, str], new_reach: dict[str, Any], ledger: Ledger, apply: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    fallback = oldest_kept_op(store, new_reach)
    for ws in workspace_dirs(store.repo):
        try:
            cur, name = read_checkout(ws)
        except (OSError, KeyError, AssertionError) as e:
            counts["unreadable"] = counts.get("unreadable", 0) + 1
            ledger.write(action="checkout-unreadable", workspace=str(ws), error=repr(e))
            continue
        if cur in new_reach:
            status = "current"
        elif cur in mapping or fallback:
            target = mapping.get(cur, fallback)
            assert target is not None
            status = write_checkout_locked(ws, target, cur) if apply else "would-rewrite"
            ledger.write(action="checkout-remap", workspace=str(ws), name=name.decode(errors="replace"),
                         old_op=cur[:12], new_op=target[:12], status=status,
                         how="reparented copy" if cur in mapping else "oldest kept operation (recorded one was abandoned)")
        else:
            status = "unmapped"
            ledger.write(action="checkout-unmapped", workspace=str(ws), name=name.decode(errors="replace"), op=cur[:12])
        counts[status] = counts.get(status, 0) + 1
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--keep-days", type=float, default=3.0, help="abandon operations older than this")
    ap.add_argument("--settle-seconds", type=float, default=300.0,
                    help="how long the store must stay free of re-attached old operations before the sweep")
    ap.add_argument("--max-iterations", type=int, default=8)
    ap.add_argument("--io-limit", type=float, default=20.0, help="pause the sweep while /proc/pressure/io full avg10 is at or above this")
    ap.add_argument("--max-process-wait", type=float, default=1800.0,
                    help="seconds to wait for jj processes that predate the abandon to exit before sweeping; "
                         "past this the sweep is deferred (rerun with --sweep-only later)")
    ap.add_argument("--sweep-only", action="store_true",
                    help="skip abandon/remap; delete files left unreachable by an earlier run, provided they are "
                         "older than --sweep-min-age and no jj process has been alive that long")
    ap.add_argument("--sweep-min-age", type=float, default=3600.0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ledger")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    store = OpStore(repo)
    ledger = Ledger(args.ledger)
    cutoff_millis = int((time.time() - args.keep_days * 86400) * 1000)

    if args.sweep_only:
        old_procs = {pid: t for pid, t in jj_processes().items() if time.time() - t > args.sweep_min_age}
        if old_procs:
            ledger.write(action="sweep-refused", reason="jj processes older than --sweep-min-age are running",
                         pids=sorted(old_procs))
            sys.exit(1)
        if args.apply:
            sweep(store, ledger, args.io_limit, min_age_s=args.sweep_min_age)
        else:
            heads = store.heads()
            reach = store.reachable(heads)
            keep_views = {op["view"] for op in reach.values()}
            ledger.write(action="sweep-plan", unreachable_ops=len({p.name for p in store.ops_dir.iterdir()} - set(reach)),
                         unreachable_views=len({p.name for p in store.views_dir.iterdir()} - keep_views))
        return

    heads0 = store.heads()
    reach0 = store.reachable(heads0)
    abandon_root = pick_abandon_root(store, heads0, cutoff_millis)
    if abandon_root is None:
        ledger.write(action="nothing-to-abandon", heads=heads0, reachable_ops=len(reach0))
        return
    old_side = store.reachable([abandon_root])
    all_ops = {p.name for p in store.ops_dir.iterdir()}
    all_views = {p.name for p in store.views_dir.iterdir()}
    reach_views = {op["view"] for op in reach0.values()}
    ledger.write(action="plan", repo=str(repo), heads=[h[:12] for h in heads0],
                 reachable_ops=len(reach0), abandon_root=abandon_root[:12],
                 abandon_root_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(store.read_op(abandon_root)["millis"] / 1000)),
                 ops_to_abandon=len(old_side), ops_kept=len(reach0) - len(old_side),
                 op_files=len(all_ops), view_files=len(all_views),
                 already_unreachable_ops=len(all_ops - set(reach0)),
                 already_unreachable_views=len(all_views - reach_views),
                 workspaces=len(workspace_dirs(repo)))
    if not args.apply:
        fixup_workspaces(store, {}, reach0, Ledger(None), apply=False)
        return

    # Iterate: abandon, remap, detect re-attachment.
    abandoned_ids: set[str] = set()          # every old-chain op id we have abandoned so far
    known_old: dict[str, dict[str, Any]] = dict(reach0)  # ops that existed before each abandon
    target = abandon_root
    procs_at_abandon: dict[int, float] = {}
    for iteration in range(1, args.max_iterations + 1):
        heads_before = store.heads()
        procs_at_abandon.update(jj_processes())  # any of these may have loaded a soon-abandoned head
        before = store.reachable(heads_before)
        known_old.update(before)
        r = jj(repo, "op", "abandon", f"..{target}")
        if r.returncode != 0:
            ledger.write(action="abandon-failed", iteration=iteration, target=target[:12], stderr=r.stderr.strip())
            sys.exit(1)
        abandoned_ids |= {o for o in store.reachable([target]) if set(o) != {"0"}}
        heads_after = store.heads()
        after = store.reachable(heads_after)
        ledger.write(action="abandon", iteration=iteration, target=target[:12], stdout=r.stdout.strip(),
                     heads_before=[h[:12] for h in heads_before], heads_after=[h[:12] for h in heads_after],
                     reachable_before=len(before), reachable_after=len(after))
        # content map: old (pre-abandon reachable, now not) -> reparented copy
        by_key: dict[tuple[Any, ...], str] = {}
        for oid in after:
            if oid not in known_old:
                by_key[store.content_key(oid)] = oid
        mapping = {oid: by_key[store.content_key(oid)] for oid in known_old
                   if oid not in after and store.content_key(oid) in by_key}
        counts = fixup_workspaces(store, mapping, after, ledger, apply=True)
        ledger.write(action="fixup", iteration=iteration, mapped_ops=len(mapping), **counts)
        # Settle: the old chain comes back when a jj process that loaded a pre-abandon head
        # commits its operation (its parent is the old head; the next load reconciles the
        # two heads with a merge). Two conditions end the settle: no re-attachment for
        # --settle-seconds, and no jj process that predates the abandon is still alive
        # (same pid + same start time = same process). Re-attachment re-iterates.
        settled_since = time.time()
        reattached: list[str] = []
        while True:
            time.sleep(min(20.0, args.settle_seconds))
            cur = store.reachable(store.heads())
            reattached = sorted(oid for oid in cur if oid in abandoned_ids)
            if reattached:
                break
            # a concurrent op may also have landed at a stale checkout id: remap again
            counts = fixup_workspaces(store, mapping, cur, Ledger(None), apply=True)
            now_procs = jj_processes()
            lingering = sorted(pid for pid, t in procs_at_abandon.items() if now_procs.get(pid) == t)
            if lingering:
                if time.time() - settled_since > args.max_process_wait:
                    ledger.write(action="sweep-deferred", reason="jj processes from before the abandon still running",
                                 pids=lingering, hint="rerun with --sweep-only --apply once they have exited")
                    return
                continue
            if time.time() - settled_since >= args.settle_seconds:
                break
        if not reattached:
            ledger.write(action="settled", iteration=iteration, seconds=round(time.time() - settled_since))
            break
        # the re-attached old chain hangs off one of the pre-abandon heads; abandon it again
        heads_in = [h for h in heads_before if h in reattached]
        target = heads_in[0] if heads_in else max(reattached, key=lambda o: store.read_op(o)["millis"])
        ledger.write(action="reattached", iteration=iteration, count=len(reattached), next_target=target[:12])
    else:
        ledger.write(action="gave-up", iterations=args.max_iterations)
        sys.exit(1)

    sweep(store, ledger, args.io_limit)


if __name__ == "__main__":
    main()
