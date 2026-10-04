#!/usr/bin/env python3
"""Extract Sami's Dispatch events (comments, ask answers, messages) since a timestamp.

Ask answers carry free text and spec comments are where design corrections happen; the
transcript extractor never sees either. Reads every issue of every project, keeps issues
updated at or after --since, pages each one's events, and keeps events by the actor whose id
starts with --actor. Writes JSONL (one event per line, the input of classify-sami-events.py)
and a readable transcript in time order with the ask question beside each answer. D<n> in the
transcript is the event's 1-based position in created_at order, the classifier's DN.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE_URL = "https://dispatch.internal.trajectorylabs.com/api/v1"
ENVOY_CONFIG = Path.home() / ".config" / "opencode" / "envoy.json"
KEEP = {"comment.created", "ask.answered", "message.created", "comment.edited", "ask.created"}


def token() -> str:
    return json.loads(ENVOY_CONFIG.read_text())["dispatch"]["token"]


def get(path: str):
    req = urllib.request.Request(f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {token()}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def projects() -> list[str]:
    data = get("/projects")
    items = data.get("projects", data) if isinstance(data, dict) else data
    return [p["key"] for p in items]


def issues(project: str, since: str) -> list[str]:
    keys, offset = [], 0
    while True:
        page = get(f"/issues?project={project}&limit=250&offset={offset}")
        rows = page["issues"]
        keys += [r["key"] for r in rows if (r.get("updated_at") or "") >= since]
        offset += len(rows)
        if not rows or offset >= page.get("total", 0):
            return keys


def events(key: str) -> list[dict]:
    out, cursor = [], None
    while True:
        q = "limit=200" + ("" if cursor is None else f"&after={cursor}")
        page = get(f"/issues/{key}/events?{q}")
        out += page
        if len(page) < 200:
            return out
        cursor = page[-1]["seq"]


def render(e: dict) -> str:
    p = e.get("payload") or {}
    if e["type"] == "ask.answered":
        a = p.get("answer") or {}
        q = p.get("question") or ""
        picked = ", ".join(a.get("selected") or [])
        return f"ASK: {q}\nPICKED: {picked}\nTEXT: {a.get('text') or ''}"
    body = p.get("body") or p.get("text") or ""
    quote = (p.get("anchor") or {}).get("quote") if isinstance(p.get("anchor"), dict) else None
    ask_q = p.get("ask_question")
    head = f"ON ASK: {ask_q}\n" if ask_q else (f"ON QUOTE: {quote}\n" if quote else "")
    return head + body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", required=True, help="ISO timestamp, e.g. 2026-09-20T17:00:00Z")
    ap.add_argument("--out", required=True, help="JSONL output")
    ap.add_argument("--text", required=True, help="readable transcript output")
    ap.add_argument("--actor", default="sami@", help="keep events whose actor id starts with this")
    args = ap.parse_args()

    keys: list[str] = []
    for project in projects():
        keys += issues(project, args.since)
    with ThreadPoolExecutor(16) as pool:
        all_events = [e for evs in pool.map(events, keys) for e in evs]
    human = sorted(
        (e for e in all_events
         if (e.get("actor") or {}).get("kind") == "user"
         and (e.get("actor") or {}).get("id", "").startswith(args.actor)
         and e["type"] in KEEP and e["created_at"] >= args.since),
        key=lambda e: e["created_at"],
    )
    with open(args.out, "w") as f:
        for e in human:
            f.write(json.dumps(e) + "\n")
    with open(args.text, "w") as f:
        for n, e in enumerate(human, 1):
            f.write(f"--- D{n} {e['created_at'][:16]} {e['issue_key']} {e['type']}\n{render(e)}\n\n")
    print(f"{len(keys)} issues updated since {args.since}; {len(all_events)} events; {len(human)} kept")


if __name__ == "__main__":
    main()
