#!/usr/bin/env python3
"""List every message Sami sent agent sessions in a window: what he typed, and what he wrote in
Dispatch that reached a session. One JSON line per message, full text, nothing cut.

    enumerate-messages.py --from '2026-10-12T03:21:00Z' --to '2026-10-19T03:21:00Z' --out messages.jsonl
    enumerate-messages.py ... --legion legion-messages.jsonl   # add Legion's sessions (see below)

A window may be written with a `Z` suffix, with an offset, or as plain naive UTC.

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
- `legion-daemon-prompt`: a typed row on a `legion-*` host, which is the Legion daemon's own
  phase prompt; his words reach a Legion pod as a Dispatch row, counted above.
- `not-his-machine`: a typed row on a `machcsl-gcp-*` host. No typed row of Sami's is on these
  hosts in the 14 days to 2026-11-07; the human ones there are Nickolai's.
- `scheduler-item`: a typed row a proof scheduler sent the session it keeps per owner — an item
  file, the owner's standing brief, or the notice that it resumed a paused step. 233 rows of the
  11-02..11-08 window, each read as a message of his and holding no finding.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

TQ = "tq"
DISPATCH_USER = "\n  dispatch:\n(?:    [^\n]*\n)*?    actor:\n(?:      [^\n]*\n)*?      kind: user(?:\n|$)"
WORD_FIELDS = re.compile(r"\n(?:    (?:answer|reply)|      (?:body|reason)): ([^\n]*)")
NEEDLE_LEN = 48
BATCH = 200          # needles per search; the whole query is one argv word, which has a size limit
WORKERS = 4          # searches in flight; the store is shared, so keep it small
# Legion runs its sessions on hosts named legion-*, where every typed row is the daemon's own
# phase prompt ("Continue <issue> … Phase: …", a production check, a round-cap notice): Sami's
# words reach a Legion pod as Dispatch rows, never as typed ones.
LEGION_HOST = re.compile(r"^legion-")
# machcsl-gcp-*: no typed row of Sami's in the 14 days to 2026-11-07 (every transcript is under
# /home/nickolai, and the one human voice there names Sami in the third person); recheck if he
# starts working there.
OTHER_PEOPLE_HOST = re.compile(r"^machcsl-gcp-")
# A proof scheduler keeps one session per owner and resumes it for each item, sending the item
# file, the owner's standing brief, or a notice naming the item it resumed. Matched on that text,
# not on a host or a path: the scheduler runs on the same hosts Sami types on.
SCHEDULER_ITEM = re.compile(
    r"\A\s*(?:<file name=\"[^\"]*\">\s*\n)?#+ (?:Item \d+ for owner |Standing brief of owner )"
    r"|\A\s*Your item is \S+: read it again if your context no longer holds its header\."
)


def sql_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def window(value: str) -> str:
    """A window end as the store wants it: naive UTC, whatever form the caller wrote.

    `2026-11-06T15:00:00Z`, `2026-11-06T15:00:00+00:00` and `2026-11-06 15:00` all mean the same
    instant; ClickHouse and this script's own date arithmetic take only the naive form, so the
    `Z` the skill's own example uses used to end the run in a tq error.
    """
    t = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t.isoformat(sep=" ")


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
        "ORDER BY m.ts, m.session_id, m.raw_line"
    )


def dispatch_rows(start: str, end: str, actor: str) -> list[dict]:
    return tq(
        "SELECT m.session_id AS session_id, m.host AS host, toString(m.ts) AS ts, m.raw_file AS raw_file, "
        "m.raw_line AS raw_line, m.text AS text, s.cwd AS cwd, s.title AS title "
        "FROM messages m LEFT JOIN sessions_latest s ON s.session_id = m.session_id "
        "WHERE m.kind = 'system' AND m.role = 'custom:envoy-message' "
        f"AND m.ts >= {sql_str(start)} AND m.ts < {sql_str(end)} AND match(m.text, {sql_str(DISPATCH_USER)}) "
        f"AND match(extract(m.text, '\\n      id: ([^\\n]*)'), {sql_str(actor)}) "
        "ORDER BY m.ts, m.session_id, m.raw_line"
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


def index_tokens(needle: str) -> list[str]:
    """Whole words of the needle that any text containing it also holds as whole words.

    `tool_calls` carries a text index on `lower(command)` (tokens split on non-alphabetic
    characters), and `hasAllTokens` over it skips the granules that cannot match, which is what
    makes the search cheap. A word counts only when it is purely alphabetic and has a
    non-alphanumeric character on each side INSIDE the needle, so it is one whole token under
    either split rule, and the needle's cut-off last word is never used.
    """
    out = []
    for m in re.finditer(r"[A-Za-z]+", needle):
        a, b = m.span()
        if a > 0 and b < len(needle) and not needle[a - 1].isalnum() and not needle[b].isalnum():
            out.append(m.group(0).lower())
    return sorted(set(out))


def search_calls(needles: list[str], lo: str, end: str, prefilter: bool) -> dict[int, list[str]]:
    """Per needle index: the start times of the tool calls whose command holds it."""
    arr = "[" + ",".join(sql_str(n) for n in needles) + "]"
    where = ""
    if prefilter:
        groups = " OR ".join(
            f"hasAllTokens(lower(ifNull(command, '')), [{','.join(sql_str(t) for t in index_tokens(n))}])" for n in needles
        )
        where = f"({groups}) AND "
    res = tq(
        f"SELECT arrayJoin(arrayFilter(x -> x > 0, arrayMap((p, k) -> if(p > 0, k, 0), "
        f"multiSearchAllPositions(command, {arr}), arrayEnumerate({arr})))) AS k, "
        "groupUniqArray(toString(started_at)) AS at FROM tool_calls "
        f"WHERE started_at >= {sql_str(lo)} AND started_at < {sql_str(end)} AND command IS NOT NULL "
        f"AND {where}multiSearchAny(command, {arr}) GROUP BY k"
    )
    return {int(r["k"]) - 1: r["at"] for r in res}


def agent_sent(rows: list[dict], start: str, end: str) -> set[int]:
    """Indexes of rows whose opening text appears in an agent tool call in the day before them.

    The search runs once per distinct needle (scripted launches repeat one first line thousands
    of times) over `tool_calls` rather than the `calls` view, whose FINAL merges every revision of
    every call to answer a question one revision already answers, and the text index on
    `lower(command)` skips the granules that cannot hold the batch's needles.
    """
    lo = (datetime.fromisoformat(start) - timedelta(days=1)).isoformat(sep=" ")
    hits: set[int] = set()
    same: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        n = needle(r["text"])
        if len(n) >= 16:
            same[n].append(i)
    indexed = sorted(n for n in same if len(index_tokens(n)) >= 2)
    plain = sorted(n for n in same if len(index_tokens(n)) < 2)
    jobs = [(indexed[b : b + BATCH], True) for b in range(0, len(indexed), BATCH)]
    jobs += [(plain[b : b + BATCH], False) for b in range(0, len(plain), BATCH)]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for batch, found in zip([j[0] for j in jobs], pool.map(lambda j: search_calls(j[0], lo, end, j[1]), jobs)):
            for k, at in found.items():
                for i in same[batch[k]]:
                    ts = rows[i]["ts"]
                    day = (datetime.fromisoformat(ts) - timedelta(days=1)).isoformat(sep=" ")
                    if any(day <= a <= ts for a in at):
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
        elif LEGION_HOST.match(r["host"] or ""):
            excluded = "legion-daemon-prompt"
        elif OTHER_PEOPLE_HOST.match(r["host"] or ""):
            excluded = "not-his-machine"
        elif SCHEDULER_ITEM.match(r["text"] or ""):
            excluded = "scheduler-item"
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
    ap.add_argument("--from", dest="start", required=True,
                    help="UTC start, e.g. '2026-10-12 03:21' or '2026-10-12T03:21:00Z'")
    ap.add_argument("--to", dest="end", required=True, help="UTC end (exclusive), same forms")
    ap.add_argument("--actor", default="^sami@", help="regex on the Dispatch actor id (default ^sami@)")
    ap.add_argument("--legion", action="append", default=[], help="JSONL of Legion rows in this script's shape")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows, by_event = build(window(a.start), window(a.end), a.actor)
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
    rows.sort(key=lambda r: (r["ts"], r["session_id"], r.get("raw_line") or 0))
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
