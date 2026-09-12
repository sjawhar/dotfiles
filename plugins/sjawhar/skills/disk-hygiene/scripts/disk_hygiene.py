#!/usr/bin/env python3
"""Mechanical half of the disk-hygiene skill. Stdlib only; safe to run anywhere.

Subcommands (all read-only except `apply`, and `tmp --apply`):

  procs                          JSON list of every process's cwd (liveness evidence)
  inventory --repo R [--root D]  every working copy of jj repo R found under roots D
                                 (default: $HOME), classified against trunk/remotes
  plan --inventory I --protected P [--fresh-hours H] [--skip-pushed]
                                 turn an inventory into a deletion plan JSON
  apply --plan P --protected PR --ledger L [--io-limit PCT]
                                 paced executor: one item at a time, ionice idle,
                                 re-reads the protected file and live cwds per item
  tmp --dir D --older-than-hours H [--families REGEX] [--protected P] [--apply --ledger L]
                                 stale temp-dir families by directory mtime
  containers                     running containers with compose project, age, and
                                 the processes whose cmdline names them (holders)
  images [--older-than-hours H]  tagged images no container uses, older than H (cheap;
                                 avoids `docker system df -v`, which stalls under IO load)

Protected file format: {"protected": ["/abs/path", ...]}. Prefix semantics both
ways: an item is protected if it is under a protected path OR a protected path is
under it. Plan items may set "subpath_release": true to bypass the prefix guard
for a path an owner explicitly released inside a live tree; the live-process
check still applies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
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


def procs_under(path: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
            if cwd == path or cwd.startswith(path + "/"):
                hits.append((int(pid), Path(f"/proc/{pid}/comm").read_text().strip()))
        except OSError:
            pass
    return hits


def io_full_avg10() -> float:
    for line in Path("/proc/pressure/io").read_text().splitlines():
        if line.startswith("full"):
            return float(line.split("avg10=")[1].split()[0])
    return 0.0


def df_used_gb() -> float:
    st = os.statvfs("/")
    return round((st.f_blocks - st.f_bfree) * st.f_frsize / 1e9, 1)


def ledger_write(path: Path, entry: dict[str, Any]) -> None:
    entry["t"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry["df_used_gb"] = df_used_gb()
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    print(json.dumps(entry), flush=True)


def is_protected(path: str, protected: list[str]) -> str | None:
    for d in protected:
        if path == d or path.startswith(d + "/") or d.startswith(path + "/"):
            return d
    return None


def load_protected(path: str | None) -> list[str]:
    if not path:
        return []
    data: dict[str, Any] = json.load(open(path))
    return [str(p) for p in data["protected"]]


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


# ---------------------------------------------------------------- inventory

def cmd_inventory(args: argparse.Namespace) -> None:
    repo = str(Path(args.repo).resolve())
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

    live = proc_cwds()
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
            cls = "STALE_REGISTRATION"
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
    # registrations with no directory found
    found_names = {r["name"] for r in rows if r["name"]}
    for name in sorted(registered - found_names - {"default"}):
        rows.append({"path": None, "name": name, "exists": False, "registered": True, "class": "STALE_REGISTRATION"})
    json.dump({"repo": repo, "trunk": trunk, "rows": rows}, sys.stdout, indent=1)
    print(file=sys.stderr)
    print(Counter(str(r["class"]) for r in rows), file=sys.stderr)


# ---------------------------------------------------------------- plan

def cmd_plan(args: argparse.Namespace) -> None:
    inv: dict[str, Any] = json.load(open(args.inventory))
    protected = load_protected(args.protected)
    plan: list[dict[str, Any]] = []
    for r in inv["rows"]:
        path, cls = r.get("path"), r["class"]
        if not path or cls in ("LIVE", "UNPUSHED", "GIT_UNPUSHED", "STALE_REGISTRATION"):
            continue
        if is_protected(path, protected):
            continue
        idle = r.get("jj_idle_hours")
        if cls in ("MERGED", "PUSHED") and idle is not None and idle < args.fresh_hours:
            continue
        if cls == "PUSHED" and args.skip_pushed:
            continue
        if cls == "GIT_HEAD_PUSHED" and not args.include_git_worktrees:
            continue  # dirty-tree state is unchecked (git status is slow); opt in after checking
        kind = "git" if r.get("git_only") else "jj"
        reason = {
            "MERGED": "all non-empty ancestors in trunk; @ empty",
            "PUSHED": f"all commits on remote bookmarks {r.get('bookmarks')}",
            "UNREGISTERED": "jj already forgot this workspace; directory is residue",
            "GIT_HEAD_PUSHED": "git worktree whose HEAD is on a remote ref (uncommitted edits unchecked)",
        }[cls]
        plan.append({"path": path, "name": r.get("name"), "kind": kind, "repo": inv["repo"], "reason": f"{reason}; jj idle {idle}h"})
    json.dump({"plan": plan, "protected": protected}, sys.stdout, indent=1)
    print(f"\n{len(plan)} items", file=sys.stderr)


# ---------------------------------------------------------------- apply

def cmd_apply(args: argparse.Namespace) -> None:
    plan: list[dict[str, Any]] = json.load(open(args.plan))["plan"]
    ledger = Path(args.ledger)
    ledger_write(ledger, {"op": "apply-start", "items": len(plan), "plan": args.plan})
    for item in plan:
        path, name, kind = item["path"], item.get("name"), item["kind"]
        repo = item.get("repo")
        if not os.path.lexists(path):
            ledger_write(ledger, {"op": "absent", "path": path, "name": name})
            continue
        protected = load_protected(args.protected)  # re-read: the agent may add to it mid-run
        hit = is_protected(path, protected)
        if hit and not item.get("subpath_release"):
            ledger_write(ledger, {"op": "skip", "path": path, "name": name, "reason": f"protected by {hit}"})
            continue
        pu = procs_under(path)
        if pu:
            ledger_write(ledger, {"op": "skip", "path": path, "name": name, "reason": f"live processes {pu[:4]}"})
            continue
        waited = 0
        while io_full_avg10() > args.io_limit:
            time.sleep(15)
            waited += 15
        if waited:
            ledger_write(ledger, {"op": "paced", "path": path, "waited_s": waited})
        forgot = None
        if kind == "jj" and name and repo:
            rc, out = run(JJ + ["workspace", "list"], cwd=repo)
            if name in {line.split(":")[0] for line in out.splitlines() if ":" in line}:
                rc, out = run(JJ + ["workspace", "forget", name], cwd=repo)
                forgot = [rc, out[-200:]]
                if rc != 0:
                    ledger_write(ledger, {"op": "error", "path": path, "name": name, "stage": "forget", "out": out[-400:]})
                    continue
        if kind == "git" and repo:
            rc, out = run(["ionice", "-c3", "nice", "-n19", "git", "-C", repo, "worktree", "remove", "--force", path], timeout=1800)
            if rc == 0:
                ledger_write(ledger, {"op": "removed", "path": path, "kind": kind, "reason": item.get("reason")})
                continue
            forgot = ["git worktree remove failed; falling back to rm + prune", out[-200:]]
        r = subprocess.run(RM + [path], capture_output=True, text=True)
        if r.returncode != 0:
            ledger_write(ledger, {"op": "error", "path": path, "name": name, "stage": "rm", "out": r.stderr[-400:]})
            continue
        if kind == "git" and repo:
            run(["git", "-C", repo, "worktree", "prune"])
        ledger_write(ledger, {"op": "removed", "path": path, "name": name, "kind": kind, "reason": item.get("reason"), "forgot": forgot})
    ledger_write(ledger, {"op": "apply-complete", "items": len(plan)})


# ---------------------------------------------------------------- tmp

DEFAULT_FAMILIES = r"^(tmp\.[A-Za-z0-9]{10}$|pytest-of-|xvfb-run\.|tl-preview-|worker-crash-|vscode-smoke-|.*-review[.-]|review-\d+|pr\d+-|agent-c-pr\d+|core-venv|compose-venv|\w+-home\d*$)"


def cmd_tmp(args: argparse.Namespace) -> None:
    fam = re.compile(str(args.families))
    tmp_dir = str(args.dir)
    protected = load_protected(args.protected)
    live = proc_cwds()
    now = time.time()
    targets: list[str] = []
    for name in sorted(os.listdir(tmp_dir)):
        p = os.path.join(tmp_dir, name)
        if not os.path.isdir(p) or os.path.islink(p) or not fam.match(name):
            continue
        if now - os.lstat(p).st_mtime < args.older_than_hours * 3600:
            continue
        if is_protected(p, protected) or any(c == p or c.startswith(p + "/") for c in live):
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
        if any(c == p or c.startswith(p + "/") for c in proc_cwds()):
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

def cmd_containers(_args: argparse.Namespace) -> None:
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
    result: list[dict[str, Any]] = []
    for d in data:
        name = str(d["Name"]).lstrip("/")
        labels: dict[str, str] = d["Config"].get("Labels") or {}
        proj = labels.get("com.docker.compose.project", "")
        needles = {n for n in (name, proj) if n}
        holders = [(pid, c[:120]) for pid, c in cmdlines if any(n in c for n in needles) and "disk_hygiene" not in c]
        result.append({
            "name": name, "status": d["State"]["Status"], "started": d["State"].get("StartedAt", "")[:19],
            "compose_project": proj, "compose_working_dir": labels.get("com.docker.compose.project.working_dir", ""),
            "holders": holders[:5],
        })
    json.dump(result, sys.stdout, indent=1)
    print(f"\n{len(result)} containers; {sum(1 for r in result if r['status']=='running' and not r['holders'])} running with no process naming them", file=sys.stderr)


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
    p = sub.add_parser("inventory")
    p.add_argument("--repo", required=True)
    p.add_argument("--root", action="append")
    p.add_argument("--trunk", default="trunk()")
    p.add_argument("--max-depth", type=int, default=6)
    p = sub.add_parser("plan")
    p.add_argument("--inventory", required=True)
    p.add_argument("--protected")
    p.add_argument("--fresh-hours", type=float, default=1.0)
    p.add_argument("--skip-pushed", action="store_true")
    p.add_argument("--include-git-worktrees", action="store_true")
    p = sub.add_parser("apply")
    p.add_argument("--plan", required=True)
    p.add_argument("--protected", required=True)
    p.add_argument("--ledger", required=True)
    p.add_argument("--io-limit", type=float, default=20.0)
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
    elif args.cmd == "inventory":
        cmd_inventory(args)
    elif args.cmd == "plan":
        cmd_plan(args)
    elif args.cmd == "apply":
        if not args.ledger:
            sys.exit("--ledger is required")
        cmd_apply(args)
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
