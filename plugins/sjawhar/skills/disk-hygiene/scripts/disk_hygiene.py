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
                                 item's class and disk-vs-store divergence before acting
  release-capture --path P --repo R --kind git|jj [--name N] [--releases R]
                                 print an owner-release entry binding the slot's
                                 identity (HEAD hash + worktree admin id + dir mtime);
                                 --releases appends it to the releases file (locked)
  tmp --dir D --older-than-hours H [--families REGEX] [--protected P] [--apply --ledger L]
                                 stale temp-dir families by directory mtime
  containers                     every container with family (platform-e2e, local-stack, ryuk,
                                 tc-fixture, agentbox, other), owner attribution, liveness verdict
  images [--older-than-hours H]  tagged images no container uses, older than H (cheap;
                                 avoids `docker system df -v`, which stalls under IO load)

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
from typing import Any

JJ = ["jj", "--ignore-working-copy", "--config", "fsmonitor.backend=none"]
RM = ["sudo", "ionice", "-c3", "nice", "-n19", "rm", "-rf", "--one-file-system"]


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


def df_used_gb() -> float:
    st = os.statvfs("/")
    return round((st.f_blocks - st.f_bfree) * st.f_frsize / 1e9, 1)


def df_free_gb() -> float:
    """Space available to an unprivileged writer, which is what a reclaim threshold decides
    on: f_bavail excludes the reserved blocks that f_bfree counts and nobody here can use."""
    st = os.statvfs("/")
    return round(st.f_bavail * st.f_frsize / 1e9, 1)


def ledger_write(path: Path, entry: dict[str, Any]) -> None:
    entry["t"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry["df_used_gb"] = df_used_gb()
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


def holds_working_copy(path: Path, depth: int = 3, dir_cap: int = 5000) -> bool | None:
    """Does a `.jj` or `.git` exist at or below <path>, down to <depth> levels? None when the
    scan could not finish (over <dir_cap> directories, or an unreadable one): unverifiable, so
    never scratch. A depth-1 check planned ~/.worktrees/<repo>/ containers of other repos'
    worktrees as scratch rm (8 of them, SRE, 2026-09-26). Raises OSError only for <path> itself."""
    frontier, seen = [(path, 0)], 0
    while frontier:
        d, level = frontier.pop()
        seen += 1
        if seen > dir_cap:
            return None
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            if d == path:
                raise
            return None
        for e in entries:
            if e.name in (".jj", ".git"):
                return True
            if level < depth and e.is_dir(follow_symlinks=False):
                frontier.append((Path(e.path), level + 1))
    return False


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
            dn[:] = [d for d in dn if not d.startswith(".") or d in (".worktrees", ".omp", ".local", ".cache")]
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
                held = holds_working_copy(entry)
                idle_h = round((time.time() - entry.stat().st_mtime) / 3600, 1)
            except OSError:
                continue  # unreadable (another user's dir, EACCES): no evidence, so no row (D1)
            live_procs = sorted({c for c in live if c == path or c.startswith(path + "/")})[:3]
            cls = ("LIVE" if live_procs else "HOLDS_WORKING_COPY" if held
                   else "SCRATCH_UNVERIFIED" if held is None else "SCRATCH")
            rows.append({"path": path, "name": None, "exists": True, "is_git_worktree": False,
                         "live_procs": live_procs, "jj_idle_hours": idle_h, "registered": False,
                         "class": cls})
    # registrations with no directory found
    found_names = {r["name"] for r in rows if r["name"]}
    for name in sorted(registered - found_names - {"default"}):
        # With no path to test, a registration the walk did not find is judgeable only on the
        # host; inside a box it is most often another box's live workspace.
        rows.append({"path": None, "name": name, "exists": False, "registered": True,
                     "class": "STALE_REGISTRATION" if obs is None else "UNOBSERVABLE"})
    json.dump({"repo": repo, "trunk": trunk, "rows": rows}, sys.stdout, indent=1)
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
    if args.inventory:
        inv: dict[str, Any] = json.load(open(args.inventory))
        trunk, repo = inv["trunk"], inv["repo"]
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
               "trunk": trunk, "fresh_hours": args.fresh_hours}, sys.stdout, indent=1)
    print(f"\n{len(plan)} items, {len(refused)} refused", file=sys.stderr)


# ---------------------------------------------------------------- apply

def _pace(io_limit: float, ledger: Path, path: str | None) -> None:
    waited = 0
    while io_full_avg10() > io_limit:
        time.sleep(15)
        waited += 15
    if waited:
        ledger_write(ledger, {"op": "paced", "path": path, "waited_s": waited})


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


def _apply_released(args: argparse.Namespace, item: dict[str, Any], trunk: str, ledger: Path) -> None:
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
    args: argparse.Namespace, item: dict[str, Any], trunk: str, ledger: Path, fresh_hours: float = 24.0
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
    if item["kind"] == "scratch":
        # A plain directory's evidence is age + liveness + "not a working copy", and all three
        # are re-derived HERE because the plan can be an hour old: a directory written to since
        # the plan, or one that has become a workspace, must survive.
        try:
            held = holds_working_copy(Path(path))
        except OSError as error:
            skip(f"re-verification failed: contents unreadable ({error})")
            return
        if held is not False:
            skip("re-verification failed: " + ("holds a jj/git working copy, not scratch" if held
                                                else "working copy scan could not finish: unverifiable"))
            return
        try:
            idle_h = (time.time() - os.stat(path).st_mtime) / 3600
        except OSError as error:
            skip(f"re-verification failed: freshness unreadable ({error})")
            return
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
        for item in plan:
            if item["kind"] == "forget":
                _apply_forget(item, trunk, fresh_hours, ledger)
            elif item["kind"] == "released":
                _apply_released(args, item, trunk, ledger)
            else:
                _apply_path_item(args, item, trunk, ledger, fresh_hours)
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
        if now - os.lstat(p).st_mtime < args.older_than_hours * 3600:
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
    `agentbox-<id>`; the shared host daemon reports `sami-agents`. A query that fails (timeout,
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
      other         no known label/name shape. On a BOX-LOCAL daemon (`docker info --format
                    '{{.Name}}'` starts with "agentbox-") no other box's container can ever exist,
                    so a container that is `created`, has State.Pid 0, State.StartedAt at docker's
                    zero time, is older than 1h, and has no holders is leaked -- it can only be this
                    box's own dead fixture. On the shared host daemon ("sami-agents") other boxes'
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

def cmd_images(args: argparse.Namespace) -> None:
    """Tagged images no container references, older than N hours. Cheap: no `docker system df`."""
    rc, cids = run(["timeout", "120", "docker", "ps", "-aq", "--no-trunc"])
    used: set[str] = set()
    if cids.strip():
        r = subprocess.run(["timeout", "120", "docker", "inspect", "-f", "{{.Image}}", *cids.split()], capture_output=True, text=True)
        used = {line.strip() for line in r.stdout.splitlines() if line.strip()}
    rc, out = run(["timeout", "120", "docker", "images", "--no-trunc", "--format", "{{.ID}}\t{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}\t{{.Size}}"])
    if rc != 0:
        sys.exit(out)
    cutoff = time.time() - args.older_than_hours * 3600
    rows: list[str] = []
    for line in out.splitlines():
        iid, ref, created, size = line.split("\t")
        if iid in used or ref.startswith("<none>"):
            continue
        # CreatedAt looks like "2026-09-10 21:28:03 +0000 UTC"
        ts = time.mktime(time.strptime(created[:19], "%Y-%m-%d %H:%M:%S")) - time.timezone
        if ts > cutoff:
            continue
        rows.append(f"{size:>8}  {created[:16]}  {ref}")
    print("\n".join(sorted(rows)))
    print(f"\n{len(rows)} unused tagged images older than {args.older_than_hours}h; remove with plain `docker rmi <ref>` (a refusal means a container uses it)", file=sys.stderr)


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
    p.add_argument("--max-depth", type=int, default=6)
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


if __name__ == "__main__":
    main()
