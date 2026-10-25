#!/usr/bin/env python3
"""Write one reading record per message of Sami's: his words in full, with the agent's work
around them, plus session, issue and repository.

    build-records.py messages.jsonl OUTDIR [--before 40] [--after 80]

Input: `enumerate-messages.py --out` (rows with `excluded` set are skipped). Output:
OUTDIR/<id>.md, one per message, and OUTDIR/records.jsonl, the index the run assigns readers from.

A record holds, in raw-line order and never cut:

- His previous message in the session, whole (his typed rows and his Dispatch deliveries both
  count as messages).
- Before: the agent's turns since that message: its text (its thinking too with --thinking), every
  tool call with its full command, the full output of every call that failed, and every Envoy or
  IRC message it got. tq rows read from tq; Legion rows (fetch-legion.py) from their session file.
- The message: his words verbatim; for a Dispatch event, the whole envelope too (it holds the ask
  or comment he answered).
- After: the same, up to the agent's next yield (its first message with no tool call), or his
  next message, whichever comes first. His next message is printed whole.

--before and --after bound the number of events, not their text: when a span holds more, the
record keeps the events nearest the message and names the raw lines it left out, so a reader
can open them (`read <raw_file>:<a>-<b>`).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TQ = "tq"
DISPATCH_USER = re.compile(r"\n  dispatch:\n(?:    [^\n]*\n)*?    actor:\n(?:      [^\n]*\n)*?      kind: user(?:\n|$)")
OWNER = re.compile(r"\n    owner: ([A-Z][A-Z0-9]*-\d+)")
SECRET = re.compile(r"(code=[A-Za-z0-9/_\-.%]{10,}|sk-[A-Za-z0-9_-]{8,}|gh[pso]_[A-Za-z0-9]{8,}|Bearer [A-Za-z0-9._-]{12,}|ya29\.[A-Za-z0-9._-]{12,})")


def tq(query: str) -> list[dict]:
    out = subprocess.run([TQ, "sql", query, "--format", "JSONEachRow"], capture_output=True, check=True).stdout
    return [json.loads(line) for line in out.decode("utf-8", "replace").splitlines() if line.strip()]


def q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def red(text: str) -> str:
    return SECRET.sub("<redacted>", text or "")


def is_human(m: dict) -> bool:
    if m["kind"] == "user" and m["role"] == "user":
        return True
    return m["role"] == "custom:envoy-message" and bool(DISPATCH_USER.search(m["text"]))


def session_events(sid: str, lo: int, hi: int) -> tuple[list[dict], dict[int, list[dict]], dict[int, str]]:
    msgs = tq(
        "SELECT raw_line, toString(ts) AS ts, role, kind, text FROM messages "
        f"WHERE session_id = {q(sid)} AND raw_line >= {lo} AND raw_line <= {hi} "
        "AND kind IN ('assistant', 'thinking', 'user', 'system') ORDER BY raw_line"
    )
    calls = tq(
        "SELECT raw_line, result_line, tool, intent, command, is_error, exit_code FROM calls "
        f"WHERE session_id = {q(sid)} AND raw_line >= {lo} AND raw_line <= {hi} ORDER BY raw_line"
    )
    by_line: dict[int, list[dict]] = defaultdict(list)
    for c in calls:
        by_line[int(c["raw_line"])].append(c)
    err_lines = [int(c["result_line"]) for c in calls if c["is_error"] and c["result_line"] is not None]
    errors: dict[int, str] = {}
    for b in range(0, len(err_lines), 300):
        chunk = ",".join(str(x) for x in err_lines[b : b + 300])
        for r in tq(
            f"SELECT raw_line, text FROM messages WHERE session_id = {q(sid)} AND kind = 'tool_result' "
            f"AND raw_line IN ({chunk})"
        ):
            errors[int(r["raw_line"])] = r["text"]
    return msgs, by_line, errors


def file_events(path: str) -> tuple[list[dict], dict[int, list[dict]], dict[int, str]]:
    """The same rows session_events returns, read from an omp session file (Legion's sessions are
    not in tq). Legion pods have no terminal, so their `user` rows are the daemon's prompts:
    they get role `user:agent`, never `user`."""
    msgs: list[dict] = []
    by_line: dict[int, list[dict]] = defaultdict(list)
    errors: dict[int, str] = {}
    open_calls: dict[str, dict] = {}
    for n, line in enumerate(open(path), 1):
        if not line.strip():
            continue
        r = json.loads(line)
        ts = (r.get("timestamp") or "").replace("T", " ").rstrip("Z")
        if r.get("type") == "custom_message" and r.get("customType") == "envoy-message":
            c = r.get("content")
            text = c if isinstance(c, str) else "".join(p.get("text", "") for p in c or [] if isinstance(p, dict))
            msgs.append({"raw_line": n, "ts": ts, "role": "custom:envoy-message", "kind": "system", "text": text})
            continue
        if r.get("type") != "message":
            continue
        m = r["message"]
        content = m.get("content")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        if m.get("role") == "user":
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            msgs.append({"raw_line": n, "ts": ts, "role": "user:agent", "kind": "user", "text": text})
        elif m.get("role") == "assistant":
            for p in parts:
                if p.get("type") == "text" and p.get("text"):
                    msgs.append({"raw_line": n, "ts": ts, "role": "assistant", "kind": "assistant", "text": p["text"]})
                elif p.get("type") == "thinking" and p.get("thinking"):
                    msgs.append({"raw_line": n, "ts": ts, "role": "assistant", "kind": "thinking", "text": p["thinking"]})
                elif p.get("type") == "toolCall":
                    args = p.get("arguments") or {}
                    cmd = args.get("command") if isinstance(args.get("command"), str) else json.dumps(args, ensure_ascii=False)
                    call = {"raw_line": n, "result_line": None, "tool": p.get("name"), "intent": args.get("i"),
                            "command": cmd, "is_error": False, "exit_code": None}
                    by_line[n].append(call)
                    open_calls[p.get("id")] = call
        elif m.get("role") == "toolResult":
            call = open_calls.pop(m.get("toolCallId"), None)
            if call:
                call["result_line"] = n
                call["is_error"] = bool(m.get("isError"))
                if call["is_error"]:
                    errors[n] = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return msgs, by_line, errors


def event_lines(msgs: list[dict], by_line: dict[int, list[dict]], thinking: bool) -> list[tuple[int, str, object]]:
    """(raw_line, kind, payload) in order; one entry per message row and per tool call."""
    ev: list[tuple[int, str, object]] = []
    for m in msgs:
        if m["kind"] == "system" and m["role"] not in ("custom:envoy-message",):
            continue
        if m["kind"] == "thinking" and not thinking:
            continue
        ev.append((int(m["raw_line"]), "M", m))
    for line, cs in by_line.items():
        for c in cs:
            ev.append((line, "C", c))
    ev.sort(key=lambda e: (e[0], 0 if e[1] == "M" else 1))
    return ev


def fmt(e: tuple[int, str, object], errors: dict[int, str]) -> str:
    line, kind, x = e
    if kind == "C":
        c = x
        head = f"[L{line}] TOOL {c['tool']}"
        if c.get("intent"):
            head += f" ({c['intent']})"
        body = red(c.get("command") or "")
        out = f"{head}:\n{body}"
        if c["is_error"]:
            res = errors.get(int(c["result_line"])) if c["result_line"] is not None else None
            out += f"\n[L{c['result_line']}] TOOL FAILED (exit {c['exit_code']}):\n{red(res or '(output not in store)')}"
        return out
    m = x
    ts = m["ts"][5:19]
    if m["kind"] == "assistant":
        return f"[L{line} {ts}] AGENT SAID:\n{red(m['text'])}"
    if m["kind"] == "thinking":
        return f"[L{line} {ts}] AGENT THOUGHT:\n{red(m['text'])}"
    if is_human(m):
        return f"[L{line} {ts}] SAMI:\n{red(m['text'])}"
    if m["role"] == "custom:envoy-message":
        return f"[L{line} {ts}] ENVOY DELIVERY (an agent or a Dispatch notice, not Sami):\n{red(m['text'])}"
    if m["role"] == "user:agent":
        return f"[L{line} {ts}] MESSAGE FROM AN AGENT (brief or relay, not Sami):\n{red(m['text'])}"
    return f"[L{line} {ts}] {m['role']}:\n{red(m['text'])}"


def yield_line(ev: list[tuple[int, str, object]], after: int) -> int | None:
    """Raw line of the agent's first message after `after` that calls no tool (its turn ends)."""
    call_lines = {e[0] for e in ev if e[1] == "C"}
    for line, kind, x in ev:
        if line <= after or kind != "M":
            continue
        if x["kind"] == "assistant" and line not in call_lines:
            return line
        if kind == "M" and is_human(x):
            return line
    return None

def len_cut(ev: list, span: tuple[int, int]) -> int:
    return sum(1 for e in ev if span[0] <= e[0] <= span[1])


def window(events: list, keep: int, nearest_end: bool) -> tuple[list, tuple[int, int] | None]:
    if len(events) <= keep:
        return events, None
    if nearest_end:
        cut = events[:-keep]
        return events[-keep:], (cut[0][0], cut[-1][0])
    cut = events[keep:]
    return events[:keep], (cut[0][0], cut[-1][0])


REPO_CACHE: dict[str, str | None] = {}


def repo_of(cwd: str | None) -> str | None:
    if not cwd:
        return None
    if cwd not in REPO_CACHE:
        url = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"], capture_output=True, text=True)
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url.stdout.strip()) if url.returncode == 0 else None
        REPO_CACHE[cwd] = m.group(1) if m else None
    return REPO_CACHE[cwd]


def session_issue(sid: str) -> str | None:
    """The Dispatch issue a session works on: the owner most of its Dispatch deliveries name."""
    rows = tq(
        "SELECT extract(text, '\\n    owner: ([A-Z][A-Z0-9]*-[0-9]+)') AS o, count() AS n FROM messages "
        f"WHERE session_id = {q(sid)} AND role = 'custom:envoy-message' AND o != '' GROUP BY o ORDER BY n DESC LIMIT 1"
    )
    return rows[0]["o"] if rows else None


def build_session(sid: str, rows: list[dict], out: Path, a: argparse.Namespace) -> list[dict]:
    lines = [int(r["raw_line"]) for r in rows]
    if rows[0]["source"] == "legion":
        msgs, by_line, errors = file_events(rows[0]["raw_file"])
        ev = event_lines(msgs, by_line, a.thinking)
        prev = max((e[0] for e in ev if e[0] < min(lines) and e[1] == "M" and is_human(e[2])), default=0)
        issue = next((r.get("issue") for r in rows if r.get("issue")), None)
    else:
        # Prior human row before the first message bounds the fetch.
        prev = tq(
            "SELECT max(raw_line) AS m FROM messages WHERE session_id = " + q(sid) + f" AND raw_line < {min(lines)} AND "
            "((kind = 'user' AND role = 'user') OR (role = 'custom:envoy-message' AND match(text, '\\n      kind: user')))"
        )[0]["m"] or 0
        msgs, by_line, errors = session_events(sid, int(prev), max(lines) + 20000)
        ev = event_lines(msgs, by_line, a.thinking)
        issue = session_issue(sid)
    human_lines = sorted({e[0] for e in ev if e[1] == "M" and is_human(e[2])} | set(lines))
    n_before, n_after = a.before, a.after
    recs = []
    for r in rows:
        line = int(r["raw_line"])
        i = human_lines.index(line)
        lo = human_lines[i - 1] if i > 0 else int(prev)
        y = yield_line(ev, line)
        hi = y if y is not None else (human_lines[i + 1] if i + 1 < len(human_lines) else 10**12)
        before = [e for e in ev if lo < e[0] < line]
        prev_msg = next((e for e in ev if e[0] == lo and e[1] == "M" and is_human(e[2])), None)
        after = [e for e in ev if line < e[0] <= hi]
        before, cut_b = window(before, n_before, nearest_end=True)
        after, cut_a = window(after, n_after, nearest_end=False)
        rec_issue = r.get("issue") or issue
        repo = repo_of(r.get("cwd"))
        parts = [
            f"# Record {r['id']}",
            f"- session: {sid} ({r.get('title') or 'untitled'}), host {r['host']}",
            f"- file: {r['raw_file']}:{line}",
            f"- time: {r['ts']} UTC",
            f"- issue: {rec_issue or 'none found'}; repository: {repo or 'none found'} (cwd {r.get('cwd')})",
            f"- channel: {'Dispatch ' + (r.get('event_type') or '') if r['kind'] == 'dispatch' else 'typed in the session'}",
            "",
            f"## Sami's previous message in this session (L{lo})",
            fmt(prev_msg, errors) if prev_msg else "(none in the store before this one)",
            "",
            f"## Before: the agent's work since Sami's previous message (line {lo})",
        ]
        if cut_b:
            parts.append(f"[{len_cut(ev, cut_b)} earlier events left out, L{cut_b[0]}-L{cut_b[1]}: read {r['raw_file']}:{cut_b[0]}-{cut_b[1]}]")
        parts += [fmt(e, errors) for e in before] or ["(no agent activity in between)"]
        parts += ["", f"## SAMI'S MESSAGE (L{line}, verbatim)", red(r["text"])]
        if r["kind"] == "dispatch":
            parts += ["", "### The Dispatch envelope it arrived in", red(r.get("envelope") or "")]
        parts += ["", "## After: the agent's work up to its next yield or Sami's next message"]
        parts += [fmt(e, errors) for e in after] or ["(nothing after it in the store)"]
        if cut_a:
            parts.append(f"[{len_cut(ev, cut_a)} later events left out, L{cut_a[0]}-L{cut_a[1]}: read {r['raw_file']}:{cut_a[0]}-{cut_a[1]}]")
        text = "\n".join(parts) + "\n"
        path = out / f"{r['id']}.md"
        path.write_text(text)
        recs.append({
            "id": r["id"], "path": str(path), "session_id": sid, "title": r.get("title"), "ts": r["ts"],
            "kind": r["kind"], "issue": rec_issue, "repo": repo, "bytes": len(text.encode()),
            "before_events": len(before), "after_events": len(after), "cut_before": cut_b, "cut_after": cut_a,
        })
    return recs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("messages")
    ap.add_argument("out")
    ap.add_argument("--before", type=int, default=40, help="most events kept before a message (default 40)")
    ap.add_argument("--after", type=int, default=80, help="most events kept after a message (default 80)")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--thinking", action="store_true",
                    help="also print the agent's thinking (about 60%% of a record's bytes; off by default)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in open(a.messages) if line.strip()]
    keep = [r for r in rows if not r.get("excluded")]
    by_sid: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in keep:
        r.setdefault("source", "tq")
        by_sid[(r["source"], r["session_id"])].append(r)
    with ThreadPoolExecutor(a.jobs) as pool:
        results = list(pool.map(lambda kv: build_session(kv[0][1], kv[1], out, a), by_sid.items()))
    recs = sorted((x for rs in results for x in rs), key=lambda x: x["id"])
    with open(out / "records.jsonl", "w") as f:
        for x in recs:
            f.write(json.dumps(x) + "\n")
    cut = Counter(bool(x["cut_before"]) or bool(x["cut_after"]) for x in recs)
    print(f"{len(recs)} records in {len(by_sid)} sessions; {cut[True]} name left-out lines; "
          f"{sum(x['bytes'] for x in recs) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
