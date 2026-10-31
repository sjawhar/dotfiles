"""chief-dashboard sami-messages [--session ID] [--since ISO8601] > sami-requests-verbatim.md

Sami's messages to one session, verbatim, with their timestamps, from the transcript store.

`tq search --human --session ID` lists them (one query, JSONEachRow); each hit's `raw` field is FILE:LINE of the
session transcript, and the text is read from that line. A typed message is a `type: message` record (timestamp
from the record). A Dispatch event reaches the session as an Envoy delivery whose `content` is a YAML envelope
(`envoy.at`, `envoy.dispatch.type`, then his words in `envoy.dispatch.payload` or on `envoy.dispatch` itself: an
ask answer is `dispatch.answer`, a reply to an ask `dispatch.reply`); its text is the first of TEXT_FIELDS found,
payload first, and an event with none of them is shown whole and marked UNCERTAIN. The command prints the messages as
markdown, oldest first: the file /mnt/nvme/sami/chief/sami-requests-verbatim.md is made this way.

PyYAML is needed here only; the page itself is standard library."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

import yaml

from .model import parse_time

SESSION = "01a13dca-e979-7264-af32-f654ef57a6f5"
TEXT_FIELDS = ("body", "text", "answer", "comment", "reply")


@dataclass
class Message:
    at: datetime
    text: str
    note: str = ""  # where it came from when not typed, e.g. "Via Dispatch (comment.created)"
    source: str = ""  # FILE:LINE of the transcript


def query(session: str, since: datetime | None) -> str:
    """The tq JSONEachRow dump of every message a person sent the session (since `since`)."""
    cmd = ["tq", "search", "--human", "--session", session, "--limit", "100000", "--format", "JSONEachRow"]
    if since is not None:
        cmd += ["--from", since.strftime("%Y-%m-%d %H:%M:%S")]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def _utc(t) -> datetime | None:
    if isinstance(t, datetime):  # YAML reads an unquoted ISO time as a datetime
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    return parse_time(str(t)) if t else None


def _from_envoy(raw: str, fallback: datetime | None) -> tuple[datetime | None, str, str]:
    try:
        env = yaml.safe_load(raw)["envoy"]
        d = env.get("dispatch") or {}
        p = d.get("payload") or {}
        kind = d.get("type", "?")
        text = next((src[k] for src in (p, d) for k in TEXT_FIELDS if src.get(k)), None)
        at = _utc(env.get("at")) or fallback
        if text:
            return at, str(text), f"Via Dispatch ({kind})"
        dump = yaml.safe_dump(p or d, allow_unicode=True, sort_keys=False)
        return at, dump, f"UNCERTAIN: Dispatch event {kind} with no text field; the event shown whole"
    except Exception as e:  # a malformed envelope is shown raw, never dropped
        return fallback, raw, f"UNCERTAIN: could not parse envoy envelope ({e}); raw delivery shown"


def messages(dump: str) -> list[Message]:
    """The messages a tq JSONEachRow dump points at, oldest first; each transcript file is read once."""
    hits = [json.loads(ln) for ln in dump.splitlines() if ln.strip()]
    want: dict[str, set[int]] = {}
    for h in hits:
        path, line = h["raw"].rsplit(":", 1)
        want.setdefault(path, set()).add(int(line))
    lines: dict[tuple[str, int], str] = {}
    for path, nums in want.items():
        with open(path) as f:
            for i, ln in enumerate(f, 1):
                if i in nums:
                    lines[(path, i)] = ln
    out: list[Message] = []
    for h in hits:
        path, line = h["raw"].rsplit(":", 1)
        rec = json.loads(lines[(path, int(line))])
        if rec.get("type") == "message":
            c = rec["message"]["content"]
            text = c if isinstance(c, str) else "\n".join(p.get("text", "") for p in c if p.get("type") == "text")
            at, note = _utc(rec.get("timestamp")), ""
        else:
            at, text, note = _from_envoy(rec.get("content", ""), _utc(h.get("ts")))
        if at is None:
            at, note = datetime.min.replace(tzinfo=UTC), f"UNCERTAIN: no timestamp. {note}".strip()
        out.append(Message(at, text, note, h["raw"]))
    out.sort(key=lambda m: m.at)
    return out


def stamp(t: datetime) -> str:
    """2026-10-25T06:05:24.512Z, or without the fraction when the time has none."""
    frac = f".{t.microsecond // 1000:03d}" if t.microsecond else ""
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") + frac + "Z"


def markdown(msgs: list[Message]) -> str:
    span = f"; range {stamp(msgs[0].at)} to {stamp(msgs[-1].at)} (UTC)" if msgs else ""
    out = [f"Total Sami messages: {len(msgs)}{span}\n"]
    for m in msgs:
        out += [f"## {stamp(m.at)}\n", f"<!-- source: {m.source} -->"]
        if m.note:
            out.append(m.note)
        out.append("")
        out.append("\n".join("> " + ln if ln else ">" for ln in m.text.rstrip("\n").split("\n")))
        out.append("")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="chief-dashboard sami-messages", description=__doc__.split("\n\n")[1])
    p.add_argument("--session", default=SESSION)
    p.add_argument("--since", help="ISO 8601 UTC start (default: the whole session)")
    args = p.parse_args(argv)
    since = parse_time(args.since) if args.since else None
    if args.since and since is None:
        p.error(f"--since is not an ISO 8601 time: {args.since}")
    sys.stdout.write(markdown(messages(query(args.session, since))))
    return 0
