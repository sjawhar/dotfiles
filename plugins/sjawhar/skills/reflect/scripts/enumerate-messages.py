#!/usr/bin/env python3
"""List every message Sami sent agent sessions in a window: what he typed, and what he wrote in
Dispatch that reached a session. One JSON line per message, full text, nothing cut.

    enumerate-messages.py --from '2026-10-12 03:21' --to '2026-10-19 03:21' --out messages.jsonl
    enumerate-messages.py ... --legion legion-messages.jsonl   # add Legion's sessions (see below)

Sources:

- tq, typed: `role = 'user' AND kind = 'user'`. Never `user:agent` (a parent's brief, a relay).
- tq, Dispatch: a `custom:envoy-message` row whose envelope names a `dispatch:` event with an
  actor of kind `user` and an id matching --actor (tq's `--human` rule). One message per
  Dispatch event id, however many sessions it reached; the others are listed in `also_in`.
- Legion: Legion keeps its sessions as files on each issue's volume, not in tq. --legion takes a
  JSONL file of rows in this script's own row shape (`source: "legion"`), written by
  `fetch-legion.py`. A Dispatch event that reached both a tq session and a Legion pod is one
  message: the tq row keeps it and the Legion session is added to its `also_in`. Without
  --legion the run states that Legion was not read.

Every row the window holds is written. A row that is not Sami's own words carries `excluded`
with the reason, so the coverage check can count it and a reader can audit the rule:

- `restart-copy`: the same (ts, text) in a later session id (a restart copies a whole session).
- `agent-sent`: typed-role text an agent produced. The first 48 characters of its first line
  appear in an agent's tool call (tmux send-keys, `omp -p`, a written brief) within a day before
  it. Agent-launched reviewers and lane briefs arrive this way.
- `single-prompt`: the session's only typed row in its whole life. Scripted reviewers, gates,
  probes and `omp -p` launches arrive this way (911 of 1,563 typed rows, 10-12 to 10-19).
  A real one-message session of Sami's is excluded too; the report lists these rows' count.
- `pruned`: the transcript keeps only a `[shaken …]` placeholder; the text must be recovered from
  the named artifact before it can be read.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta

TQ = "tq"
DISPATCH_USER = "\n  dispatch:\n(?:    [^\n]*\n)*?    actor:\n(?:      [^\n]*\n)*?      kind: user(?:\n|$)"
WORD_FIELDS = re.compile(r"\n(?:    (?:answer|reply)|      (?:body|reason)): ([^\n]*)")
NEEDLE_LEN = 48


def sql_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def tq(query: str) -> list[dict]:
    out = subprocess.run([TQ, "sql", query, "--format", "JSONEachRow"], capture_output=True, check=True).stdout
    return [json.loads(line) for line in out.decode("utf-8", "replace").splitlines() if line.strip()]


def envelope_field(text: str, key: str) -> str | None:
    m = re.search(rf"\n {{2,6}}{key}: ([^\n]*)", text)
    return m.group(1).strip() if m else None


def unquote(value: str) -> str:
    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return "" if value == "null" else value


def dispatch_words(text: str) -> str:
    """The person's own words in a Dispatch envelope: answer, reply, body or reason (tq's said())."""
    found = [unquote(v) for v in WORD_FIELDS.findall(text)]
    return "\n".join(w for w in found if w)


def typed_rows(start: str, end: str) -> list[dict]:
    return tq(
        "SELECT m.session_id AS session_id, m.host AS host, toString(m.ts) AS ts, m.raw_file AS raw_file, "
        "m.raw_line AS raw_line, m.text AS text, s.parent_session AS parent_session, s.cwd AS cwd, s.title AS title "
        "FROM messages m LEFT JOIN sessions_latest s ON s.session_id = m.session_id "
        f"WHERE m.kind = 'user' AND m.role = 'user' AND m.ts >= {sql_str(start)} AND m.ts < {sql_str(end)} "
        "ORDER BY m.ts, m.session_id"
    )


def dispatch_rows(start: str, end: str, actor: str) -> list[dict]:
    return tq(
        "SELECT m.session_id AS session_id, m.host AS host, toString(m.ts) AS ts, m.raw_file AS raw_file, "
        "m.raw_line AS raw_line, m.text AS text, s.cwd AS cwd, s.title AS title "
        "FROM messages m LEFT JOIN sessions_latest s ON s.session_id = m.session_id "
        "WHERE m.kind = 'system' AND m.role = 'custom:envoy-message' "
        f"AND m.ts >= {sql_str(start)} AND m.ts < {sql_str(end)} AND match(m.text, {sql_str(DISPATCH_USER)}) "
        f"AND match(extract(m.text, '\\n      id: ([^\\n]*)'), {sql_str(actor)}) "
        "ORDER BY m.ts, m.session_id"
    )


def first_typed_line(session_ids: list[str]) -> dict[str, tuple[int, int]]:
    """Per session: (number of typed rows in its whole life, raw_line of the first)."""
    out = {}
    for i in range(0, len(session_ids), 500):
        ids = ",".join(sql_str(s) for s in session_ids[i : i + 500])
        for r in tq(
            "SELECT session_id, count() AS n, min(raw_line) AS first FROM messages "
            f"WHERE kind = 'user' AND role = 'user' AND session_id IN ({ids}) GROUP BY session_id"
        ):
            out[r["session_id"]] = (int(r["n"]), int(r["first"]))
    return out


def needle(text: str) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line[:NEEDLE_LEN]


def agent_sent(rows: list[dict], start: str, end: str) -> set[int]:
    """Indexes of rows whose opening text appears in an agent tool call in the day before them."""
    lo = (datetime.fromisoformat(start) - timedelta(days=1)).isoformat(sep=" ")
    hits: set[int] = set()
    todo = [(i, needle(r["text"])) for i, r in enumerate(rows)]
    todo = [(i, n) for i, n in todo if len(n) >= 16]
    for b in range(0, len(todo), 200):
        batch = todo[b : b + 200]
        arr = "[" + ",".join(sql_str(n) for _, n in batch) + "]"
        res = tq(
            f"SELECT arrayJoin(arrayFilter(x -> x > 0, arrayMap((p, k) -> if(p > 0, k, 0), "
            f"multiSearchAllPositions(command, {arr}), arrayEnumerate({arr})))) AS k, "
            "groupArray(toString(started_at)) AS at FROM calls "
            f"WHERE started_at >= {sql_str(lo)} AND started_at < {sql_str(end)} AND command IS NOT NULL "
            f"AND multiSearchAny(command, {arr}) GROUP BY k"
        )
        for r in res:
            i, _ = batch[int(r["k"]) - 1]
            ts = rows[i]["ts"]
            if any(a <= ts and a >= (datetime.fromisoformat(ts) - timedelta(days=1)).isoformat(sep=" ") for a in r["at"]):
                hits.add(i)
    return hits


def build(start: str, end: str, actor: str) -> tuple[list[dict], dict[str, dict]]:
    """Rows in window order, and the Dispatch rows by event id (so Legion rows dedupe against them)."""
    out: list[dict] = []
    typed = typed_rows(start, end)
    # Restart copies: keep the lowest session id (the original) for each (ts, text).
    first: dict[tuple, int] = {}
    for i, r in sorted(enumerate(typed), key=lambda x: x[1]["session_id"]):
        first.setdefault((r["ts"], r["text"]), i)
    life = first_typed_line(sorted({r["session_id"] for r in typed}))
    sent = agent_sent(typed, start, end)
    copies = defaultdict(list)
    for i, r in enumerate(typed):
        keep = first[(r["ts"], r["text"])]
        if keep != i:
            copies[keep].append(r["session_id"])
    for i, r in enumerate(typed):
        keep = first[(r["ts"], r["text"])]
        n, first_line = life.get(r["session_id"], (0, -1))
        excluded = None
        if keep != i:
            excluded = "restart-copy"
        elif r["text"].startswith("[shaken"):
            excluded = "pruned"
        elif i in sent:
            excluded = "agent-sent"
        elif n == 1 and r["raw_line"] == first_line:
            excluded = "single-prompt"
        out.append({
            "source": "tq", "kind": "typed", "ts": r["ts"], "host": r["host"], "session_id": r["session_id"],
            "raw_file": r["raw_file"], "raw_line": r["raw_line"], "cwd": r.get("cwd"), "title": r.get("title"),
            "text": r["text"], "excluded": excluded, "also_in": copies.get(i, []),
            "copy_of": typed[keep]["session_id"] if keep != i else None,
        })
    seen: dict[str, dict] = {}
    for r in dispatch_rows(start, end, actor):
        did = envelope_field(r["text"], "id") or f"{r['session_id']}:{r['raw_line']}"
        if did in seen:
            seen[did]["also_in"].append(r["session_id"])
            continue
        row = {
            "source": "tq", "kind": "dispatch", "ts": r["ts"], "host": r["host"], "session_id": r["session_id"],
            "raw_file": r["raw_file"], "raw_line": r["raw_line"], "cwd": r.get("cwd"), "title": r.get("title"),
            "text": dispatch_words(r["text"]), "envelope": r["text"], "dispatch_id": did,
            "issue": envelope_field(r["text"], "owner"), "event_type": envelope_field(r["text"], "type"),
            "excluded": None, "also_in": [],
        }
        seen[did] = row
        out.append(row)
    return out, seen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", required=True, help="UTC start, e.g. '2026-10-12 03:21'")
    ap.add_argument("--to", dest="end", required=True, help="UTC end (exclusive)")
    ap.add_argument("--actor", default="^sami@", help="regex on the Dispatch actor id (default ^sami@)")
    ap.add_argument("--legion", action="append", default=[], help="JSONL of Legion rows in this script's shape")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows, by_event = build(a.start, a.end, a.actor)
    merged = 0
    for path in a.legion:
        for line in open(path):
            if not line.strip():
                continue
            row = json.loads(line)
            twin = by_event.get(row.get("dispatch_id") or "")
            if twin is not None:
                twin["also_in"].append(row["session_id"])
                merged += 1
                continue
            rows.append(row)
    rows.sort(key=lambda r: (r["ts"], r["session_id"]))
    with open(a.out, "w") as f:
        for n, r in enumerate(rows, 1):
            r["id"] = f"M{n:04d}"
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    kept = [r for r in rows if not r["excluded"]]
    by = defaultdict(int)
    for r in rows:
        by[(r["source"], r["kind"], r["excluded"] or "kept")] += 1
    for k, v in sorted(by.items(), key=str):
        print(f"{v:6d}  {' / '.join(k)}", file=sys.stderr)
    print(f"{len(kept)} messages of Sami's to read; {len(rows) - len(kept)} rows excluded with a reason", file=sys.stderr)
    if a.legion:
        print(f"Legion: {merged} of its Dispatch rows also reached a tq session and merged into it", file=sys.stderr)
    else:
        print("Legion: not read (no --legion file). Say so in the report.", file=sys.stderr)


if __name__ == "__main__":
    main()
