#!/usr/bin/env python3
"""Mechanical half of the disk-hygiene skill. Stdlib only; safe to run anywhere.

Subcommands (all read-only except `apply`, and `tmp --apply`):

  procs                          JSON list of every process's cwd (liveness evidence)
  protected --fixed F [--knives-registry T]
                                 regenerate the protected set (D4): fixed entries ∪
                                 /proc cwds (as anchors) ∪ container bind-mount
                                 sources ∪ knives checkouts, realpath-normalised;
                                 a missing source fails loudly, never shrinks the set
  inventory --repo R [--root D]  every working copy of jj repo R found under roots D
                                 (default: $HOME), classified against trunk/remotes
  plan --inventory I --protected P [--fresh-hours H] [--skip-pushed] [--releases R]
                                 turn an inventory (and/or an owner-release file)
                                 into a deletion plan JSON; every reapable class is
                                 freshness-gated and refusals are reported, not dropped
  apply --plan P --protected PR --ledger L [--io-limit PCT] [--releases R]
                                 paced executor: one item at a time, ionice idle,
                                 single-instance per store (flock), re-verifies each
                                 item's class and disk-vs-store divergence before acting,
                                 and refuses a removal another clone's objects/info/alternates
                                 borrows from (one scan of the inventory roots and $HOME)
  release-capture --path P --repo R --kind git|jj [--name N] [--releases R]
                                 print an owner-release entry binding the slot's
                                 identity (HEAD hash + worktree admin id + dir mtime);
                                 --releases appends it to the releases file (locked)
  tmp --dir D --older-than-hours H [--families REGEX] [--protected P] [--apply --ledger L]
                                 stale temp-dir families by the newest mtime in each directory
  containers                     every container with family (platform-e2e, local-stack, ryuk,
                                 tc-fixture, agentbox, other), owner attribution, liveness verdict
  images [--older-than-hours H]  tagged images no container uses, neither built nor pulled in the
                                 last H (cheap; avoids `docker system df -v`, which stalls under IO load)

Instruments — measure the box now; each prints what its number does NOT measure:

  writers [--seconds S] [--top N] [--argv]
                                 processes by write rate over S seconds (default 5), each with
                                 its working directory, beside its whole-life average
  cpu [--seconds S] [--top N]    CPU over S seconds grouped by COMMAND NAME, not by process
  fdwrites [--seconds S] [--pid P] [--top N] [--all-modes]
                                 open files whose position or size moved, which names the file
                                 `writers` cannot
  dangling ROOT... [--list] [--top N] [--max-entries N]
                                 symlinks whose target is gone (`find -xtype l`), by directory
  sizes ROOT... [--max-depth D] [--top N] [--hardlinks]
                                 directories by the bytes that are theirs alone, from ONE du over
                                 every root, so the rows can be added up and a hardlinked inode is
                                 counted once; --hardlinks measures what separate runs double-count
  recent ROOT... [--since 2h] [--group-depth N] [--use-mtime]
                                 files whose ctime is inside the window, counted whatever their size

Protected file format: {"protected": ["/abs/path", ...], "anchors": ["/abs/path", ...]}.
"protected" has prefix semantics both ways: an item is protected if it is under a
protected path OR a protected path is under it. "anchors" (optional) are live-location
evidence — a process cwd — and protect only the tree they stand IN (equal or deeper):
/, $HOME and /tmp are live cwds on every box (measured 2026-09-15), so subtree
semantics for them would blanket-protect every slot and neuter the reaper. Residual
stated in the plan (D4): a slot driven only via `jj -R` from elsewhere has no cwd
inside it and is protected by the idle threshold alone. Plan items may set
"subpath_release": true to bypass the prefix guard for a path an owner explicitly
released inside a live tree; the live-process check still applies.

Releases file format: {"releases": [entry, ...]} with entries printed (and, with
--releases, appended) by `release-capture`. Entries are one-shot: `apply` retires an
entry in place (adds a "retired" field) once it acts on it; `plan` only reports and
never consumes one. Every mutation of the file (retirement, append) holds an
exclusive flock on the file itself, so concurrent mutators never lose updates.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

JJ = ["jj", "--ignore-working-copy", "--config", "fsmonitor.backend=none"]
RM = ["sudo", "ionice", "-c3", "nice", "-n19", "rm", "-rf", "--one-file-system"]
WALK_HIDDEN = (".worktrees", ".omp", ".local", ".cache")  # dot-dirs the root walks descend into
WALK_DEPTH = 6


# ---------------------------------------------------------------- helpers

def run(cmd: list[str], cwd: str | None = None, timeout: int = 600) -> tuple[int, str]:
    """Return (rc, stdout). stderr is appended only on failure so parsers never see jj warnings."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    out = r.stdout.strip()
    if r.returncode != 0:
        out = (out + "\n" + r.stderr.strip()).strip()
    return r.returncode, out


def proc_cwds() -> set[str]:
    out: set[str] = set()
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                out.add(os.readlink(f"/proc/{pid}/cwd"))
            except OSError:
                pass
    return out


_REF_SPLIT = re.compile(r"[:=,\s]")


def proc_path_refs() -> tuple[list[tuple[int, str]], int]:
    """((pid, path) for every absolute path a readable process depends on, unreadable count).
    A process depends on a directory through its cwd, a PATH-style or other env value, or an
    argument: an orphan test loop whose fake kubectl/tmux lived in /tmp/tmp.X/bin had its cwd
    elsewhere, a cwd-only check cleared the dir, and its next kubectl resolved to the REAL
    binary (SRE, 2026-09-26). OLDPWD is a record of where a shell was, not a dependency. A
    non-root reader gets EPERM for other users' processes and PID 1, so the census is partial
    and says how partial rather than reporting a clean result."""
    refs: list[tuple[int, str]] = []
    unreadable, me = 0, os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        n = int(pid)
        try:
            refs.append((n, os.readlink(f"/proc/{pid}/cwd").removesuffix(" (deleted)")))
            env = Path(f"/proc/{pid}/environ").read_bytes()
            argv = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            unreadable += 1
            continue
        values = [p.split(b"=", 1)[1] for p in env.split(b"\0") if b"=" in p and not p.startswith(b"OLDPWD=")]
        for raw in values + argv.split(b"\0"):
            for tok in _REF_SPLIT.split(raw.decode(errors="replace")):
                if tok.startswith("/") and len(tok) > 1:
                    refs.append((n, tok.rstrip("/")))
    return refs, unreadable


def refs_under(path: str, refs: list[tuple[int, str]]) -> list[int]:
    return sorted({pid for pid, r in refs if r == path or r.startswith(path + "/")})


def procs_under(path: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for pid in refs_under(path, proc_path_refs()[0]):
        try:
            hits.append((pid, Path(f"/proc/{pid}/comm").read_text().strip()))
        except OSError:
            hits.append((pid, "?"))
    return hits


def io_full_avg10() -> float:
    for line in Path("/proc/pressure/io").read_text().splitlines():
        if line.startswith("full"):
            return float(line.split("avg10=")[1].split()[0])
    return 0.0


def living_ancestor(path: str) -> str:
    """The nearest path that exists, so a filesystem reading survives the removal it measures."""
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return "/"
        path = parent
    return path


def df_used_gb(path: str = "/") -> float:
    """Used GB of the filesystem that holds <path>. A reading of `/` is true of `/` alone: a
    removal under another mount frees nothing there, and the ledger then shows no change."""
    st = os.statvfs(living_ancestor(path))
    return round((st.f_blocks - st.f_bfree) * st.f_frsize / 1e9, 1)


def df_free_gb(path: str = "/") -> float:
    """Space available to an unprivileged writer on the filesystem that holds <path>, which is
    what a reclaim threshold decides on: f_bavail excludes the reserved blocks that f_bfree
    counts and nobody here can use."""
    st = os.statvfs(living_ancestor(path))
    return round(st.f_bavail * st.f_frsize / 1e9, 1)


def mount_point(path: str) -> str:
    """The mount point of the filesystem that holds <path>, so a space reading names the disk
    it is true of: a removal under another mount frees nothing on `/`."""
    path = living_ancestor(os.path.abspath(path))
    dev = os.lstat(path).st_dev
    while path != "/":
        parent = os.path.dirname(path)
        if os.lstat(parent).st_dev != dev:
            return path
        path = parent
    return "/"


def ledger_write(path: Path, entry: dict[str, Any]) -> None:
    """One JSONL line, with the used space of the filesystem the entry's own path lives on."""
    target = str(entry.get("path") or "/")
    entry["t"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry["df_used_gb"] = df_used_gb(target)
    entry["df_mount"] = mount_point(target)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    print(json.dumps(entry), flush=True)


def is_protected(path: str, protected: list[str], anchors: list[str] | None = None) -> str | None:
    for d in protected:
        if path == d or path.startswith(d + "/") or d.startswith(path + "/"):
            return d
    # An anchor (a live process cwd) protects the tree it stands IN — equal or deeper —
    # never its own subtree: /, $HOME and /tmp are live cwds on every box (measured
    # 2026-09-15), and subtree semantics for them would blanket-protect every slot.
    # Residual stated in the plan (D4): a slot driven only via `jj -R` from elsewhere
    # has no cwd inside it and is protected by the idle threshold alone.
    for a in anchors or []:
        if a == path or a.startswith(path + "/"):
            return a
    return None


def load_protected(path: str | None) -> tuple[list[str], list[str]]:
    """(protected, anchors) from a protected file; "anchors" is optional in the file."""
    if not path:
        return [], []
    data: dict[str, Any] = json.load(open(path))
    return [str(p) for p in data["protected"]], [str(p) for p in data.get("anchors", [])]


# jj stores the workspace name in .jj/working_copy/checkout as protobuf field 3.
def _varint(b: bytes, i: int) -> tuple[int, int]:
    shift = val = 0
    while True:
        c = b[i]
        i += 1
        val |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return val, i


def jj_workspace_name(dirpath: str) -> str | None:
    p = Path(dirpath) / ".jj" / "working_copy" / "checkout"
    if not p.is_file():
        return None
    raw = p.read_bytes()
    i = 0
    while i < len(raw):
        key, i = _varint(raw, i)
        field, wire = key >> 3, key & 7
        if wire == 2:
            ln, i = _varint(raw, i)
            data = raw[i : i + ln]
            i += ln
            if field == 3:
                return data.decode()
        elif wire == 0:
            _, i = _varint(raw, i)
        else:
            return None
    return None


# --------------------------------------------- protected-set generator (D4)

def docker_bind_sources() -> set[str]:
    """Bind-mount sources of every container (incl. stopped: a stopped container can be
    restarted onto its mounts). A docker failure is fatal: a protected set silently
    missing this source would unprotect every bind-mounted slot."""
    rc, cids = run(["timeout", "120", "docker", "ps", "-aq", "--no-trunc"])
    if rc != 0:
        sys.exit(f"protected: docker ps failed (rc={rc}): {cids[-400:]}")
    if not cids.strip():
        return set()
    # rc is 1 when any id vanished between ps and inspect; stdout is still one JSON array
    # (same measured contract as cmd_containers).
    r = subprocess.run(["timeout", "120", "docker", "inspect", *cids.split()], capture_output=True, text=True)
    if not r.stdout.strip().startswith("["):
        sys.exit(f"protected: docker inspect returned no JSON array (rc={r.returncode}): {r.stderr[-400:]}")
    data: list[dict[str, Any]] = json.loads(r.stdout)
    out: set[str] = set()
    for d in data:
        for m in d.get("Mounts") or []:
            if m.get("Type") == "bind" and m.get("Source"):
                out.add(os.path.realpath(str(m["Source"])))
    return out


def knives_checkouts(registry: str) -> set[str]:
    """Every knives-managed checkout location. `knives repos --json` supplies the repo
    paths; a default-layout path (<parent>/default) contributes its PARENT, which also
    covers the sibling per-branch workspace slots knives opens next to default/. A bare
    path (the checkout itself, e.g. ~/oh-my-pi) contributes only itself — its parent is
    $HOME. Workspace dirs configured per repo (`workspaces = ...`) appear only in the
    registry file, not in the CLI output (measured 2026-09-15, knives 2.0.11), so the
    registry is read too. Any missing piece is fatal: a set silently missing this
    source would expose every fork checkout."""
    if shutil.which("knives") is None:
        sys.exit("protected: knives not on PATH — refusing to emit a protected set with the knives source missing")
    # Measured (knives 2.0.11): exits 3 when the forge is unreachable while stdout still
    # carries the complete local repo list — judge the JSON, not the exit code.
    r = subprocess.run(["knives", "repos", "--json"], capture_output=True, text=True, timeout=600)
    try:
        doc: dict[str, Any] = json.loads(r.stdout)
        repos: list[dict[str, Any]] = doc["repos"]
    except (json.JSONDecodeError, KeyError):
        sys.exit(f"protected: `knives repos --json` output unparseable (rc={r.returncode}): {r.stderr[-400:]}")
    out: set[str] = set()
    for repo in repos:
        p = repo.get("path")
        if not p:
            continue  # registered but not checked out on this machine (measured: path is null)
        rp = os.path.realpath(str(p))
        out.add(os.path.dirname(rp) if os.path.basename(rp) == "default" else rp)
    with open(os.path.expanduser(registry), "rb") as f:
        reg: dict[str, Any] = tomllib.load(f)
    repos_cfg: dict[str, Any] = reg.get("repos", {})
    for cfg in repos_cfg.values():
        ws = cfg.get("workspaces")
        if ws:
            out.add(os.path.realpath(os.path.expanduser(str(ws))))
    return out


def cmd_protected(args: argparse.Namespace) -> None:
    """D4: regenerate the protected set for this run. Liveness is measured, never asked:
    fixed entries ∪ container bind-mount sources ∪ knives checkouts (both-way prefix
    protection) plus every process cwd as an anchor (protects only the tree it stands
    in — see is_protected). Every source either contributes or kills the run."""
    fixed_protected, fixed_anchors = load_protected(args.fixed)
    binds = docker_bind_sources()
    knives = knives_checkouts(args.knives_registry)
    # A cwd whose directory was deleted reads as "<path> (deleted)"; keep the path —
    # protecting a recreated slot some process once stood in is conservative-safe.
    cwds = {os.path.realpath(c.removesuffix(" (deleted)")) for c in proc_cwds()}
    protected = sorted({os.path.realpath(p) for p in fixed_protected} | binds | knives)
    anchors = sorted({os.path.realpath(a) for a in fixed_anchors} | cwds)
    json.dump({
        "protected": protected,
        "anchors": anchors,
        "sources": {"fixed_protected": len(fixed_protected), "fixed_anchors": len(fixed_anchors),
                    "docker_bind_sources": len(binds), "knives": len(knives), "proc_cwds": len(cwds)},
        "generated_t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, sys.stdout, indent=1)
    print(f"\n{len(protected)} protected, {len(anchors)} anchors", file=sys.stderr)


# ---------------------------------------------------------------- inventory

def observable_root(box_dir: str) -> str | None:
    """The only tree under which this process can judge a path ABSENT, or None for everywhere.

    Inside an agentbox, other boxes' checkouts, the host's ~/.worktrees and the host's /tmp are
    invisible, so every one of their registrations looks exactly like a stale one. Measured
    2026-09-25 09:15Z: a prune from inside a box took 13 registrations of the shared agent-c
    store, four of them live worktrees in other boxes and on the host. `auto` = the box's own
    directory (~/boxes/<hostname>) when /etc/agentbox-identity exists, else None (the host sees
    everything); `none` forces host semantics; any other value is taken as the directory."""
    if box_dir == "none":
        return None
    if box_dir == "auto":
        if not Path("/etc/agentbox-identity").exists():
            return None
        return str(Path.home() / "boxes" / os.uname().nodename)
    return str(Path(box_dir).resolve())


class TreeScan(NamedTuple):
    holds_working_copy: bool | None  # None = the scan could not finish
    newest_mtime: float | None  # None = not established (scan unfinished, or stopped at a working copy)
    dirs: int


def scan_tree(path: Path, depth: int = 3, dir_cap: int = 5000) -> TreeScan:
    """One bounded walk of <path>: does a `.jj` or `.git` exist at or below it, down to <depth>
    levels, and what is the newest mtime in that same bounded set?

    `holds_working_copy` is None when the walk could not finish (over <dir_cap> directories, or
    an unreadable one): unverifiable, so never scratch. A depth-1 check planned
    ~/.worktrees/<repo>/ containers of other repos' worktrees as scratch rm (8 of them, SRE,
    2026-09-26). Raises OSError only for <path> itself.

    `newest_mtime` is set only when the whole bounded set was read, so no caller dates a tree
    from a part of it. A directory's own mtime moves only when an entry is added or removed, so
    the top-level stat this replaces called a tree stale while its files were being written: on
    2026-09-26 a knives container read idle 160-280 h with live branches under it. What the walk
    sees is the top <depth> levels, so a file written deeper does not move the reading, and the
    idle time derived from it is an UPPER bound on how stale the tree is. It is an age, never
    proof that nothing writes there: the live-process check and `writers` prove that."""
    frontier: list[tuple[Path, int]] = [(path, 0)]
    seen = 0
    newest = os.lstat(path).st_mtime
    while frontier:
        d, level = frontier.pop()
        seen += 1
        if seen > dir_cap:
            return TreeScan(None, None, seen)
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            if d == path:
                raise
            return TreeScan(None, None, seen)
        for e in entries:
            if e.name in (".jj", ".git"):
                return TreeScan(True, None, seen)
            try:
                newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
            except OSError:
                continue
            if level < depth and e.is_dir(follow_symlinks=False):
                frontier.append((Path(e.path), level + 1))
    return TreeScan(False, newest, seen)


def cmd_inventory(args: argparse.Namespace) -> None:
    repo = str(Path(args.repo).resolve())
    obs = observable_root(args.box_dir)
    repo_store = str(Path(repo) / ".jj" / "repo")
    roots = args.root or [str(Path.home())]
    rc, out = run(JJ + ["workspace", "list"], cwd=repo)
    registered = {line.split(":")[0] for line in out.splitlines() if ":" in line}
    trunk = args.trunk

    # jj working copies pointing at this repo store
    dirs: dict[str, str | None] = {}
    for root in roots:
        for dp, dn, _fn in os.walk(root):
            if ".jj" in dn:
                pointer = Path(dp) / ".jj" / "repo"
                if pointer.is_file():
                    target = str((Path(dp) / ".jj" / pointer.read_text().strip()).resolve())
                    if target == repo_store and dp != repo:
                        dirs[dp] = jj_workspace_name(dp)
                dn[:] = [d for d in dn if d == ".worktrees"]
                continue
            dn[:] = [d for d in dn if not d.startswith(".") or d in WALK_HIDDEN]
            if dp.count("/") - root.count("/") >= args.max_depth:
                dn[:] = []
    # git worktrees registered on the colocated repo (covers /tmp etc.)
    rc, out = run(["git", "-C", repo, "worktree", "list", "--porcelain"])
    git_wts = [line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("worktree ")]
    for p in git_wts:
        if p != repo and p not in dirs:
            dirs[p] = jj_workspace_name(p) if os.path.isdir(p) else None

    live_refs, unreadable = proc_path_refs()
    live = {r for _, r in live_refs}
    print(f"liveness census: {unreadable} processes unreadable (other uid / PID 1) - partial", file=sys.stderr)
    rows: list[dict[str, Any]] = []
    for path, name in sorted(dirs.items()):
        exists = os.path.isdir(path)
        row: dict[str, Any] = {"path": path, "name": name, "exists": exists, "is_git_worktree": path in git_wts}
        row["live_procs"] = sorted({c for c in live if c == path or c.startswith(path + "/")})[:3]
        ts = Path(path) / ".jj" / "working_copy" / "tree_state"
        row["jj_idle_hours"] = round((time.time() - ts.stat().st_mtime) / 3600, 1) if ts.exists() else None
        if name and name in registered:
            rc, at = run(JJ + ["log", "-r", f"{name}@", "--no-graph", "-T", 'empty ++ "|" ++ description.first_line()'], cwd=repo)
            rc, um = run(
                JJ + ["log", "-r", f"(::{name}@ ~ ::{trunk}) ~ empty()", "--no-graph", "-T",
                      'commit_id.short() ++ "|" ++ self.contained_in("::remote_bookmarks()") ++ "|" ++ bookmarks ++ "\\n"'],
                cwd=repo,
            )
            um_rows = [line.split("|", 2) for line in um.splitlines() if line.count("|") >= 2]
            row.update(
                registered=True,
                at_empty=at.split("|")[0] == "true",
                unmerged=len(um_rows),
                unpushed=sum(1 for r in um_rows if r[1] == "false"),
                bookmarks=sorted({b for r in um_rows for b in r[2].split() if b}),
            )
        elif name is None and (Path(path) / ".git").exists() and exists:
            # HEAD reachable from some remote ref? (`--branches` would see every jj-exported branch, not this worktree)
            rc, lo = run(["git", "-C", path, "log", "HEAD", "--not", "--remotes", "--oneline", "-n", "1"], timeout=120)
            row.update(registered=False, git_only=True, head_unpushed=bool(lo.strip()) if rc == 0 else None)
        else:
            row.update(registered=False)
        # classification
        if row["live_procs"]:
            cls = "LIVE"
        elif not exists:
            # Absence is evidence only where this process can see: outside `obs`, a live
            # workspace and a stale one are indistinguishable from here.
            cls = "STALE_REGISTRATION" if obs is None or path == obs or path.startswith(obs + "/") else "UNOBSERVABLE"
        elif not row.get("registered"):
            cls = "UNREGISTERED" if not row.get("git_only") else ("GIT_UNPUSHED" if row.get("head_unpushed") else "GIT_HEAD_PUSHED")
        elif row["unmerged"] == 0:
            cls = "MERGED"
        elif row["unpushed"] == 0:
            cls = "PUSHED"
        else:
            cls = "UNPUSHED"
        row["class"] = cls
        rows.append(row)
    # Plain scratch directories under the roots. Until 2026-09-18 this inventory saw only
    # jj/git working copies, so /tmp's ordinary agent scratch was invisible BY CONSTRUCTION:
    # the night the box hit 91% used, /tmp held 1.3 TB of it (one dir, /tmp/forrest, was
    # 182 GB) while the hourly plan listed 49 workspace items and nothing else. Depth 1 only:
    # an agent's scratch is a top-level directory, and walking deeper turns one refusal into
    # thousands of rows.
    known = set(dirs)
    for root in roots:
        for entry in sorted(Path(root).glob("*")):
            path = str(entry)
            if not entry.is_dir() or entry.is_symlink() or path in known:
                continue
            if any(path == d or path.startswith(d + "/") or d.startswith(path + "/") for d in known):
                continue  # a workspace lives here (or under here): its own row owns it
            try:
                scan = scan_tree(entry)
            except OSError:
                continue  # unreadable (another user's dir, EACCES): no evidence, so no row (D1)
            held = scan.holds_working_copy
            # Idle is the newest mtime the bounded walk saw, not this directory's own: a
            # directory's mtime moves only when an entry is added or removed, so the top-level
            # stat this replaces read 160-280 h of idleness off trees being written to.
            idle_h = round((time.time() - scan.newest_mtime) / 3600, 1) if scan.newest_mtime else None
            live_procs = sorted({c for c in live if c == path or c.startswith(path + "/")})[:3]
            cls = ("LIVE" if live_procs else "HOLDS_WORKING_COPY" if held
                   else "SCRATCH_UNVERIFIED" if held is None else "SCRATCH")
            rows.append({"path": path, "name": None, "exists": True, "is_git_worktree": False,
                         "live_procs": live_procs, "jj_idle_hours": idle_h, "registered": False,
                         "idle_source": f"newest mtime in the top 3 levels ({scan.dirs} directories read)",
                         "class": cls})
    # registrations with no directory found
    found_names = {r["name"] for r in rows if r["name"]}
    for name in sorted(registered - found_names - {"default"}):
        # With no path to test, a registration the walk did not find is judgeable only on the
        # host; inside a box it is most often another box's live workspace.
        rows.append({"path": None, "name": name, "exists": False, "registered": True,
                     "class": "STALE_REGISTRATION" if obs is None else "UNOBSERVABLE"})
    json.dump({"repo": repo, "trunk": trunk, "roots": roots, "rows": rows}, sys.stdout, indent=1)
    print(file=sys.stderr)
    print(Counter(str(r["class"]) for r in rows), file=sys.stderr)


# ---------------------------------------------------------------- reaper helpers

RECOVER_NOTE = (
    "jj op revert {op} — restores the workspace registration only, never files; "
    "reverting a forget whose name was reused since is a silent no-op"
)


def find_op(repo: str, description: str, limit: int | None = None) -> tuple[str, int] | None:
    """Most recent op whose description matches exactly; (op_id, epoch_s) or None.

    Exact description match, never `-n1`: on a shared store a concurrent op can land
    between an action and its capture, so positional selection names the wrong op.
    """
    cmd = JJ + ["op", "log", "--no-graph", "-T", 'id.short() ++ "\\t" ++ time.start().format("%s") ++ "\\t" ++ description ++ "\\n"']
    if limit is not None:
        cmd += ["-n", str(limit)]
    rc, out = run(cmd, cwd=repo)
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[2] == description:
            return parts[0], int(parts[1])
    return None


def capture_forget_op(repo: str, name: str) -> str | None:
    hit = find_op(repo, f"forget workspace {name}", limit=50)  # forget op description is unquoted (measured)
    return hit[0] if hit else None


def jj_registered(repo: str) -> set[str] | None:
    rc, out = run(JJ + ["workspace", "list"], cwd=repo)
    if rc != 0:
        return None
    return {line.split(":")[0] for line in out.splitlines() if ":" in line}


def jj_merged_state(repo: str, name: str, trunk: str) -> dict[str, Any] | None:
    """Re-derive @-empty / unmerged / unpushed for <name>@; None = could not derive."""
    rc, at = run(JJ + ["log", "-r", f"{name}@", "--no-graph", "-T", 'empty ++ "|" ++ description.first_line()'], cwd=repo)
    if rc != 0:
        return None
    rc, um = run(
        JJ + ["log", "-r", f"(::{name}@ ~ ::{trunk}) ~ empty()", "--no-graph", "-T",
              'commit_id.short() ++ "|" ++ self.contained_in("::remote_bookmarks()") ++ "\\n"'],
        cwd=repo,
    )
    if rc != 0:
        return None
    rows = [line.split("|", 1) for line in um.splitlines() if "|" in line]
    return {"at_empty": at.split("|")[0] == "true", "unmerged": len(rows), "unpushed": sum(1 for r in rows if r[1] == "false")}


def jj_divergence(repo: str, name: str, path: str, limit: int = 5) -> list[str] | None:
    """Disk-vs-store divergence for <name>@ — modified/deleted/added tracked files and
    untracked/ignored content (Decision 1). None = unverifiable; callers must refuse."""
    rc, out = run(JJ + ["file", "list", "-r", f"{name}@"], cwd=repo)
    if rc != 0:
        return None
    tracked = {line for line in out.splitlines() if line}
    ondisk: set[str] = set()
    for dp, dn, fn in os.walk(path):
        if dp == path:
            dn[:] = [d for d in dn if d not in (".jj", ".git")]
            fn = [f for f in fn if f != ".git"]
        for d in list(dn):
            if os.path.islink(os.path.join(dp, d)):  # walk won't descend; the link itself is disk-only content
                ondisk.add(os.path.relpath(os.path.join(dp, d), path))
                dn.remove(d)
        for f in fn:
            ondisk.add(os.path.relpath(os.path.join(dp, f), path))
    diffs = [f"untracked:{p}" for p in sorted(ondisk - tracked)]
    diffs += [f"deleted:{p}" for p in sorted(tracked - ondisk)]
    for rel in sorted(tracked & ondisk):
        if len(diffs) >= limit:
            break
        full = os.path.join(path, rel)
        if os.path.islink(full):
            diffs.append(f"symlink:{rel}")  # `jj file show` cannot render a symlink (measured); refuse rather than guess
            continue
        r = subprocess.run(JJ + ["file", "show", "-r", f"{name}@", rel], cwd=repo, capture_output=True)
        if r.returncode != 0:
            return None
        if Path(full).read_bytes() != r.stdout:
            diffs.append(f"modified:{rel}")
    return diffs[:limit]


def git_head_pushed(path: str) -> bool | None:
    """Every commit reachable from HEAD reachable from some remote ref? None = could not derive."""
    rc, lo = run(["git", "-C", path, "log", "HEAD", "--not", "--remotes", "--oneline", "-n", "1"], timeout=120)
    if rc != 0:
        return None
    return not lo.strip()


def git_status_clean(path: str) -> tuple[bool, str]:
    """(clean, refusal reason). --ignored: gitignored content (a .env, a venv) exists only on
    disk and `worktree remove --force` destroys it (Decision 1); plain --porcelain omits it
    (measured: `!! .env` / `!! .venv/` — directories included — appear only with --ignored)."""
    rc, out = run(["git", "-C", path, "status", "--porcelain", "--ignored"], timeout=600)
    if rc != 0:
        return False, f"git status failed: {out[-200:]}"
    lines = out.strip().splitlines()
    if not lines:
        return True, ""
    cls = ("ignored content exists only on disk" if all(ln.startswith("!!") for ln in lines)
           else "dirty git worktree")
    return False, f"{cls} (status --porcelain --ignored non-empty): {out.strip()[:400]}"


def slot_identity(path: str, repo: str | None = None, name: str | None = None) -> dict[str, Any] | None:
    """A slot's identity: HEAD hash + worktree admin id + directory mtime. None = unreadable."""
    try:
        mtime = os.lstat(path).st_mtime
    except OSError:
        return None
    gitfile = Path(path) / ".git"
    if gitfile.is_file():
        pointer = gitfile.read_text().strip()
        if not pointer.startswith("gitdir:"):
            return None
        admin_id = os.path.basename(pointer.split(":", 1)[1].strip())
        rc, head = run(["git", "-C", path, "rev-parse", "HEAD"], timeout=120)
        if rc != 0:
            return None
        return {"head": head, "admin_id": admin_id, "mtime": mtime}
    ws = name or jj_workspace_name(path)
    if ws and repo:  # non-colocated jj workspace: the working-copy commit is the HEAD analogue
        rc, head = run(JJ + ["log", "-r", f"{ws}@", "--no-graph", "-T", "commit_id"], cwd=repo)
        if rc != 0:
            return None
        return {"head": head, "admin_id": ws, "mtime": mtime}
    return None


def apply_lock_path(repos: list[str]) -> Path:
    """Fixed per-store lock path so every apply entry point (timer or hand-run) contends."""
    key = "\n".join(sorted({str(Path(r).resolve()) for r in repos}))
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "disk-hygiene"
    state.mkdir(parents=True, exist_ok=True)
    return state / f"apply-{digest}.lock"


def load_releases(path: str) -> list[dict[str, Any]]:
    with open(path) as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        data: dict[str, Any] = json.load(f)
    return [dict(e) for e in data["releases"]]


def retire_release(path: str, entry: dict[str, Any], reason: str) -> None:
    """One-shot semantics: mark the acted-on entry retired, in place.

    The read-modify-write holds LOCK_EX on the releases file itself, so concurrent
    mutators (another apply's retirement, an operator's `release-capture --releases`
    append) serialize instead of losing updates. The rewrite is in place (seek +
    truncate), not os.replace: replacing the inode would hand a waiter that already
    opened the old file a stale copy to write back.
    """
    with open(path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        doc: dict[str, Any] = json.load(f)
        for e in doc["releases"]:
            if not e.get("retired") and (e.get("path"), e.get("head"), e.get("admin_id"), e.get("mtime")) == (
                entry.get("path"), entry.get("head"), entry.get("admin_id"), entry.get("mtime")
            ):
                e["retired"] = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "reason": reason}
                break
        else:
            raise RuntimeError(f"release entry not found for retirement: {entry.get('path')}")
        f.seek(0)
        json.dump(doc, f, indent=1)
        f.truncate()


def append_release(path: str, entry: dict[str, Any]) -> None:
    """Append a fresh release entry under the same LOCK_EX as retire_release, so a
    capture landing mid-apply is never lost to a concurrent rewrite. Creates the file.
    O_CREAT without O_APPEND: append mode would force every write to the file's end,
    breaking the in-place rewrite."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    with os.fdopen(fd, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        raw = f.read()
        doc: dict[str, Any] = json.loads(raw) if raw.strip() else {"releases": []}
        doc["releases"].append(entry)
        f.seek(0)
        json.dump(doc, f, indent=1)
        f.truncate()


# ---------------------------------------------------------------- plan

def cmd_plan(args: argparse.Namespace) -> None:
    if not args.inventory and not args.releases:
        sys.exit("plan needs --inventory and/or --releases")
    protected, anchors = load_protected(args.protected)
    plan: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    trunk = "trunk()"
    roots: list[str] = []
    if args.inventory:
        inv: dict[str, Any] = json.load(open(args.inventory))
        trunk, repo, roots = inv["trunk"], inv["repo"], inv["roots"]
        for r in inv["rows"]:
            path, cls = r.get("path"), r["class"]
            if cls == "STALE_REGISTRATION":
                name = r.get("name")
                if not name:
                    # A git worktree admin entry whose directory is gone: there is no jj
                    # workspace name to verify merged-ness against. A colocated jj slot's
                    # registration is carried by its own named row; the git-side entry
                    # itself is the owner's to clean up.
                    refused.append({"path": path, "name": None, "class": cls,
                                    "reason": "git worktree admin entry with no jj workspace name: nothing to verify merged-ness against; git-side cleanup left to the owner"})
                    continue
                st = jj_merged_state(repo, name, trunk)
                if st is None:
                    refused.append({"name": name, "class": cls, "reason": "cannot derive merged-ness for the registration"})
                    continue
                if st["unmerged"] or not st["at_empty"]:
                    continue  # not merged: not reapable
                reg = find_op(repo, f"add workspace '{name}'")  # add op single-quotes the name (measured)
                if reg is None:
                    refused.append({"name": name, "class": cls,
                                    "reason": "registering op unfindable (op log truncated?): refusing — treating missing as old would re-open the creation race"})
                    continue
                age_h = (time.time() - reg[1]) / 3600
                if age_h < args.fresh_hours:
                    refused.append({"name": name, "class": cls,
                                    "reason": f"registering op {age_h:.2f}h old is younger than {args.fresh_hours}h: likely the `jj workspace add` creation window"})
                    continue
                plan.append({"kind": "forget", "path": None, "name": name, "repo": repo, "class": cls,
                             "reason": f"registration with no directory; {name}@ merged; registering op {age_h:.1f}h old"})
                continue
            if not path or cls in ("LIVE", "UNPUSHED", "GIT_UNPUSHED", "GIT_HEAD_PUSHED", "UNOBSERVABLE",
                                   "HOLDS_WORKING_COPY", "SCRATCH_UNVERIFIED"):
                continue  # keep classes; plain git worktrees are reaped only via an owner release
            if is_protected(path, protected, anchors):
                continue
            idle = r.get("jj_idle_hours")
            if idle is None:  # D1: freshness unverifiable => refuse, whatever the class
                refused.append({"path": path, "name": r.get("name"), "class": cls,
                                "reason": "idle unknown (no tree_state mtime): freshness unverifiable — a mid-creation slot looks exactly like this"})
                continue
            if idle < args.fresh_hours:
                continue
            if cls == "SCRATCH":
                # A plain directory has no working-copy commit to verify against, so age and
                # liveness ARE the evidence, re-derived again at apply time. This is the rule
                # the 2026-09-18 hand sweep used on 270k candidates: 71,256 removed, 3,055
                # skipped as fresh or live.
                plan.append({"path": path, "name": None, "kind": "scratch", "class": cls,
                             "reason": f"plain scratch directory, idle {idle}h (>= {args.fresh_hours}h), no live process"})
                continue
            if cls == "PUSHED" and args.skip_pushed:
                continue
            reason = {
                "MERGED": "all non-empty ancestors in trunk; @ empty",
                "PUSHED": f"all commits on remote bookmarks {r.get('bookmarks')}",
                "UNREGISTERED": "jj already forgot this workspace; directory is residue",
            }[cls]
            plan.append({"path": path, "name": r.get("name"), "kind": "jj", "repo": repo, "class": cls,
                         "reason": f"{reason}; jj idle {idle}h"})
    if args.releases:
        for e in load_releases(args.releases):
            if e.get("retired"):
                continue
            note = "path absent"
            if os.path.lexists(e["path"]):
                ident = slot_identity(e["path"], repo=e.get("repo"), name=e.get("name"))
                expected = {"head": e.get("head"), "admin_id": e.get("admin_id"), "mtime": e.get("mtime")}
                note = "identity matches" if ident == expected else f"identity mismatch (read-only report; apply decides): now {ident}"
            plan.append({"kind": "released", "path": e["path"], "name": e.get("name"), "repo": e.get("repo"),
                         "class": "RELEASED", "release": e, "reason": f"owner-released slot; {note}"})
    json.dump({"plan": plan, "refused": refused, "protected": protected, "anchors": anchors,
               "trunk": trunk, "fresh_hours": args.fresh_hours, "roots": roots}, sys.stdout, indent=1)
    print(f"\n{len(plan)} items, {len(refused)} refused", file=sys.stderr)


# ---------------------------------------------------------------- apply

def _pace(io_limit: float, ledger: Path, path: str | None) -> None:
    waited = 0
    while io_full_avg10() > io_limit:
        time.sleep(15)
        waited += 15
    if waited:
        ledger_write(ledger, {"op": "paced", "path": path, "waited_s": waited})


def _store_git_dirs(dp: Path) -> list[Path]:
    """The git dirs whose object store serves the checkout or bare repo at <dp>. A worktree's
    alternates live in its common dir, not in its own .git/worktrees/<id> entry."""
    found: list[Path] = []
    dotgit = dp / ".git"
    if dotgit.is_dir():
        found.append(dotgit)
    elif dotgit.is_file():
        text = dotgit.read_text()
        if text.startswith("gitdir:"):
            gd = dp / text.split(":", 1)[1].strip()
            commondir = gd / "commondir"
            found.append(gd / commondir.read_text().strip() if commondir.is_file() else gd)
    jj_repo = dp / ".jj" / "repo"
    if jj_repo.is_file():
        jj_repo = dp / ".jj" / jj_repo.read_text().strip()
    found.append(jj_repo / "store" / "git")
    if (dp / "objects").is_dir() and (dp / "HEAD").is_file():
        found.append(dp)  # bare repo
    return found


def borrowed_object_dirs(roots: list[str], ledger: Path) -> dict[str, set[str]]:
    """realpath of every object dir some clone under <roots> borrows from through
    objects/info/alternates -> the clones (checkout or bare repo paths) that borrow it.
    Walks once per apply, with the inventory walk's pruning. An alternates file that
    exists but cannot be read is ledgered: its borrows are unknown."""
    borrowed: dict[str, set[str]] = {}
    for root in roots:
        for dp, dn, _fn in os.walk(root):
            here = Path(dp)
            try:
                git_dirs = _store_git_dirs(here)
            except OSError as exc:
                ledger_write(ledger, {"op": "error", "path": dp, "stage": "alternates-scan", "out": repr(exc)[-400:]})
                git_dirs = []
            for gd in git_dirs:
                alternates = gd / "objects" / "info" / "alternates"
                try:
                    lines = alternates.read_text().splitlines()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    ledger_write(ledger, {"op": "error", "path": str(alternates), "stage": "alternates-scan", "out": repr(exc)[-400:]})
                    continue
                for line in lines:
                    entry = line.strip()
                    if entry and not entry.startswith("#"):
                        target = os.path.realpath(gd / "objects" / entry)
                        borrowed.setdefault(target, set()).add(dp)
            if here in git_dirs:
                dn[:] = []  # a bare repo's internals hold no clones
                continue
            dn[:] = [d for d in dn if not d.startswith(".") or d in WALK_HIDDEN]
            if dp.count("/") - root.count("/") >= WALK_DEPTH:
                dn[:] = []
    return borrowed


def borrowers_of(path: str, borrowed: dict[str, set[str]]) -> list[str]:
    """Clones outside <path> that borrow an object dir at or under <path>."""
    real = os.path.realpath(path)

    def inside(p: str) -> bool:
        r = os.path.realpath(p)
        return r == real or r.startswith(real + "/")

    return sorted({b for obj, clones in borrowed.items() if inside(obj) for b in clones if not inside(b)})


BORROWED_REASON = "object store borrowed through objects/info/alternates by {clones}: removal would break them and lose their commits"


def _apply_forget(item: dict[str, Any], trunk: str, fresh_hours: float, ledger: Path) -> None:
    """D8: forget a merged registration with no directory, re-verified at apply time."""
    name, repo = item["name"], item["repo"]
    registered = jj_registered(repo)
    if registered is None:
        ledger_write(ledger, {"op": "error", "name": name, "stage": "workspace-list", "out": "jj workspace list failed"})
        return
    if name not in registered:
        ledger_write(ledger, {"op": "absent", "name": name, "reason": "registration already gone"})
        return
    st = jj_merged_state(repo, name, trunk)
    if st is None or st["unmerged"] or not st["at_empty"]:
        ledger_write(ledger, {"op": "skip", "name": name, "reason": f"re-verification failed: {name}@ no longer merged ({st})"})
        return
    reg = find_op(repo, f"add workspace '{name}'")
    if reg is None:
        ledger_write(ledger, {"op": "skip", "name": name, "reason": "re-verification failed: registering op unfindable"})
        return
    if (time.time() - reg[1]) / 3600 < fresh_hours:
        ledger_write(ledger, {"op": "skip", "name": name, "reason": "re-verification failed: registering op younger than threshold"})
        return
    rc, out = run(JJ + ["workspace", "forget", name], cwd=repo)
    if rc != 0:
        ledger_write(ledger, {"op": "error", "name": name, "stage": "forget", "out": out[-400:]})
        return
    op_id = capture_forget_op(repo, name)
    if op_id is None:
        ledger_write(ledger, {"op": "error", "name": name, "stage": "forget-op-capture",
                              "out": "forget succeeded but its op id was not found in the op log"})
        return
    ledger_write(ledger, {"op": "forgot", "name": name, "reason": item.get("reason"),
                          "forget_op": op_id, "recover": RECOVER_NOTE.format(op=op_id)})


def _retire_after_removal(releases_path: str, entry: dict[str, Any], ledger: Path, path: str) -> None:
    """Retire only after the ledger line is written: the ledger is the recovery surface,
    so a destructive action is recorded before any step that can raise. A retire failure
    (releases file edited, replaced, or clobbered) is ledgered as its own error line
    rather than crashing apply mid-plan."""
    try:
        retire_release(releases_path, entry, "removed")
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        ledger_write(ledger, {"op": "error", "path": path, "stage": "retire", "out": repr(exc)[-400:]})


def _cleanup_admin_entry(repo: str, admin_id: str) -> str:
    """Targeted removal of one worktree admin entry (.git/worktrees/<admin_id>) after the
    rm fallback deleted its directory. Never `git worktree prune`: on a shared store a
    prune walks every slot and can delete other sessions' admin entries. Returns a result
    string the caller ledgers — success and failure alike, nothing discarded. Refuses any
    admin_id that is not a single plain directory name: slot_identity captures the id as
    basename of the slot's .git pointer, so a crafted pointer ending in '/..' (or '/', '/.')
    plus a matching release entry would otherwise resolve the join to .git itself or the
    whole worktrees/ dir."""
    if admin_id != os.path.basename(admin_id) or admin_id in ("", ".", ".."):
        return f"admin-entry cleanup refused: admin id {admin_id!r} is not a plain directory name (join would escape .git/worktrees/)"
    rc, gitdir = run(["git", "-C", repo, "rev-parse", "--git-common-dir"], timeout=120)
    if rc != 0:
        return f"admin-entry cleanup failed: rev-parse --git-common-dir: {gitdir[-200:]}"
    admin = Path(gitdir if os.path.isabs(gitdir) else os.path.join(repo, gitdir)) / "worktrees" / admin_id
    if not admin.is_dir():
        return f"admin entry already absent: {admin}"
    try:
        shutil.rmtree(admin)
    except OSError as exc:
        return f"admin-entry cleanup failed: {exc}"
    return f"admin entry removed: {admin}"


def _apply_released(
    args: argparse.Namespace, item: dict[str, Any], trunk: str, ledger: Path, borrowed: dict[str, set[str]]
) -> None:
    """Owner-released slot: verify identity, guard reachability, refuse divergence, retire one-shot."""
    e: dict[str, Any] = item["release"]
    path, kind, repo = e["path"], e["kind"], e.get("repo")
    if not args.releases:
        ledger_write(ledger, {"op": "error", "path": path, "stage": "released",
                              "out": "released plan item but apply got no --releases file to retire entries in"})
        return

    def refuse(reason: str, retire: bool = False) -> None:
        ledger_write(ledger, {"op": "released-refused", "path": path, "reason": reason, "retire_intended": retire})
        if retire:
            try:
                retire_release(args.releases, e, f"refused: {reason}")
            except (RuntimeError, OSError, ValueError, KeyError) as exc:
                ledger_write(ledger, {"op": "error", "path": path, "stage": "retire", "out": repr(exc)[-400:]})

    if not os.path.lexists(path):
        ledger_write(ledger, {"op": "released-absent", "path": path})
        try:
            retire_release(args.releases, e, "path absent at apply")
        except (RuntimeError, OSError, ValueError, KeyError) as exc:
            ledger_write(ledger, {"op": "error", "path": path, "stage": "retire", "out": repr(exc)[-400:]})
        return
    protected, anchors = load_protected(args.protected)
    hit = is_protected(path, protected, anchors)
    if hit and not (item.get("subpath_release") or e.get("subpath_release")):
        refuse(f"protected by {hit}")
        return
    pu = procs_under(path)
    if pu:
        refuse(f"live processes {pu[:4]}")
        return
    clones = borrowers_of(path, borrowed)
    if clones:
        refuse(BORROWED_REASON.format(clones=clones))
        return
    ident = slot_identity(path, repo=repo, name=e.get("name"))
    if ident is None:
        refuse("slot identity unreadable")
        return
    if ident["head"] != e.get("head") or ident["admin_id"] != e.get("admin_id"):
        refuse(f"adoption evidence — HEAD/admin-id mismatch (now {ident['head'][:12]}/{ident['admin_id']})", retire=True)
        return
    if ident["mtime"] != e.get("mtime"):
        refuse(f"mtime-only mismatch ({e.get('mtime')} -> {ident['mtime']}): transient toucher; keeping the entry")
        return
    _pace(args.io_limit, ledger, path)
    if kind == "git":
        if git_head_pushed(path) is not True:
            refuse("unpushed commits reachable from HEAD (removal would make them gc-able)")
            return
        clean, reason = git_status_clean(path)
        if not clean:
            refuse(reason)
            return
        fallback: list[str] | None = None
        rc, out = run(["ionice", "-c3", "nice", "-n19", "git", "-C", str(repo), "worktree", "remove", "--force", path], timeout=1800)
        if rc != 0:
            fallback = ["git worktree remove failed; falling back to rm + targeted admin-entry cleanup", out[-200:]]
            r = subprocess.run(RM + [path], capture_output=True, text=True)
            if r.returncode != 0:
                ledger_write(ledger, {"op": "error", "path": path, "stage": "rm", "out": r.stderr[-400:]})
                return
            # The admin id was identity-verified against the release entry above, so this
            # removes exactly the reaped slot's entry — never a store-wide prune.
            fallback.append(_cleanup_admin_entry(str(repo), str(e["admin_id"])))
        removed: dict[str, Any] = {"op": "removed", "path": path, "kind": "released-git", "reason": item.get("reason")}
        if fallback:
            removed["fallback"] = fallback
        ledger_write(ledger, removed)
        _retire_after_removal(args.releases, e, ledger, path)
        return
    # jj released slot: merged-ness + divergence are enforced below; staleness comes from
    # the mtime identity match above (any touch since release refuses) — stronger than an
    # idle threshold, so none is checked.
    name = e.get("name") or jj_workspace_name(path)
    if not (name and repo):
        refuse("jj released slot without a workspace name/repo")
        return
    st = jj_merged_state(str(repo), name, e.get("trunk", trunk))
    if st is None or st["unmerged"] or not st["at_empty"]:
        refuse(f"re-verification failed: not MERGED ({st})")
        return
    div = jj_divergence(str(repo), name, path)
    if div is None or div:
        refuse(f"divergence — content exists only on disk: {div}")
        return
    forget_op = None
    registered = jj_registered(str(repo))
    if registered and name in registered:
        rc, out = run(JJ + ["workspace", "forget", name], cwd=str(repo))
        if rc != 0:
            ledger_write(ledger, {"op": "error", "path": path, "name": name, "stage": "forget", "out": out[-400:]})
            return
        forget_op = capture_forget_op(str(repo), name)
        if forget_op is None:
            ledger_write(ledger, {"op": "error", "path": path, "name": name, "stage": "forget-op-capture",
                                  "out": "forget succeeded but its op id was not found; refusing rm without a recovery id"})
            return
    r = subprocess.run(RM + [path], capture_output=True, text=True)
    if r.returncode != 0:
        ledger_write(ledger, {"op": "error", "path": path, "name": name, "stage": "rm", "out": r.stderr[-400:]})
        return
    entry: dict[str, Any] = {"op": "removed", "path": path, "name": name, "kind": "released-jj", "reason": item.get("reason")}
    if forget_op:
        entry["forget_op"] = forget_op
        entry["recover"] = RECOVER_NOTE.format(op=forget_op)
    ledger_write(ledger, entry)
    _retire_after_removal(args.releases, e, ledger, path)


def _apply_path_item(
    args: argparse.Namespace, item: dict[str, Any], trunk: str, ledger: Path, borrowed: dict[str, set[str]],
    fresh_hours: float = 24.0,
) -> None:
    path, name, cls = item["path"], item.get("name"), item.get("class")
    repo = item.get("repo")
    if item["kind"] not in ("jj", "scratch"):
        # cmd_plan emits jj and scratch path items; plain git worktrees are reaped solely via
        # an owner release. Anything else is a hand-authored plan this pipeline cannot verify.
        ledger_write(ledger, {"op": "error", "path": path, "name": name, "stage": "kind",
                              "out": f"unknown plan kind {item['kind']!r}: refusing removal without a verification pipeline"})
        return

    def skip(reason: str) -> None:
        ledger_write(ledger, {"op": "skip", "path": path, "name": name, "reason": reason})

    if not os.path.lexists(path):
        ledger_write(ledger, {"op": "absent", "path": path, "name": name})
        return
    protected, anchors = load_protected(args.protected)  # re-read: the agent may add to it mid-run
    hit = is_protected(path, protected, anchors)
    if hit and not item.get("subpath_release"):
        skip(f"protected by {hit}")
        return
    pu = procs_under(path)
    if pu:
        skip(f"live processes {pu[:4]}")
        return
    clones = borrowers_of(path, borrowed)
    if clones:
        skip(BORROWED_REASON.format(clones=clones))
        return
    if item["kind"] == "scratch":
        # A plain directory's evidence is age + liveness + "not a working copy", and all three
        # are re-derived HERE because the plan can be an hour old: a directory written to since
        # the plan, or one that has become a workspace, must survive. One walk gives all of it.
        try:
            scan = scan_tree(Path(path))
        except OSError as error:
            skip(f"re-verification failed: contents unreadable ({error})")
            return
        if scan.holds_working_copy is not False:
            skip("re-verification failed: " + ("holds a jj/git working copy, not scratch"
                                               if scan.holds_working_copy
                                               else "working copy scan could not finish: unverifiable"))
            return
        if scan.newest_mtime is None:
            skip("re-verification failed: freshness unreadable (the walk did not finish)")
            return
        idle_h = (time.time() - scan.newest_mtime) / 3600
        if idle_h < fresh_hours:
            skip(f"re-verification failed: written to {idle_h:.2f}h ago (< {fresh_hours}h)")
            return
        _pace(args.io_limit, ledger, path)
        r = subprocess.run(RM + [path], capture_output=True, text=True)
        if r.returncode != 0:
            ledger_write(ledger, {"op": "error", "path": path, "stage": "rm", "out": r.stderr[-400:]})
            return
        ledger_write(ledger, {"op": "removed", "path": path, "name": None, "kind": "scratch",
                              "idle_hours": round(idle_h, 1), "reason": item.get("reason"),
                              "recover": "none: a scratch directory has no store copy — recovery is re-running whatever wrote it"})
        return
    # D6: the plan may be stale — re-derive the class evidence immediately before acting
    if repo:
        disk_name = jj_workspace_name(path)
        if cls in ("MERGED", "PUSHED"):
            if disk_name != name:
                skip(f"re-verification failed: workspace name changed ({name} -> {disk_name})")
                return
            st = jj_merged_state(repo, str(name), trunk)
            if st is None:
                skip("re-verification failed: cannot derive workspace state")
                return
            if cls == "MERGED" and (st["unmerged"] or not st["at_empty"]):
                skip(f"re-verification failed: no longer MERGED ({st})")
                return
            if cls == "PUSHED" and st["unpushed"]:
                skip(f"re-verification failed: no longer PUSHED ({st})")
                return
        elif cls == "UNREGISTERED":
            registered = jj_registered(repo)
            if registered is None or (disk_name and disk_name in registered):
                skip("re-verification failed: workspace is registered (again) or the registry is unreadable")
                return
    _pace(args.io_limit, ledger, path)
    # D2: refuse when disk diverges from the store — content that exists only on disk (Decision 1).
    # Runs BEFORE the forget: costless ordering that guards against forget-semantics drift.
    if not (name and repo and cls in ("MERGED", "PUSHED")):
        skip("no working-copy commit to verify disk contents against (unregistered residue): refusing removal")
        return
    div = jj_divergence(repo, name, path)
    if div is None:
        skip("divergence unverifiable: refusing removal")
        return
    if div:
        skip(f"divergence — content exists only on disk: {div}")
        return
    forgot = None
    forget_op = None
    registered = jj_registered(repo)
    if registered and name in registered:
        rc, out = run(JJ + ["workspace", "forget", name], cwd=repo)
        forgot = [rc, out[-200:]]
        if rc != 0:
            ledger_write(ledger, {"op": "error", "path": path, "name": name, "stage": "forget", "out": out[-400:]})
            return
        forget_op = capture_forget_op(repo, name)
        if forget_op is None:
            ledger_write(ledger, {"op": "error", "path": path, "name": name, "stage": "forget-op-capture",
                                  "out": "forget succeeded but its op id was not found; refusing rm without a recovery id"})
            return
    r = subprocess.run(RM + [path], capture_output=True, text=True)
    if r.returncode != 0:
        ledger_write(ledger, {"op": "error", "path": path, "name": name, "stage": "rm", "out": r.stderr[-400:]})
        return
    entry: dict[str, Any] = {"op": "removed", "path": path, "name": name, "kind": "jj", "reason": item.get("reason"), "forgot": forgot}
    if forget_op:
        entry["forget_op"] = forget_op
        entry["recover"] = RECOVER_NOTE.format(op=forget_op)
    ledger_write(ledger, entry)


def cmd_apply(args: argparse.Namespace) -> None:
    doc: dict[str, Any] = json.load(open(args.plan))
    plan: list[dict[str, Any]] = doc["plan"]
    trunk: str = doc.get("trunk", "trunk()")
    fresh_hours: float = doc.get("fresh_hours", 24.0)
    ledger = Path(args.ledger)
    # D5: single instance per store, whatever the entry point (timer or hand-run)
    lock_path = apply_lock_path([item["repo"] for item in plan if item.get("repo")])
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        ledger_write(ledger, {"op": "locked", "lock": str(lock_path), "reason": "another apply holds the per-store lock; exiting"})
        lock_fd.close()
        return
    try:
        ledger_write(ledger, {"op": "apply-start", "items": len(plan), "plan": args.plan})
        # Every removal below is refused while a clone borrows objects from inside it. The
        # inventory's roots plus $HOME (its default root) bound where such a clone can be seen.
        borrowed = borrowed_object_dirs(sorted({str(Path.home()), *doc["roots"]}), ledger)
        for item in plan:
            if item["kind"] == "forget":
                _apply_forget(item, trunk, fresh_hours, ledger)
            elif item["kind"] == "released":
                _apply_released(args, item, trunk, ledger, borrowed)
            else:
                _apply_path_item(args, item, trunk, ledger, borrowed, fresh_hours)
        ledger_write(ledger, {"op": "apply-complete", "items": len(plan)})
    finally:
        lock_fd.close()


def cmd_release_capture(args: argparse.Namespace) -> None:
    path = str(Path(args.path).resolve())
    repo = str(Path(args.repo).resolve())
    ident = slot_identity(path, repo=repo, name=args.name)
    if ident is None:
        sys.exit(f"cannot capture slot identity for {path}")
    entry: dict[str, Any] = {
        "path": path, "repo": repo, "kind": args.kind, "name": args.name,
        "head": ident["head"], "admin_id": ident["admin_id"], "mtime": ident["mtime"],
        "session": args.session, "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": args.message,
    }
    if args.trunk:
        entry["trunk"] = args.trunk
    if args.releases:
        append_release(args.releases, entry)
    json.dump(entry, sys.stdout, indent=1)
    print(file=sys.stderr)


# ---------------------------------------------------------------- tmp

DEFAULT_FAMILIES = r"^(tmp\.[A-Za-z0-9]{10}$|pytest-of-|xvfb-run\.|tl-preview-|worker-crash-|vscode-smoke-|.*-review[.-]|review-\d+|pr\d+-|agent-c-pr\d+|core-venv|compose-venv|\w+-home\d*$)"


def cmd_tmp(args: argparse.Namespace) -> None:
    fam = re.compile(str(args.families))
    tmp_dir = str(args.dir)
    protected, anchors = load_protected(args.protected)
    live_refs, unreadable = proc_path_refs()
    live = {r for _, r in live_refs}
    print(f"liveness census: {unreadable} processes unreadable (other uid / PID 1) - partial", file=sys.stderr)
    now = time.time()
    targets: list[str] = []
    for name in sorted(os.listdir(tmp_dir)):
        p = os.path.join(tmp_dir, name)
        if not os.path.isdir(p) or os.path.islink(p) or not fam.match(name):
            continue
        # The directory's own mtime moves only when an entry is added or removed, so a tree
        # whose files are being rewritten reads as untouched; date it by the newest mtime a
        # bounded walk of it finds, and keep one the walk could not date (it did not finish, or
        # it holds a jj/git working copy, which is not throwaway scratch).
        try:
            scan = scan_tree(Path(p))
        except OSError:
            continue
        if scan.newest_mtime is None or now - scan.newest_mtime < args.older_than_hours * 3600:
            continue
        if is_protected(p, protected, anchors) or any(c == p or c.startswith(p + "/") for c in live):
            continue
        targets.append(p)
    if not args.apply:
        print("\n".join(targets))
        print(f"{len(targets)} stale temp dirs (dry run; add --apply --ledger L to delete)", file=sys.stderr)
        return
    ledger = Path(args.ledger)
    ledger_write(ledger, {"op": "tmp-start", "targets": len(targets), "dir": tmp_dir})
    removed = 0
    for p in targets:
        if refs_under(p, proc_path_refs()[0]):
            ledger_write(ledger, {"op": "skip", "path": p, "reason": "live processes"})
            continue
        while io_full_avg10() > args.io_limit:
            time.sleep(15)
        r = subprocess.run(RM + [p], capture_output=True, text=True)
        if r.returncode != 0:
            ledger_write(ledger, {"op": "error", "path": p, "out": r.stderr[-200:]})
            continue
        removed += 1
    ledger_write(ledger, {"op": "tmp-complete", "removed": removed})


# ---------------------------------------------------------------- containers

_LOCAL_STACK_RE = re.compile(r"^tl-platform-(.+)-([0-9a-f]{16})-db$")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_E2E_NAME_RE = re.compile(rf"^platform-e2e-(?:(.+)-)?{_UUID}$")


def _e2e_run_ids_in_env() -> set[str]:
    """TRAJECTORY_PLATFORM_E2E_RUN_ID of every readable host process (the e2e owner's liveness key)."""
    found: set[str] = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            env = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            continue
        for part in env.split(b"\0"):
            if part.startswith(b"TRAJECTORY_PLATFORM_E2E_RUN_ID="):
                found.add(part.split(b"=", 1)[1].decode(errors="replace"))
    return found


def _pg_clients(ss_out: str, ports: list[int]) -> int:
    """Established connections to any of the container's published host ports, docker-proxy excluded."""
    return sum(1 for line in ss_out.splitlines() if "docker-proxy" not in line
               and any(re.search(rf"127\.0\.0\.1:{p}\s|0\.0\.0\.0:{p}\s|\]:{p}\s", line) for p in ports))


def _local_stack_workspaces() -> dict[str, str]:
    """owner-hash -> checkout root, for every plausible tl_platform PACKAGE_ROOT on the box.
    local_stack_state.database_identity: owner = sha256(f"{uid}:{PACKAGE_ROOT.resolve()}")[:16]."""
    import hashlib
    home = Path.home()
    roots: list[Path] = [home, home / "src", home / ".worktrees"]
    roots += [p for p in (home / ".worktrees").glob("*") if p.is_dir()]
    roots += [p for p in (home / ".worktrees").glob("*/*") if p.is_dir()]
    roots += [p for p in (home / "boxes").glob("*") if p.is_dir()]
    roots += [p for p in Path("/tmp").glob("*") if p.is_dir() and not p.is_symlink()]
    out: dict[str, str] = {}
    for root in roots:
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for c in children:
            pkg = f"{c}/platform/tl_platform"
            out[hashlib.sha256(f"{os.getuid()}:{pkg}".encode()).hexdigest()[:16]] = str(c)
    return out

def _docker_daemon_is_box_local() -> bool:
    """True iff `docker info` reports a box-local daemon. A box's own daemon names itself
    `agentbox-<id>`; the shared host daemon reports the host's name. A query that fails (timeout,
    docker absent, non-zero exit) is conservatively NOT box-local."""
    try:
        rc, out = run(["docker", "info", "--format", "{{.Name}}"], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 0 and out.strip().startswith("agentbox-")


def _is_leaked_never_started(box_local: bool, status: str, pid: int, started_at: str, age_h: float, holders: list[Any]) -> bool:
    """A `created`, never-started container is safe to call leaked only on a box-local daemon,
    where no other box's container can exist: Pid 0 and a zero StartedAt confirm it never ran,
    age past 1h rules out a container still in its creation window, and no holders confirm
    nothing is about to start it."""
    return (
        box_local
        and status == "created"
        and pid == 0
        and started_at.startswith("0001-01-01")
        and age_h > 1
        and not holders
    )


_INSPECT_DRIVER_RE = re.compile(r"\binspect(\s|_ai|-)|\btl (run|cyber)\b|\bpytest\b|\bhawk\b")


def _is_leaked_inspect_sandbox(box_local: bool, status: str, age_h: float, drivers: list[Any], holders: list[Any]) -> bool:
    """An `inspect-*` compose project whose eval is over: on a BOX-LOCAL daemon only this box's
    processes can have started it, and the box's /proc sees them all, so a running sandbox older
    than a day with no eval-driver process anywhere in the box (the driver pattern includes
    pytest, which drives Inspect through its Python API) and no holder has nothing left to serve.
    On the shared daemon a driver can live in any box, so the row stays unknown."""
    return box_local and status == "running" and age_h > 24 and not drivers and not holders


def cmd_containers(_args: argparse.Namespace) -> None:
    """Every container with its family, owner attribution and a liveness verdict.

    Families and rules are the fixture owners' (e2e owner, 2026-09-20, agent-c#19533):
      platform-e2e  labels trajectory.platform-e2e=1, .role (harness-db|test-fixture|cleanup-probe),
                    .run-id (32-hex TRAJECTORY_PLATFORM_E2E_RUN_ID, or "" for a developer's local run).
                    Created-never-started and older than 1h: leaked. Running with a run-id: live iff a
                    host process carries that run-id, else live iff its postgres port has clients.
                    Running with an EMPTY run-id: the env walk matches NOTHING (another producer's id
                    says nothing about this container) - clients alone decide, docker's Created as grace.
                    Unlabelled containers matching the name shape are pre-#19533 producers: same rules,
                    role "unlabelled".
      local-stack   labels trajectory.local-stack{,.name,.owner}; owner = sha256(uid:PACKAGE_ROOT)[:16]
                    resolved to a checkout here. Live iff a process stands under that checkout, or its
                    postgres port has clients. Workspace gone and no clients: leaked.
      ryuk          testcontainers-ryuk-*: Created = stranded before start (remove together with the
                    org.testcontainers.session-id-labelled fixtures of the same id); running = find the
                    client pid through its published port, never remove by hand - it self-reaps.
      agentbox      session infrastructure; never a reaper target.
      inspect-sandbox  compose project `inspect-*` (an Inspect eval's sandbox). On a BOX-LOCAL daemon,
                    running, older than 24h, with no eval-driver process in the box (inspect, tl run,
                    tl cyber, pytest, hawk) and no holders: leaked. Three lanes found these 9 h to 2
                    days after their evals ended (2026-09-26). On the shared daemon: unknown.
      other         no known label/name shape. On a BOX-LOCAL daemon (`docker info --format
                    '{{.Name}}'` starts with "agentbox-") no other box's container can ever exist,
                    so a container that is `created`, has State.Pid 0, State.StartedAt at docker's
                    zero time, is older than 1h, and has no holders is leaked -- it can only be this
                    box's own dead fixture. On the shared host daemon (named for the host) other boxes'
                    containers are legitimately present, so this row stays unknown regardless of age
                    or state. Four independent lanes (e2e, env typing, Reaper, agent-c#20033)
                    rediscovered this same discriminator on 2026-09-25 while chasing the AGENTC-751
                    fixture-timeout leak, each blocked by a family=other/verdict=unknown row that a
                    leaked/stranded-only reaper would never remove.
    Labels attribute; they never decide liveness. Verdicts are a report - removal is the apply step's
    judgment with the owner's rules re-checked at that moment.
    """
    rc, out = run(["timeout", "120", "docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"])
    if rc != 0:
        sys.exit(out)
    ids = [line.split("\t")[0] for line in out.splitlines()]
    # rc is 1 when any id vanished between ps and inspect; stdout is still one JSON array.
    insp = subprocess.run(["timeout", "120", "docker", "inspect", *ids], capture_output=True, text=True)
    data: list[dict[str, Any]] = json.loads(insp.stdout) if insp.stdout.strip().startswith("[") else []
    cmdlines: list[tuple[int, str]] = []
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                cmdlines.append((int(pid), Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")))
            except OSError:
                pass
    run_ids = _e2e_run_ids_in_env()
    drivers = [(pid, c[:120]) for pid, c in cmdlines if _INSPECT_DRIVER_RE.search(c) and "disk_hygiene" not in c]
    _, ss_out = run(["sudo", "ss", "-tnpH", "state", "established"])
    cwds = proc_cwds()
    stack_ws = _local_stack_workspaces()
    box_local = _docker_daemon_is_box_local()
    now = time.time()
    result: list[dict[str, Any]] = []
    for d in data:
        name = str(d["Name"]).lstrip("/")
        labels: dict[str, str] = d["Config"].get("Labels") or {}
        status = d["State"]["Status"]
        created = d.get("Created", "")[:19]
        try:
            age_h = (now - time.mktime(time.strptime(created, "%Y-%m-%dT%H:%M:%S")) + time.timezone) / 3600
        except ValueError:
            age_h = 0.0
        ports = [int(b["HostPort"]) for bs in (d["NetworkSettings"].get("Ports") or {}).values() if bs for b in bs if b.get("HostPort", "").isdigit()]
        clients = _pg_clients(ss_out, ports) if ports else 0
        proj = labels.get("com.docker.compose.project", "")
        needles = {n for n in (name, proj) if n}
        holders = [(pid, c[:120]) for pid, c in cmdlines if any(n in c for n in needles) and "disk_hygiene" not in c]
        row: dict[str, Any] = {
            "name": name, "status": status, "started": d["State"].get("StartedAt", "")[:19], "age_h": round(age_h, 1),
            "compose_project": proj, "compose_working_dir": labels.get("com.docker.compose.project.working_dir", ""),
            "holders": holders[:5], "pg_clients": clients, "family": "other", "verdict": "unknown",
        }
        e2e_name = _E2E_NAME_RE.match(name)
        if labels.get("trajectory.platform-e2e") == "1" or e2e_name:
            run_id = labels.get("trajectory.platform-e2e.run-id", e2e_name.group(1) or "" if e2e_name else "")
            row.update(family="platform-e2e", role=labels.get("trajectory.platform-e2e.role", "unlabelled"), run_id=run_id)
            if status == "created":
                row["verdict"] = "leaked" if age_h > 1 else "grace"
            elif run_id and run_id in run_ids:
                row["verdict"] = "live"
            else:
                row["verdict"] = "live" if clients else ("grace" if age_h < 1 else "leaked")
        elif labels.get("trajectory.local-stack") == "1" or _LOCAL_STACK_RE.match(name):
            m = _LOCAL_STACK_RE.match(name)
            owner = labels.get("trajectory.local-stack.owner") or (m.group(2) if m else "")
            ws = stack_ws.get(owner)
            under = bool(ws) and any(c == ws or c.startswith(ws + "/") for c in cwds)
            row.update(family="local-stack", stack=labels.get("trajectory.local-stack.name") or (m.group(1) if m else ""), owner=owner, workspace=ws)
            if status == "created":
                row["verdict"] = "leaked"
            else:
                row["verdict"] = "live" if (under or clients) else ("leaked" if not ws else "idle")
        elif name.startswith("testcontainers-ryuk-"):
            row.update(family="ryuk", session_id=name.split("testcontainers-ryuk-", 1)[1])
            row["verdict"] = "stranded" if status == "created" else ("live" if clients else "no-client")
        elif labels.get("org.testcontainers.session-id"):
            row.update(family="tc-fixture", session_id=labels["org.testcontainers.session-id"])
        elif name.startswith("agentbox-"):
            row.update(family="agentbox", verdict="never")
        elif proj.startswith("inspect-"):
            row.update(family="inspect-sandbox", drivers=drivers[:5])
            if _is_leaked_inspect_sandbox(box_local, status, age_h, drivers, holders):
                row.update(verdict="leaked", reason="box-local daemon, eval sandbox older than 24h, no eval driver in the box")
            elif _is_leaked_never_started(box_local, status, d["State"].get("Pid", 0), d["State"].get("StartedAt", ""), age_h, holders):
                row.update(verdict="leaked", reason="box-local daemon, never started")
        elif _is_leaked_never_started(box_local, status, d["State"].get("Pid", 0), d["State"].get("StartedAt", ""), age_h, holders):
            row.update(verdict="leaked", reason="box-local daemon, never started")
        result.append(row)
    # A fixture's lifecycle is its ryuk's (testcontainers' only reaper; started first, lives while a
    # client holds its socket, reaps on disconnect). Stranded ryuk -> its fixtures go with it. Ryuk
    # ABSENT -> the lifecycle is structurally broken: no live owner can exist and self-reap never
    # comes; leaked once past a 15 min startup grace with no clients (e2e owner ruling, 2026-09-20).
    # Ryuk PRESENT -> never reap the fixture on its own; racing an intact ryuk kills a live database.
    ryuk_verdict = {r["session_id"]: r["verdict"] for r in result if r["family"] == "ryuk"}
    for r in result:
        if r["family"] != "tc-fixture":
            continue
        if r["session_id"] in ryuk_verdict:
            r["verdict"] = ryuk_verdict[r["session_id"]]
        elif r["pg_clients"] == 0 and r["age_h"] > 0.25:
            r["verdict"] = "orphan-no-ryuk"
        else:
            r["verdict"] = "grace"
    json.dump(result, sys.stdout, indent=1)
    fam: dict[str, dict[str, int]] = {}
    for r in result:
        fam.setdefault(r["family"], {}).setdefault(r["verdict"], 0)
        fam[r["family"]][r["verdict"]] += 1
    print(f"\n{len(result)} containers; by family/verdict: " + "; ".join(f"{k}: {v}" for k, v in sorted(fam.items())), file=sys.stderr)


# ---------------------------------------------------------------- images

def _utc_ts(stamp: str) -> float:
    """Seconds since the epoch of a docker UTC time ("2026-09-10 21:28:03 +0000 UTC", or LastTagTime's
    "2026-10-21T16:59:00Z" form); docker's zero time is 0."""
    if stamp.startswith("0001-01-01"):
        return 0.0
    return time.mktime(time.strptime(stamp[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")) - time.timezone


def cmd_images(args: argparse.Namespace) -> None:
    """Tagged images no container references, neither built nor pulled in the last N hours. Cheap: no
    `docker system df`. An image's CreatedAt is its build time, so an old image a session pulled an hour ago
    counts from that pull: its Metadata.LastTagTime, which a pull or a tag sets."""
    rc, cids = run(["timeout", "120", "docker", "ps", "-aq", "--no-trunc"])
    used: set[str] = set()
    if cids.strip():
        r = subprocess.run(["timeout", "120", "docker", "inspect", "-f", "{{.Image}}", *cids.split()], capture_output=True, text=True)
        used = {line.strip() for line in r.stdout.splitlines() if line.strip()}
    rc, out = run(["timeout", "120", "docker", "images", "--no-trunc", "--format", "{{.ID}}\t{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}\t{{.Size}}"])
    if rc != 0:
        sys.exit(out)
    cutoff = time.time() - args.older_than_hours * 3600
    old = [line.split("\t") for line in out.splitlines()]
    old = [r for r in old if r[0] not in used and not r[1].startswith("<none>") and _utc_ts(r[2]) <= cutoff]
    tagged: dict[str, float] = {}
    if old:
        rc, ins = run(["timeout", "120", "docker", "image", "inspect", "--format", "{{.Id}}\t{{.Metadata.LastTagTime}}",
                       *sorted({r[0] for r in old})])
        if rc != 0:
            sys.exit(ins)
        tagged = {iid: _utc_ts(t) for iid, t in (line.split("\t", 1) for line in ins.splitlines())}
    rows = [f"{size:>8}  {created[:16]}  {ref}" for iid, ref, created, size in old if tagged[iid] <= cutoff]
    print("\n".join(sorted(rows)))
    print(f"\n{len(rows)} unused tagged images neither built nor pulled in the last {args.older_than_hours}h; remove with plain `docker rmi <ref>` (a refusal means a container uses it)", file=sys.stderr)


# ---------------------------------------------------------------- instruments
# What is happening now, and where the space is. Each instrument prints what its number does
# NOT measure, because the failure these replace is a true number of the wrong thing: on
# 2026-10-30 two disks on sami-rocqperf filled while nine readings taken that afternoon each
# answered a question nobody had asked (a du roll-up at 210% of its own total, 26 GB of
# "duplication" that was 10.87 GB of hardlinks, a 15 s burst read as a rate).

CLK_TCK = os.sysconf("SC_CLK_TCK")


def _gb(n: float) -> str:
    return f"{n / 1e9:.2f}"


def parse_duration(text: str) -> float:
    """Seconds from 90, 90s, 15m, 2h or 3d."""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    suffix = text[-1].lower()
    return float(text[:-1]) * units[suffix] if suffix in units else float(text)


def boot_time() -> float:
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("btime"):
            return float(line.split()[1])
    raise RuntimeError("/proc/stat has no btime line")


def _proc_io(pid: str) -> dict[str, int] | None:
    """/proc/<pid>/io as a dict, or None when this uid may not read it."""
    try:
        text = Path(f"/proc/{pid}/io").read_text()
    except OSError:
        return None
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, value = line.partition(": ")
        if value.strip().isdigit():
            out[key] = int(value)
    return out


def _proc_stat_fields(pid: str) -> list[str] | None:
    """Fields of /proc/<pid>/stat after comm: state [0], utime [11], stime [12], starttime [19]."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None


def _proc_comm(pid: int | str) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return "?"


def proc_sample(want_io: bool) -> tuple[dict[int, dict[str, Any]], int]:
    """One pass over /proc: (pid -> counters, processes whose counters this uid may not read).

    Each sample carries starttime, so a pid reused between two samples is dropped instead of
    producing a nonsense delta."""
    sample: dict[int, dict[str, Any]] = {}
    denied = 0
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        fields = _proc_stat_fields(name)
        if fields is None:
            continue
        row: dict[str, Any] = {"start": fields[19], "ticks": int(fields[11]) + int(fields[12]),
                               "comm": _proc_comm(name)}
        if want_io:
            io = _proc_io(name)
            if io is None:
                denied += 1
                continue
            row["io"] = io
        sample[int(name)] = row
    return sample, denied


WRITERS_CAVEAT = """\
write_bytes is what the kernel charged this process at the block layer, less the pages a truncate or a
delete cancelled. It names no filesystem and no file, and a rewrite of the same bytes is charged again,
so the column is an upper bound on what any disk gained: `fdwrites` names the file, and `df` before and
after measures what a filesystem gained. Each rate is this interval's average, so a burst inside the
interval is invisible and a process that started or stopped inside it has no row. The lifetime column is
the same counter over the whole age of the process; where the two disagree, the interval caught a burst."""


def cmd_writers(args: argparse.Namespace) -> None:
    """Processes by write rate over an interval, each with its working directory."""
    boot = boot_time()
    first, denied = proc_sample(want_io=True)
    time.sleep(args.seconds)
    second, _ = proc_sample(want_io=True)
    rows: list[tuple[float, float, float, float, int, str, str, str]] = []
    for pid, b in second.items():
        a = first.get(pid)
        if a is None or a["start"] != b["start"]:
            continue
        delta = {k: max(0, b["io"].get(k, 0) - a["io"].get(k, 0)) for k in
                 ("write_bytes", "cancelled_write_bytes", "read_bytes", "syscw")}
        wrote = max(0, delta["write_bytes"] - delta["cancelled_write_bytes"])
        if wrote == 0 and delta["read_bytes"] == 0:
            continue
        age = max(1e-9, time.time() - (boot + int(b["start"]) / CLK_TCK))
        life = max(0, b["io"].get("write_bytes", 0) - b["io"].get("cancelled_write_bytes", 0)) / age
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = "?"
        cmd = " ".join(a.decode(errors="replace") for a in
                       Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a)[:100] \
            if os.path.exists(f"/proc/{pid}/cmdline") else "?"
        rows.append((wrote / args.seconds, delta["read_bytes"] / args.seconds,
                     delta["syscw"] / args.seconds, life, pid, b["comm"], cwd, cmd))
    rows.sort(reverse=True)
    print(f"{'MB/s wr':>9} {'MB/s rd':>8} {'wsys/s':>8} {'MB/s life':>10}  {'pid':>7}  {'comm':<16} cwd")
    for wrote, read, syscw, life, pid, comm, cwd, cmd in rows[:args.top]:
        print(f"{wrote / 1e6:9.2f} {read / 1e6:8.2f} {syscw:8.0f} {life / 1e6:10.3f}  {pid:>7}  {comm:<16} {cwd}")
        if args.argv:
            print(f"{'':57}{cmd}")
    print(f"\n{len(rows)} processes did IO over {args.seconds:g} s, {sum(r[0] for r in rows) / 1e6:.1f} MB/s "
          f"written in all; {denied} processes' counters are unreadable by this uid (run it under sudo for "
          f"every process).", file=sys.stderr)
    print(WRITERS_CAVEAT, file=sys.stderr)


def cmd_cpu(args: argparse.Namespace) -> None:
    """CPU over an interval, grouped by command name: what a per-process list hides when one
    name runs as a fan-out (46 python3 processes held 53% of a 32-core box, 2026-10-30)."""
    cores = os.cpu_count() or 1
    first, _ = proc_sample(want_io=False)
    time.sleep(args.seconds)
    second, _ = proc_sample(want_io=False)
    by_comm: dict[str, dict[str, Any]] = {}
    for pid, b in second.items():
        a = first.get(pid)
        if a is None or a["start"] != b["start"] or b["ticks"] - a["ticks"] <= 0:
            continue
        group = by_comm.setdefault(b["comm"], {"ticks": 0, "pids": []})
        group["ticks"] += b["ticks"] - a["ticks"]
        group["pids"].append(pid)
    rows = sorted(by_comm.items(), key=lambda kv: kv[1]["ticks"], reverse=True)
    print(f"{'cores':>7} {'%box':>6} {'procs':>6}  {'comm':<16} busiest pids")
    for comm, group in rows[:args.top]:
        used = group["ticks"] / CLK_TCK / args.seconds
        pids = " ".join(str(p) for p in sorted(group["pids"])[:6])
        print(f"{used:7.2f} {100 * used / cores:6.1f} {len(group['pids']):6}  {comm:<16} {pids}")
    busy = sum(g["ticks"] for _, g in rows) / CLK_TCK / args.seconds
    print(f"\n{len(rows)} command names ran on CPU over {args.seconds:g} s: {busy:.1f} of {cores} cores "
          f"({100 * busy / cores:.0f}% of the box).", file=sys.stderr)
    print("CPU time inside the interval only: a process that stopped inside it has no row, and a burst "
          "shorter than the interval is averaged away. This is CPU and not IO: a process in D state, "
          "waiting on the disk, burns none of it and still holds the box.", file=sys.stderr)


ACCMODE = {0: "r", 1: "w", 2: "rw"}


def _fd_positions(pid: int) -> dict[str, tuple[int, str, int]]:
    """fd -> (file position, path, open flags) for every file on disk the process holds open."""
    out: dict[str, tuple[int, str, int]] = {}
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return out
    for fd in fds:
        try:
            path = os.readlink(f"/proc/{pid}/fd/{fd}")
            if not path.startswith("/") or path.startswith(("/proc/", "/sys/", "/dev/")):
                continue
            pos = flags = 0
            for line in Path(f"/proc/{pid}/fdinfo/{fd}").read_text().splitlines():
                if line.startswith("pos:"):
                    pos = int(line.split()[1])
                elif line.startswith("flags:"):
                    flags = int(line.split()[1], 8)
            out[fd] = (pos, path, flags)
        except (OSError, ValueError, IndexError):
            continue
    return out


def _fd_size(pid: int, fd: str) -> int:
    try:
        return os.stat(f"/proc/{pid}/fd/{fd}").st_size
    except OSError:
        return -1


def cmd_fdwrites(args: argparse.Namespace) -> None:
    """Which FILE grows, when `writers` says a process writes but not where."""
    pids = [int(p) for p in args.pid] if args.pid else [int(p) for p in os.listdir("/proc") if p.isdigit()]
    first = {pid: _fd_positions(pid) for pid in pids}
    sizes = {(pid, fd): _fd_size(pid, fd) for pid, fds in first.items() for fd in fds}
    time.sleep(args.seconds)
    rows: list[tuple[float, float, int, str, str, str]] = []
    for pid in pids:
        for fd, (pos, path, flags) in _fd_positions(pid).items():
            before = first.get(pid, {}).get(fd)
            if before is None or before[1] != path:
                continue
            mode = ACCMODE.get(flags & 3, "?") + ("a" if flags & 0o2000 else "")
            if not args.all_modes and "w" not in mode:
                continue
            size_now, size_before = _fd_size(pid, fd), sizes.get((pid, fd), -1)
            grew = size_now - size_before if size_now >= 0 and size_before >= 0 else 0
            if pos - before[0] <= 0 and grew <= 0:
                continue
            rows.append(((pos - before[0]) / args.seconds, grew / args.seconds, pid, mode,
                         _proc_comm(pid), path))
    rows.sort(reverse=True)
    print(f"{'MB/s pos':>9} {'MB/s size':>10} {'pid':>7} {'mode':>5}  {'comm':<16} path")
    for moved, grew, pid, mode, comm, path in rows[:args.top]:
        print(f"{moved / 1e6:9.2f} {grew / 1e6:10.2f} {pid:>7} {mode:>5}  {comm:<16} {path}")
    print(f"\n{len(rows)} open files moved over {args.seconds:g} s, across {len(pids)} processes.",
          file=sys.stderr)
    print("A file position moves on a read and on a seek as well as on a write, so the size column is the "
          "one that says a file grew; a rewrite in place grows nothing while it writes, and shows here as "
          "position without size. A path that ends `(deleted)` still holds its blocks until the last "
          "descriptor closes. Only descriptors open at both samples get a row: a file written and closed "
          "inside the interval leaves none.", file=sys.stderr)


def cmd_dangling(args: argparse.Namespace) -> None:
    """Symlinks whose target is gone (`find -xtype l`): each one is a tree that was deleted under
    a user of it. One sweep of this box found 32,659 of them on 2026-10-30."""
    per_dir: dict[str, int] = {}
    samples: list[str] = []
    total = examined = 0
    truncated = False
    for root in args.roots:
        root = str(Path(root).resolve())
        try:
            dev = os.lstat(root).st_dev
        except OSError as error:
            print(f"{root}: {error}", file=sys.stderr)
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            try:
                if os.lstat(dirpath).st_dev != dev:  # one filesystem, as `du -x` and `find -xdev`
                    dirnames[:] = []
                    continue
            except OSError:
                continue
            for name in dirnames + filenames:
                p = os.path.join(dirpath, name)
                examined += 1
                if examined > args.max_entries:
                    truncated = True
                    break
                try:
                    if not os.path.islink(p) or os.path.exists(p):
                        continue
                except OSError:
                    continue
                total += 1
                per_dir[dirpath] = per_dir.get(dirpath, 0) + 1
                if len(samples) < args.top:
                    samples.append(f"{p} -> {os.readlink(p)}")
            if truncated:
                break
        if truncated:
            break
    for d, n in sorted(per_dir.items(), key=lambda kv: kv[1], reverse=True)[:args.top]:
        print(f"{n:9} {d}")
    if args.list:
        print("\n".join(samples))
    print(f"\n{total} dangling symlinks in {len(per_dir)} directories, {examined} entries examined"
          f"{' (stopped at --max-entries)' if truncated else ''}.", file=sys.stderr)
    print("A dangling symlink holds no data: this is a count of broken references, never bytes to "
          "reclaim. A link whose target this uid cannot stat reads as dangling too.", file=sys.stderr)


def du_tree(roots: list[str], max_depth: int) -> dict[str, int]:
    """path -> bytes, from ONE du over every root, so an inode hardlinked into two of them is
    counted once. du remembers an inode only within one run, so a run per root counts it again."""
    r = subprocess.run(["ionice", "-c3", "nice", "-n19", "du", "-xB1", f"--max-depth={max_depth}",
                        "--null", "--", *roots], capture_output=True)
    sizes: dict[str, int] = {}
    for rec in r.stdout.split(b"\0"):
        size, _, path = rec.decode("utf-8", "surrogateescape").partition("\t")
        if size.isdigit() and path:
            sizes[path] = int(size)
    return sizes


def exclusive_sizes(sizes: dict[str, int]) -> dict[str, int]:
    """Each listed directory less the listed directories under it, so a set of rows can be added
    up. du's own output cannot be: it prints a parent and its children both, and a `sort -rn |
    head` over it mixes levels — one such list made the top fifteen rows 210% of the total."""
    children: dict[str, int] = dict.fromkeys(sizes, 0)
    for path in sizes:
        parent = os.path.dirname(path)
        while parent and parent != "/" and parent not in sizes:
            parent = os.path.dirname(parent)
        if parent != path and parent in sizes:
            children[parent] += sizes[path]
    return {p: sizes[p] - children[p] for p in sizes}


def cmd_sizes(args: argparse.Namespace) -> None:
    """Directories ranked by bytes that are theirs alone, from one du over every root."""
    roots = [str(Path(r).resolve()) for r in args.roots]
    started = time.time()
    sizes = du_tree(roots, args.max_depth)
    if not sizes:
        sys.exit("du produced nothing: unreadable roots?")
    union = sum(sizes[r] for r in roots if r in sizes)
    rows = sorted(exclusive_sizes(sizes).items(), key=lambda kv: kv[1], reverse=True)[:args.top]
    print(f"{'own GB':>9} {'tree GB':>9}  path")
    for path, own in rows:
        print(f"{_gb(own):>9} {_gb(sizes[path]):>9}  {path}")
    shown = sum(own for _, own in rows)
    print(f"\n{len(sizes)} directories to depth {args.max_depth} in {time.time() - started:.0f} s. The rows "
          f"shown sum to {_gb(shown)} GB, {100 * shown / union:.0f}% of the {_gb(union)} GB total: 'own GB' "
          f"holds no bytes of the listed directories below it, so these rows add up and their sum can never "
          f"pass the total.", file=sys.stderr)
    if args.hardlinks:
        separate = 0
        for root in roots:
            r = subprocess.run(["ionice", "-c3", "nice", "-n19", "du", "-sxB1", "--", root], capture_output=True)
            separate += int(r.stdout.split(b"\t")[0] or 0)
        print(f"one du over every root: {_gb(union)} GB. One du per root, added up: {_gb(separate)} GB. The "
              f"{_gb(separate - union)} GB between them is hardlinked into more than one root and counted "
              f"once per root by separate runs. In the one-run column those shared bytes sit under the root "
              f"du reached first, so a copy that shares every file with an earlier root reads as nearly "
              f"empty, which is what sharing means and not what the copy would free.", file=sys.stderr)


def cmd_recent(args: argparse.Namespace) -> None:
    """Files that appeared or changed inside a window, counted whatever their size."""
    cutoff = time.time() - parse_duration(args.since)
    per_dir: dict[str, list[int]] = {}
    biggest = (0, "none")
    files = total = 0
    for root in args.roots:
        root = str(Path(root).resolve())
        try:
            dev = os.lstat(root).st_dev
        except OSError as error:
            print(f"{root}: {error}", file=sys.stderr)
            continue
        depth0 = root.count("/")
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            try:
                if os.lstat(dirpath).st_dev != dev:
                    dirnames[:] = []
                    continue
            except OSError:
                continue
            key = "/".join(dirpath.split("/")[:depth0 + args.group_depth + 1])
            for name in filenames:
                try:
                    st = os.lstat(os.path.join(dirpath, name))
                except OSError:
                    continue
                if (st.st_mtime if args.use_mtime else st.st_ctime) < cutoff:
                    continue
                size = st.st_blocks * 512
                files += 1
                total += size
                row = per_dir.setdefault(key, [0, 0])
                row[0] += 1
                row[1] += size
                biggest = max(biggest, (size, os.path.join(dirpath, name)))
    print(f"{'GB':>9} {'files':>9}  directory")
    for key, (n, size) in sorted(per_dir.items(), key=lambda kv: kv[1][1], reverse=True)[:args.top]:
        print(f"{_gb(size):>9} {n:9}  {key}")
    print(f"\n{files} files, {_gb(total)} GB, {'mtime' if args.use_mtime else 'ctime'} inside {args.since}; "
          f"the biggest is {_gb(biggest[0])} GB ({biggest[1]}). Every file counts, whatever its size: a flood "
          f"of small files is invisible to `find -size +500M` and fills a disk as well as one large file.",
          file=sys.stderr)
    if not args.use_mtime:
        print("ctime is when the inode last changed HERE: a create, a rename into place, a chmod, a change of "
              "link count. No copy can carry it in, which is why mtime cannot date an arrival — gzip, cp -p, "
              "tar -p and rsync -a all copy mtime from the source. ctime cannot tell a file created now from "
              "one chmod'ed now, and a file rewritten in place moves neither of them.", file=sys.stderr)


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("procs")
    p = sub.add_parser("protected")
    p.add_argument("--fixed", help='fixed entries file: {"protected": [...], "anchors": [...]} (extra keys ignored)')
    p.add_argument("--knives-registry", default="~/.config/knives/repos.toml")
    p = sub.add_parser("inventory")
    p.add_argument("--repo", required=True)
    p.add_argument("--root", action="append")
    p.add_argument("--trunk", default="trunk()")
    p.add_argument("--max-depth", type=int, default=WALK_DEPTH)
    p.add_argument("--box-dir", default="auto",
                   help="where absence can be judged: auto (inside an agentbox, its own ~/boxes/<host> dir; "
                        "on the host, everywhere), none (host semantics), or a directory")
    p = sub.add_parser("plan")
    p.add_argument("--inventory")
    p.add_argument("--protected")
    p.add_argument("--releases")
    p.add_argument("--fresh-hours", type=float, default=24.0)
    p.add_argument("--skip-pushed", action="store_true")
    p = sub.add_parser("apply")
    p.add_argument("--plan", required=True)
    p.add_argument("--protected", required=True)
    p.add_argument("--ledger", required=True)
    p.add_argument("--io-limit", type=float, default=20.0)
    p.add_argument("--releases")
    p = sub.add_parser("release-capture")
    p.add_argument("--path", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--kind", required=True, choices=["git", "jj"])
    p.add_argument("--name")
    p.add_argument("--session")
    p.add_argument("--message")
    p.add_argument("--trunk")
    p.add_argument("--releases", help="append the printed entry to this releases file (locked)")
    p = sub.add_parser("tmp")
    p.add_argument("--dir", default="/tmp")
    p.add_argument("--older-than-hours", type=float, default=24.0)
    p.add_argument("--families", default=DEFAULT_FAMILIES)
    p.add_argument("--protected")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--ledger")
    p.add_argument("--io-limit", type=float, default=20.0)
    sub.add_parser("containers")
    p = sub.add_parser("images")
    p.add_argument("--older-than-hours", type=float, default=48.0)
    p = sub.add_parser("writers", help="processes by write rate over an interval, with their cwd")
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--argv", action="store_true", help="print each process's command line too")
    p = sub.add_parser("cpu", help="CPU over an interval, grouped by command name")
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--top", type=int, default=15)
    p = sub.add_parser("fdwrites", help="open files that grow, by file-position and size deltas")
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--pid", action="append", help="limit to these pids (default: every process)")
    p.add_argument("--all-modes", action="store_true", help="include read-only descriptors")
    p = sub.add_parser("dangling", help="symlinks whose target is gone (`find -xtype l`)")
    p.add_argument("roots", nargs="+")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--list", action="store_true", help="print the first --top links with their targets")
    p.add_argument("--max-entries", type=int, default=2_000_000)
    p = sub.add_parser("sizes", help="directories by bytes that are theirs alone, from one du")
    p.add_argument("roots", nargs="+")
    p.add_argument("--max-depth", type=int, default=2)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--hardlinks", action="store_true",
                   help="also size each root on its own, to measure the bytes they share (a second walk)")
    p = sub.add_parser("recent", help="files that appeared inside a window, counted whatever their size")
    p.add_argument("roots", nargs="+")
    p.add_argument("--since", default="2h", help="window: 90s, 15m, 2h, 3d (default 2h)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--group-depth", type=int, default=1, help="levels below each root to group by")
    p.add_argument("--use-mtime", action="store_true",
                   help="date files by mtime, which a copy carries in from the source, instead of ctime")
    args = ap.parse_args()
    if args.cmd == "procs":
        json.dump(sorted(proc_cwds()), sys.stdout, indent=1)
    elif args.cmd == "protected":
        cmd_protected(args)
    elif args.cmd == "inventory":
        cmd_inventory(args)
    elif args.cmd == "plan":
        cmd_plan(args)
    elif args.cmd == "apply":
        if not args.ledger:
            sys.exit("--ledger is required")
        cmd_apply(args)
    elif args.cmd == "release-capture":
        cmd_release_capture(args)
    elif args.cmd == "tmp":
        if args.apply and not args.ledger:
            sys.exit("--apply requires --ledger")
        cmd_tmp(args)
    elif args.cmd == "containers":
        cmd_containers(args)
    elif args.cmd == "images":
        cmd_images(args)
    else:
        globals()[f"cmd_{args.cmd}"](args)  # the instruments: writers, cpu, fdwrites, dangling, sizes, recent


if __name__ == "__main__":
    main()
