#!/usr/bin/env python3
"""Copy Legion's agent sessions out of the cluster and list Sami's Dispatch messages in them, as
rows for enumerate-messages.py --legion. Read-only on the cluster.

    fetch-legion.py --copy-to DIR --from '2026-10-20' --to '2026-10-25' --out legion-rows.jsonl
    fetch-legion.py --sessions DIR ...      # reuse an earlier copy

Where the sessions are: Legion keeps them as files on each issue's volume (session_store `pvc`,
the default), not in its Postgres, which holds only the daemon's issue records. Inside an issue
pod they are under /home/legion/.omp/profiles/legion/agent/sessions/. This copies them with
`kubectl exec ... tar` from every running pod that has an `architect` container (one per issue;
the role containers share the volume) and from each `*-controller` pod, in namespace `legion` of
--context. An issue whose pod is gone keeps its volume but no pod mounts it: those sessions are
NOT read, and the script prints how many volumes it could not reach.

Legion pods run without a terminal, so every `user` row is a prompt the daemon wrote; only
Dispatch deliveries (an `envoy-message` whose envelope names a `dispatch:` event with an actor of
kind `user`) are Sami's. Subagent transcripts (files inside a session's own directory) are skipped.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SESS = "/home/legion/.omp/profiles/legion/agent/sessions"
DISPATCH_USER = re.compile(r"\n  dispatch:\n(?:    [^\n]*\n)*?    actor:\n(?:      [^\n]*\n)*?      kind: user(?:\n|$)")
WORDS = re.compile(r"\n(?:    (?:answer|reply)|      (?:body|reason)): ([^\n]*)")
MAIN_FILE = re.compile(r"\d{4}-\d\d-\d\dT.*_([0-9a-f-]{36})\.jsonl$")


def kubectl(ctx: str, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", "--context", ctx, "-n", "legion", *args], **kw)


def copy(ctx: str, dest: Path) -> None:
    pods = json.loads(kubectl(ctx, "get", "pods", "-o", "json", capture_output=True, check=True, text=True).stdout)
    mounted, total = set(), set()
    for p in pods["items"]:
        name = p["metadata"]["name"]
        containers = [c["name"] for c in p["spec"]["containers"]]
        claims = [v["persistentVolumeClaim"]["claimName"] for v in p["spec"].get("volumes", []) if v.get("persistentVolumeClaim")]
        if p["status"].get("phase") != "Running":
            continue
        if "architect" in containers:
            container = "architect"
        elif name.endswith("-controller") and "controller" in containers:
            container = "controller"
        else:
            continue
        mounted.update(claims)
        out = dest / name
        out.mkdir(parents=True, exist_ok=True)
        tar = kubectl(ctx, "exec", name, "-c", container, "--", "tar", "-C", SESS, "-cf", "-", ".",
                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        subprocess.run(["tar", "-C", str(out), "-xf", "-"], input=tar.stdout, check=False)
    pvcs = json.loads(kubectl(ctx, "get", "pvc", "-o", "json", capture_output=True, check=True, text=True).stdout)
    total = {c["metadata"]["name"] for c in pvcs["items"] if c["metadata"]["name"].startswith("issue-")}
    unread = sorted(total - mounted)
    print(f"copied from running pods; {len(unread)} of {len(total)} issue volumes have no running pod and were "
          f"NOT read: {' '.join(unread)}", file=sys.stderr)


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if isinstance(p, dict))


def unquote(v: str) -> str:
    if v.startswith('"'):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return "" if v == "null" else v


def field(text: str, key: str) -> str | None:
    m = re.search(rf"\n {{2,6}}{key}: ([^\n]*)", text)
    return m.group(1).strip() if m else None


def rows(root: Path, start: str, end: str, actor: re.Pattern) -> list[dict]:
    out, seen = [], set()
    for f in sorted(root.rglob("*.jsonl")):
        m = MAIN_FILE.match(f.name)
        if not m:
            continue
        sid, title = m.group(1), None
        for n, line in enumerate(f.open(), 1):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("type") in ("title", "title_change"):
                title = r.get("title") or title
            if r.get("customType") != "envoy-message":
                continue
            ts = (r.get("timestamp") or "").replace("T", " ").rstrip("Z")
            if not (start <= ts < end):
                continue
            env = text_of(r.get("content"))
            actor_id = re.search(r"\n      id: ([^\n]*)", env)
            if not DISPATCH_USER.search(env) or not actor_id or not actor.search(actor_id.group(1)):
                continue
            did = field(env, "id")
            key = (sid, did)
            if key in seen:  # the same file copied from two pods
                continue
            seen.add(key)
            out.append({
                "source": "legion", "kind": "dispatch", "ts": ts, "host": f"legion:{f.relative_to(root).parts[0]}",
                "session_id": sid, "raw_file": str(f), "raw_line": n, "cwd": f.parent.name, "title": title,
                "text": "\n".join(w for w in (unquote(v) for v in WORDS.findall(env)) if w), "envelope": env,
                "dispatch_id": did, "issue": field(env, "owner"), "event_type": field(env, "type"),
                "excluded": None, "also_in": [],
            })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--copy-to", help="copy the cluster's sessions into this directory first")
    src.add_argument("--sessions", help="a directory an earlier --copy-to filled")
    ap.add_argument("--context", default="gke_theorem-agent-infra_us-west1_dispatch")
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--actor", default="^sami@")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = Path(a.copy_to or a.sessions)
    if a.copy_to:
        copy(a.context, root)
    found = rows(root, a.start, a.end, re.compile(a.actor))
    with open(a.out, "w") as f:
        for r in found:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(found)} Dispatch messages of Sami's in Legion sessions -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
