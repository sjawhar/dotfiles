"""chief-dashboard pilot {facts,summary,schedule,census,measure}: the stream pilot without a standing lead session.

A stream's state lives in its Dispatch document `stream.md`; `facts` turns it and the live state around it into one
JSON snapshot, `summary` hands that snapshot to one bounded headless omp session, and `schedule` runs the summaries at
fixed Pacific times and at each commitment's due time. `census` and `measure` expose the failure the pilot guards
against: sessions that spread their work over many issues, or move parts elsewhere, so they can call it done.
`measure` reads Dispatch events only, never transcripts, so every count can be checked against Dispatch.

Nothing here writes to Dispatch or sends an Envoy message. The state directory holds the record copy, the snapshots,
`started.tsv`, `pilot-sessions.txt`, the offline files, `fired-dues.json` and the scheduler's `heartbeat`."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from . import sessions
from .collect import SCOREBOARD_SLUG, VersionCache
from .lead_times import events
from .sources import Dispatch, SourceError

ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")
SESSION_ID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
LEDGER = re.compile(
    r"^LEDGER\s+(start|merged|measured|blocked|unblocked|handoff|done)\s+([A-Z][A-Z0-9]*-\d+)\s+(.+?)\s+(https?://\S+)$"
)
DUE = re.compile(r"\bdue\s+(\d{4}-\d\d-\d\dT\d\d:\d\dZ)\b")
EXTERNAL_URL = re.compile(r"https://[^\s)>]+")

# The pilot measurement begins then unless `pilot facts --pilot-start` explicitly sets a different boundary.
PILOT_START = dt.datetime(2026, 11, 3, 14, 30, tzinfo=dt.UTC)


def parse_time(value: str) -> dt.datetime:
    """An ISO 8601 time as aware UTC. Naive input is UTC; an explicit offset is converted, never discarded."""
    text = value.strip()
    parsed = dt.datetime.fromisoformat(f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text)
    return parsed.replace(tzinfo=dt.UTC) if parsed.tzinfo is None else parsed.astimezone(dt.UTC)


def utc_stamp(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _section(markdown: str, heading: str) -> list[str]:
    """The lines belonging to one Markdown heading, stopping at its next peer or parent."""
    lines = markdown.splitlines()
    start = None
    level = 0
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match and match.group(2).strip().lower() == heading.lower():
            start, level = index + 1, len(match.group(1))
            break
    if start is None:
        return []
    out: list[str] = []
    for line in lines[start:]:
        match = re.match(r"^(#{1,6})\s+", line)
        if match and len(match.group(1)) <= level:
            break
        out.append(line)
    return out


def _cells(line: str) -> list[str]:
    """A Markdown table row's cells, splitting only separators not escaped with a backslash."""
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in inner:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def _first_key(text: str) -> str | None:
    match = ISSUE_KEY.search(text)
    return match.group(0) if match else None


def _first_session(text: str) -> str | None:
    match = SESSION_ID.search(text)
    return match.group(0) if match else None


def parse_order_of_work(markdown: str) -> list[dict]:
    """Rows from the record's fixed `Order of work` table, retaining only fields the pilot interprets."""
    rows = _section(markdown, "Order of work")
    header_at = next((index for index, line in enumerate(rows) if "|" in line and "Issue" in line), None)
    if header_at is None or header_at + 1 >= len(rows):
        return []
    header = _cells(rows[header_at])
    try:
        issue_at, owner_at, state_at = header.index("Issue"), header.index("Owner session"), header.index("State")
    except ValueError:
        return []
    out: list[dict] = []
    for line in rows[header_at + 2:]:
        if "|" not in line or re.match(r"^\s*\|?\s*-", line):
            continue
        cells = _cells(line)
        if len(cells) <= max(issue_at, owner_at, state_at):
            continue
        key = _first_key(cells[issue_at])
        if key is None:
            continue
        state = cells[state_at]
        waits = ISSUE_KEY.findall(state) if re.search(r"\bwaits?\b", state, re.I) else []
        # `owner` is the column's text: the summary skips a row that names a person, which has no session id.
        out.append({"issue": key, "owner": cells[owner_at], "owner_session": _first_session(cells[owner_at]),
                    "waits_on": waits, "state": state})
    return out


def parse_commitments(markdown: str, now: dt.datetime) -> list[dict]:
    """Unchecked commitments from the stream record, including their due state at NOW."""
    out: list[dict] = []
    for line in _section(markdown, "Open commitments"):
        match = re.match(r"^\s*-\s+\[\s\]\s+(.+?)\s*$", line)
        if match is None:
            continue
        item = match.group(1)
        due_match = DUE.search(item)
        due = due_match.group(1) if due_match else None
        owner_match = re.search(r"\bowner\s+([0-9a-f-]{36})\b", item, re.I)
        text = item.split(" · owner ", 1)[0].strip()
        link = EXTERNAL_URL.search(item)
        out.append({
            "text": text,
            "line": item,
            "link": link.group(0) if link else None,
            "owner_session": owner_match.group(1) if owner_match else None,
            "issue": _first_key(item),
            "due": due,
            "overdue": bool(due and parse_time(due) < now),
        })
    return out


def parse_ledger_line(line: str) -> dict:
    """One machine-readable LEDGER line, or its original text marked malformed for a person to repair."""
    match = LEDGER.fullmatch(line) if "\n" not in line else None
    if match is None:
        return {"raw": line, "malformed": True}
    return {"kind": match.group(1), "issue": match.group(2), "text": match.group(3), "link": match.group(4)}


def parse_roles(markdown: str, live_sessions: list[dict] | None) -> list[dict]:
    """Rows from `Envoy roles and claims`, joined to Envoy's live `roles` arrays rather than a declared holder.
    LIVE_SESSIONS None means Envoy could not be read: each holder's liveness is then null (unknown)."""
    rows = _section(markdown, "Envoy roles and claims")
    header_at = next((index for index, line in enumerate(rows) if "|" in line and "Envoy role" in line), None)
    if header_at is None or header_at + 1 >= len(rows):
        return []
    header = _cells(rows[header_at])
    try:
        role_at, holder_at = header.index("Envoy role"), header.index("Holder now")
    except ValueError:
        return []
    next_at = next((index for index, name in enumerate(header) if name.startswith("Goes to")), None)
    holders: dict[str, list[str]] = {}
    for session in live_sessions or []:
        for role in session.get("roles") or []:
            if session.get("session_id"):
                holders.setdefault(role, []).append(session["session_id"])
    out: list[dict] = []
    for line in rows[header_at + 2:]:
        if "|" not in line or re.match(r"^\s*\|?\s*-", line):
            continue
        cells = _cells(line)
        if len(cells) <= max(role_at, holder_at) or not cells[role_at]:
            continue
        live = sorted(holders.get(cells[role_at], []))
        out.append({
            "role": cells[role_at],
            "holder": cells[holder_at],
            "next_holder": cells[next_at] if next_at is not None and next_at < len(cells) else None,
            "holder_live": bool(live) if live_sessions is not None else None,
            "live_holder": live[0] if live else None,
        })
    return out


def _recent_starts(path: Path, now: dt.datetime, live_panes: set[str]) -> dict[str, dict]:
    """Each issue's latest `started.tsv` line (the file's append order) while it is under two hours old or omp still
    runs in its pane (LIVE_PANES), so agent-new's trailing interactive bash cannot strand a ready issue."""
    if not path.exists():
        return {}
    latest: dict[str, tuple[str, dt.datetime]] = {}
    for line in path.read_text().splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        try:
            latest[fields[0]] = (fields[1], parse_time(fields[2]))
        except ValueError:
            continue
    out: dict[str, dict] = {}
    for issue, (pane, at) in latest.items():
        age = now - at
        # `agent-new` ends with `exec bash`; only a pane with omp running in it is a still-running started session.
        if dt.timedelta() <= age < dt.timedelta(hours=2) or pane in live_panes:
            out[issue] = {"pane": pane, "at": utc_text(at)}
    return out


def _envoy_sessions(findings: list[str]) -> list[dict] | None:
    """Live Envoy sessions and their held roles, read once per facts snapshot; None, with a finding, when the hub
    cannot be read, so a role check never stops the stream's summary."""
    try:
        completed = subprocess.run(["envoy", "ps", "--json"], text=True, capture_output=True, check=True)
        value = json.loads(completed.stdout)
        if not isinstance(value, list):
            raise ValueError("envoy ps --json did not return a session list")
        return value
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None) or ""
        findings.append(f"Envoy unavailable, role holders not read: {error} {detail}".rstrip())
        return None


def omp_panes(processes: list[dict], panes: list[dict]) -> set[str]:
    """The ids of the tmux panes an omp process runs under (`sessions.is_omp`, its ancestors up to the pane's
    process). `#{pane_current_command}` cannot tell: `omp` here is the bash shim ~/.dotfiles/shims/omp, so a live
    session's pane and agent-new's trailing bash both report `bash`. An omp inside an agent box is not a descendant of
    its pane and is not found; agent-new, which starts every pane in `started.tsv`, never uses a box."""
    by_pid = {p["pid"]: p for p in processes}
    pane_of = {pane["pid"]: pane["pane"] for pane in panes}
    live: set[str] = set()
    for process in processes:
        if not sessions.is_omp(process):
            continue
        seen: set[int] = set()
        current: dict | None = process
        while current is not None and current["pid"] not in seen:
            seen.add(current["pid"])
            if current["pid"] in pane_of:
                live.add(pane_of[current["pid"]])
                break
            current = by_pid.get(current["ppid"])
    return live


def _live_omp_panes() -> set[str]:
    """The panes omp runs in now, read from /proc and `tmux list-panes -a` as `chief-dashboard sessions` reads them."""
    return omp_panes(sessions.read_processes(), sessions.read_panes())


def _state_dir(state: Path, stream: str) -> Path:
    directory = state / stream
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _title(actor: dict | None, live: dict[str, dict]) -> tuple[str | None, str | None, bool]:
    if not actor or actor.get("kind") != "session":
        return None, None, False
    session = actor.get("id")
    agent = live.get(session)
    title = ((agent or {}).get("title") or actor.get("origin", {}).get("session_title"))
    return session, title, agent is not None


def _latest_scoreboard(api, issue: dict) -> dict | None:
    """The newest `scoreboard.json` version, read as the page reads it (`collect.VersionCache`), or None."""
    artifact = next((item for item in issue.get("artifacts", []) if item.get("slug") == SCOREBOARD_SLUG), None)
    if artifact is None or not artifact.get("versions"):
        return None
    version = max(artifact["versions"], key=lambda item: item["number"])
    data = VersionCache().get(api, issue["key"], version["number"])
    return {"number": version["number"], "created_at": version.get("created_at"), "data": data}


def _ledger_entries(comments: list[dict], after: dt.datetime | None) -> list[dict]:
    """The LEDGER comments written after AFTER, while callers that need pilot-wide starts retain the unfiltered list."""
    out: list[dict] = []
    for comment in comments:
        body = comment.get("body") or ""
        when = comment.get("created_at")
        if not body.startswith("LEDGER ") or (after and when and parse_time(when) <= after):
            continue
        item = parse_ledger_line(body)
        item.update({key: comment[key] for key in ("created_at", "author") if key in comment})
        out.append(item)
    return out


def facts(api, stream: str, state: Path, now: dt.datetime, pilot_start: dt.datetime = PILOT_START) -> tuple[dict, Path]:
    """Read one stream's durable facts and write both the current record copy and an immutable facts snapshot.

    While Dispatch is down the snapshot is built from the saved record copy and the offline files alone: every
    live field is null, no row counts as ready (its owner is unknown, not absent), and `last-facts` stays put so
    the next run reads the ledger comments this one could not."""
    now = now.astimezone(dt.UTC)
    pilot_start = pilot_start.astimezone(dt.UTC)
    directory = _state_dir(state, stream)
    copy = directory / "stream.md"
    findings: list[str] = []
    record_source: str | None = None
    down: str | None = None
    try:
        response = api.get(f"/api/v1/issues/{quote(stream)}/artifacts/stream.md/text")
        record = response["markdown"]
        copy.write_text(record)
        record_source = "Dispatch"
    except SourceError as error:
        record = None
        if "HTTP 404" in str(error):
            # The issue has no record: a copy left from an earlier read must not keep feeding the scheduler its dues.
            copy.unlink(missing_ok=True)
            findings.append("no stream record")
        else:
            down = str(error)
            findings.append(f"Dispatch unavailable, live state not read: {down}")
            if copy.exists():
                record = copy.read_text()
                mtime = dt.datetime.fromtimestamp(copy.stat().st_mtime, dt.UTC)
                record_source = f"local copy from {utc_text(mtime)}"

    issue_cache: dict[str, dict] = {}

    def read_issue(key: str) -> dict:
        if key not in issue_cache:
            issue_cache[key] = api.get(f"/api/v1/issues/{quote(key)}")
        return issue_cache[key]

    issue = read_issue(stream) if down is None else None
    agents = ({agent.get("session_id"): agent for agent in api.get("/api/v1/agents") if agent.get("session_id")}
              if down is None else {})
    roles = parse_roles(record, _envoy_sessions(findings)) if record else []
    for role in roles:
        if role["holder_live"] is False:
            findings.append(f"role has no live holder: {role['role']}")
    recent_starts = _recent_starts(directory / "started.tsv", now, _live_omp_panes())
    commitments = parse_commitments(record or "", now)
    for commitment in commitments:
        if commitment["overdue"]:
            findings.append(f"overdue commitment: {commitment['text']}")

    order: list[dict] = []
    for row in parse_order_of_work(record or ""):
        if down is not None:
            order.append({**row, "title": None, "status": None, "claim": None, "ready_no_owner": False})
            continue
        work = read_issue(row["issue"])
        claim_session, claim_title, claim_live = _title((work.get("claim") or {}).get("actor"), agents)
        waits_done = all(read_issue(key).get("status") == "done" for key in row["waits_on"])
        # Only a live session's claim governs: a claim left by an ended session does not hide a fresh start.
        started = recent_starts.get(row["issue"]) if not claim_live else None
        ready = work.get("status") != "done" and not claim_live and waits_done and started is None
        item = {
            **row,
            "title": work.get("title"),
            "status": work.get("status"),
            "claim": {"session": claim_session, "title": claim_title, "live": claim_live} if claim_session else None,
            "ready_no_owner": ready,
        }
        if started:
            item["started_recently"] = started
        order.append(item)
        if claim_session and not claim_live:
            findings.append(f"dead claim holder: {row['issue']} ({claim_session})")
        if ready:
            findings.append(f"ready issue with no owner: {row['issue']}")

    for commitment in commitments:
        if down is not None:
            commitment["title"] = None
            commitment["owner_live"] = None
            continue
        work = read_issue(commitment["issue"]) if commitment["issue"] else None
        commitment["title"] = work.get("title") if work else None
        commitment["owner_live"] = bool(commitment["owner_session"] in agents) if commitment["owner_session"] else None

    previous = directory / "last-facts"
    after = parse_time(previous.read_text().strip()) if previous.exists() and previous.read_text().strip() else None
    comments = api.get(f"/api/v1/issues/{quote(stream)}/comments") if down is None else []
    ledger = _ledger_entries(comments, after)
    pilot_sessions_path = directory / "pilot-sessions.txt"
    pilot_sessions = {line for line in pilot_sessions_path.read_text().splitlines() if line} if pilot_sessions_path.exists() else set()
    for comment in comments:
        body = comment.get("body") or ""
        author = comment.get("author") or {}
        if (body.startswith("LEDGER start ") and comment.get("created_at")
                and parse_time(comment["created_at"]) >= pilot_start and author.get("kind") == "session" and author.get("id")):
            pilot_sessions.add(author["id"])
    if down is None:
        pilot_sessions_path.write_text("".join(f"{session}\n" for session in sorted(pilot_sessions)))
    offline = directory / "offline-ledger.md"
    pending_offline = [line for line in offline.read_text().splitlines() if line.strip()] if offline.exists() else []
    for line in pending_offline:
        entry = parse_ledger_line(line)
        entry["pending_offline"] = True
        ledger.append(entry)
    offline_decisions = directory / "offline-decisions.md"
    pending_offline_decisions = [offline_decisions.read_text().rstrip()] if offline_decisions.exists() else []
    for entry in ledger:
        if entry.get("malformed"):
            findings.append(f"malformed ledger line: {entry['raw']}")

    multi_claim: list[dict] = []
    if down is None:
        project = stream.split("-", 1)[0]
        claims: dict[str, dict] = {}
        for work in api.get(f"/api/v1/issues?project={quote(project)}&open=true"):
            actor = (work.get("claim") or {}).get("actor") or {}
            if work.get("status") == "done" or actor.get("kind") != "session" or not actor.get("id"):
                continue
            claims.setdefault(actor["id"], {"issues": [], "actor": actor})["issues"].append(work["key"])
        for session, claimed in sorted(claims.items()):
            if len(claimed["issues"]) < 2:
                continue
            _, title, _ = _title(claimed["actor"], agents)
            multi_claim.append({
                "session": session, "title": title, "issues": sorted(claimed["issues"]),
                "pilot_session": session in pilot_sessions,
            })
            findings.append(f"multi-claim: {session} ({', '.join(sorted(claimed['issues']))})")

    result = {
        "stream": stream,
        "at": utc_text(now),
        "record": record,
        "record_source": record_source,
        "scoreboard": _latest_scoreboard(api, issue) if issue is not None else None,
        "order_of_work": order,
        "commitments": commitments,
        "ledger": ledger,
        "multi_claim": multi_claim,
        "roles": roles,
        "pending_offline": pending_offline,
        "pending_offline_decisions": pending_offline_decisions,
        "findings": findings,
    }
    path = directory / f"facts-{utc_stamp(now)}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    if down is None:
        previous.write_text(utc_text(now) + "\n")
    return result, path


def summary(api, stream: str, state: Path, window: str, dry_run: bool = False) -> int:
    """Write facts before asking one short-lived omp session to produce the stream's Dispatch summary."""
    now = dt.datetime.now(dt.UTC)
    snapshot, facts_path = facts(api, stream, state, now)
    directory = _state_dir(state, stream)
    log = directory / f"summary-{utc_stamp(now)}.log"
    if snapshot["record"] is None:
        print("no stream record, skipped")
        log.write_text("no stream record, skipped\n")
        return 0
    starts = directory / "started.tsv"
    prompt = (
        f"Read the facts at {facts_path}, then summarize stream issue {stream}. Start any sessions only in tmux window "
        f"{window}. For each session you start for an issue: before you run agent-new, append the line "
        f"`ISSUE<TAB>pending<TAB>UTC_ISO` to {starts}; after it starts, append `ISSUE<TAB>TMUX_PANE_ID<TAB>UTC_ISO`. "
        f"Follow skill://stream-lead/references/summary.md exactly."
    )
    command = ["omp", "-p", "--no-title", "--max-time", "30m", "--max-cost", "15", prompt]
    if dry_run:
        print(shlex.join(command))
        return 0
    completed = subprocess.run(command, cwd=directory, text=True, capture_output=True, check=False)
    log.write_text(f"exit {completed.returncode}\n{completed.stdout}{completed.stderr}")
    return completed.returncode


@dataclass(frozen=True)
class Fire:
    """One eligible scheduler event. A raw due carries one durable key; a coalesced fire carries all the keys it
    satisfies, so daily and repeated due triggers at one timestamp run a job once."""

    name: str
    at: dt.datetime
    kind: str
    due_stream: str | None = None
    due_key: str | None = None
    due_keys: tuple[tuple[str, str], ...] = ()


def _due_key(name: str, due: dt.datetime) -> str:
    return f"{name}@{utc_text(due)}"


def _due_pairs(fire: Fire) -> tuple[tuple[str, str], ...]:
    if fire.due_keys:
        return fire.due_keys
    return ((fire.due_stream, fire.due_key),) if fire.due_stream and fire.due_key else ()


def coalesce_fires(fires: list[Fire]) -> list[Fire]:
    """One pending event per (job, time), retaining every due marker the single job execution must acknowledge."""
    grouped: dict[tuple[str, dt.datetime], list[Fire]] = {}
    for fire in fires:
        grouped.setdefault((fire.name, fire.at), []).append(fire)
    out: list[Fire] = []
    for (name, at), group in grouped.items():
        pairs = tuple(sorted({pair for fire in group for pair in _due_pairs(fire)}))
        daily = any(fire.kind == "daily" for fire in group)
        out.append(Fire(name, at, "daily" if daily else "due", due_keys=pairs))
    return sorted(out, key=lambda fire: (fire.at, fire.name))


def fires_since(jobs: list[dict], timezone: str, started: dt.datetime, now: dt.datetime,
                dues: dict[str, list[dt.datetime]], fired_dues: dict[str, set[str]],
                executed_daily: set[tuple[str, str]]) -> list[Fire]:
    """Every daily and due fire from this scheduler process's start through ten minutes past NOW that has not run,
    recomputed from the latest jobs and records each cycle. DUES holds every due in the records, overdue ones too: a
    due that passed while a job ran still fires, one the owner removed or moved fires no more or at its new time. A
    restart sets STARTED to its own start, so times missed while the process was down are not replayed."""
    started, now = started.astimezone(dt.UTC), now.astimezone(dt.UTC)
    zone = ZoneInfo(timezone)
    horizon = now + dt.timedelta(minutes=10)
    first_day = started.astimezone(zone).date()
    last_day = horizon.astimezone(zone).date()
    out: list[Fire] = []
    for job in jobs:
        name = job["name"]
        for offset in range((last_day - first_day).days + 1):
            day = first_day + dt.timedelta(days=offset)
            for at in job.get("at", []):
                hour, minute = (int(part) for part in at.split(":", 1))
                scheduled = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=zone).astimezone(dt.UTC)
                key = (name, utc_text(scheduled))
                if started <= scheduled <= horizon and key not in executed_daily:
                    out.append(Fire(name, scheduled, "daily"))
        source = job.get("dues_from")
        if source:
            for due in dues.get(source, []):
                key = _due_key(name, due)
                if started <= due <= horizon and key not in fired_dues.get(source, set()):
                    out.append(Fire(name, due, "due", source, key))
    return out


def refresh_stream_record(api, state: Path, stream: str) -> str | None:
    """Refresh one due source from Dispatch. On an outage retain its saved copy; a 404 removes it so a deleted
    record cannot keep scheduling commitments."""
    directory = _state_dir(state, stream)
    copy = directory / "stream.md"
    try:
        markdown = api.get(f"/api/v1/issues/{quote(stream)}/artifacts/stream.md/text")["markdown"]
    except SourceError as error:
        if "HTTP 404" in str(error):
            copy.unlink(missing_ok=True)
            return None
        return copy.read_text() if copy.exists() else None
    copy.write_text(markdown)
    return markdown


def _load_dues(state: Path, stream: str, now: dt.datetime) -> list[dt.datetime]:
    """Every due in the stream's saved record, past ones included: `fires_since` decides which may still fire."""
    copy = state / stream / "stream.md"
    if not copy.exists():
        return []
    return [parse_time(item["due"]) for item in parse_commitments(copy.read_text(), now) if item["due"]]


def _load_fired(state: Path, streams: set[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for stream in streams:
        path = state / stream / "fired-dues.json"
        if not path.exists():
            out[stream] = set()
            continue
        loaded = json.loads(path.read_text())
        out[stream] = set(loaded if isinstance(loaded, list) else loaded.get("fired", []))
    return out


def _write_fired(state: Path, fired: dict[str, set[str]]) -> None:
    """Only the scheduler's main thread calls this, after it has collected every finished job, so two jobs of one
    stream that end together add their keys to one set and the file is written once. The rename keeps a reader or a
    crash from seeing half a file."""
    for stream, entries in fired.items():
        directory = _state_dir(state, stream)
        partial = directory / "fired-dues.json.tmp"
        partial.write_text(json.dumps({"fired": sorted(entries)}, indent=2) + "\n")
        os.replace(partial, directory / "fired-dues.json")


def _jobs(path: Path) -> tuple[str, list[dict]]:
    data = json.loads(path.read_text())
    if not isinstance(data.get("timezone"), str) or not isinstance(data.get("jobs"), list):
        raise ValueError("jobs file must have string timezone and list jobs")
    for job in data["jobs"]:
        required = ("name", "at", "command", "cwd", "timeout_s")
        if any(key not in job for key in required) or not isinstance(job["at"], list) or not isinstance(job["command"], list):
            raise ValueError(f"invalid job: {job!r}")
    return data["timezone"], data["jobs"]


HEARTBEAT_INTERVAL_S = 60
MAX_CONCURRENT_JOBS = 4


def write_heartbeat(state: Path, now: dt.datetime, pid: int) -> None:
    """`DIR/heartbeat`: one line, the UTC time and the scheduler's pid. A file under 15 minutes old means it runs."""
    state.mkdir(parents=True, exist_ok=True)
    (state / "heartbeat").write_text(f"{utc_text(now)}\t{pid}\n")


def _beat(state: Path, interval_s: float) -> None:
    try:
        while True:
            write_heartbeat(state, dt.datetime.now(dt.UTC), os.getpid())
            time.sleep(interval_s)
    except BaseException as error:  # noqa: BLE001 - any death of the heartbeat must stop the scheduler
        print(f"pilot-schedule heartbeat stopped: {error!r}", file=sys.stderr, flush=True)
        os._exit(1)


def start_heartbeat(state: Path, interval_s: float = HEARTBEAT_INTERVAL_S) -> threading.Thread:
    """Write the heartbeat from a daemon thread, so it stays fresh while a job runs up to its timeout. A heartbeat
    that cannot be written ends the whole process with exit 1: a stale file must mean the scheduler is down."""
    thread = threading.Thread(target=_beat, args=(state, interval_s), name="pilot-heartbeat", daemon=True)
    thread.start()
    return thread


def start_scheduled_command(job: dict) -> subprocess.Popen:
    """Start one scheduled command in its own process group."""
    return subprocess.Popen(job["command"], cwd=job["cwd"], start_new_session=True)


def finish_scheduled_command(process: subprocess.Popen, timeout_s: float) -> int:
    """Wait for a started command. If its timeout expires, kill and reap every descendant before reporting exit 124:
    a wrapper around omp must not leave omp alive."""
    try:
        return process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return 124


def _await_job(fire: Fire, process, timeout_s: float, results: queue.Queue) -> None:
    """A job's worker thread: it only waits for the process and hands the outcome to the main thread, which alone
    touches the scheduler's state."""
    started = time.monotonic()
    try:
        code: int | str = finish_scheduled_command(process, timeout_s)
    except BaseException as error:  # noqa: BLE001 - the main thread must learn the job ended, or it never runs again
        code = f"error {error!r}"
    results.put((fire, code, time.monotonic() - started))


def _wait_for_result(results: queue.Queue, seconds: float) -> list:
    """Block until a job ends or SECONDS pass; return the outcome that woke it, if any."""
    try:
        return [results.get(timeout=seconds)]
    except queue.Empty:
        return []


def schedule(jobs_file: Path, state: Path, api=None, clock=None, wait=_wait_for_result,
             max_jobs: int = MAX_CONCURRENT_JOBS) -> None:
    """Run the local scheduler until stopped. Each cycle collects the jobs that ended, reloads the jobs file,
    refreshes the due-source records and recomputes the fires from them (`fires_since`), so nothing a record no longer
    holds can run. It starts every fire that is due, earliest first, while fewer than MAX_JOBS jobs run; a job runs
    at most one instance at a time, so a fire of a running job waits until it ends. Then it waits for the next fire
    time, a job's end or ten minutes. CLOCK and WAIT are injectable so the loop itself is tested on a fake clock."""
    clock = clock or (lambda: dt.datetime.now(dt.UTC))
    started_at = clock()
    print(f"pilot-schedule started {utc_text(started_at)}", flush=True)
    heartbeat = start_heartbeat(state)
    api = api or Dispatch()
    fired: dict[str, set[str]] = {}
    executed_daily: set[tuple[str, str]] = set()
    running: dict[str, Fire] = {}
    results: queue.Queue = queue.Queue()
    finished: list = []
    while True:
        if not heartbeat.is_alive():
            print("pilot-schedule heartbeat thread is not running", file=sys.stderr, flush=True)
            raise SystemExit(1)
        while True:
            try:
                finished.append(results.get_nowait())
            except queue.Empty:
                break
        if finished:
            for fire, code, elapsed in finished:
                del running[fire.name]
                print(f"{utc_text(clock())} ran {fire.name} exit {code} in {elapsed:.1f}s", flush=True)
                # A daily time is marked run whatever the fire's kind: a due that coincides with it covers it.
                executed_daily.add((fire.name, utc_text(fire.at)))
                for stream, key in fire.due_keys:
                    fired.setdefault(stream, set()).add(key)
            _write_fired(state, fired)
            finished = []
        timezone, jobs = _jobs(jobs_file)
        now = clock()
        streams = {job["dues_from"] for job in jobs if job.get("dues_from")}
        for stream in streams:
            refresh_stream_record(api, state, stream)
            fired.setdefault(stream, _load_fired(state, {stream})[stream])
        dues = {stream: _load_dues(state, stream, now) for stream in streams}
        fires = coalesce_fires(fires_since(jobs, timezone, started_at, now, dues, fired, executed_daily))
        by_name = {job["name"]: job for job in jobs}
        for fire in fires:
            if fire.at > now or len(running) >= max_jobs:
                break
            if fire.name in running:
                continue
            job = by_name[fire.name]
            process = start_scheduled_command(job)
            running[fire.name] = fire
            print(f"{utc_text(now)} start {fire.name} for {utc_text(fire.at)}", flush=True)
            threading.Thread(target=_await_job, args=(fire, process, job["timeout_s"], results),
                             name=f"pilot-job-{fire.name}", daemon=True).start()
        upcoming = [(fire.at - now).total_seconds() for fire in fires if fire.at > now and fire.name not in running]
        finished = wait(results, min([600.0, *upcoming]))


def census_ids(measured: dict, stream: str) -> list[str]:
    """The live top-level omp sessions currently attributed to STREAM's configured tmux windows."""
    ids = {
        session["session"]
        for window in sessions.stream_windows(stream)
        for session in measured.get("windows", {}).get(window, [])
        if session.get("session")
    }
    return sorted(ids)


def census(stream: str, out: Path | None = None) -> list[str]:
    """Measure the machine once and optionally freeze the stream's session ids for later before/after measurement."""
    measured = sessions.measure(sessions.read_processes(), sessions.read_panes(), sessions.read_containers())
    ids = census_ids(measured, stream)
    text = "".join(f"{session}\n" for session in ids)
    if out:
        out.write_text(text)
    print(text, end="")
    return ids


PROJECTS = ("INFRA", "BET", "LINUX", "OSSV")
# `dispatch issue-update --status done --reason` posts the reason as an issue message just before the close: on
# INFRA-975 the message came 63 ms before its `issue.closed`.
REASON_GAP = dt.timedelta(seconds=60)
DISPATCH_ISSUE_PATH = re.compile(r"^/issues/[A-Z][A-Z0-9]*-\d+/?$")
DISPATCH_REF = re.compile(r"dispatch://[A-Za-z0-9-]+/[^\s)>\]]+")


def _in_window(event: dict, start: dt.datetime, end: dt.datetime) -> bool:
    return start <= parse_time(event["created_at"]) < end


def _session(event: dict) -> str | None:
    actor = event.get("actor") or {}
    return actor.get("id") if actor.get("kind") == "session" else None


def _payload(event: dict) -> dict:
    return event.get("payload") or {}


def _issue_status(event: dict) -> str | None:
    """The issue's status after EVENT. Only `issue.*` events carry the issue's own status: a parent's `child.status`
    event carries its child's."""
    return _payload(event).get("status") if event["type"].startswith("issue.") else None


def _moved_to_done(event: dict) -> bool:
    payload = _payload(event)
    return (event["type"] in ("issue.updated", "issue.closed") and payload.get("status") == "done"
            and "status" in (payload.get("changed") or []))


def _ledger(event: dict) -> dict | None:
    """The parsed LEDGER line of a `comment.created` event, or None for any other event or comment."""
    if event["type"] != "comment.created":
        return None
    body = _payload(event).get("body") or ""
    return parse_ledger_line(body) if body.startswith("LEDGER ") else None


def _references(event: dict) -> set[str]:
    """The issue keys an `issue.created` event links or mentions, other than the new issue itself."""
    payload = _payload(event)
    refs = {item.get("id") for item in payload.get("references_changed") or [] if item.get("kind") == "issue"}
    refs |= set(ISSUE_KEY.findall(json.dumps(payload)))
    return {ref for ref in refs if ref and ref != event.get("issue_key")}


def _evidence(text: str) -> bool:
    """Whether TEXT carries a link to a merged pull request, a measured result or a launch record: any https link
    other than to a Dispatch issue page, or a `dispatch://` reference to a document, comment or message."""
    if DISPATCH_REF.search(text):
        return True
    for url in EXTERNAL_URL.findall(text):
        parsed = urlparse(url.rstrip(".,;"))
        if not (parsed.netloc.startswith("dispatch.") and DISPATCH_ISSUE_PATH.match(parsed.path)):
            return True
    return False


class _History:
    """The issue list of PROJECTS and the events of each issue updated since START, read once and in parallel; any
    other issue's events are read when first asked for (an old child that nobody touched in the window)."""

    def __init__(self, api, start: dt.datetime) -> None:
        self.api = api
        self.listing: dict[str, dict] = {}
        for project in PROJECTS:
            for issue in api.get(f"/api/v1/issues?project={quote(project)}"):
                self.listing[issue["key"]] = issue
        recent = [key for key, issue in self.listing.items()
                  if issue.get("updated_at") and parse_time(issue["updated_at"]) >= start]
        with cf.ThreadPoolExecutor(16) as pool:
            self.events: dict[str, list[dict]] = dict(zip(recent, pool.map(lambda key: events(api, key), recent),
                                                          strict=True))

    def of(self, key: str) -> list[dict]:
        if key not in self.events:
            self.events[key] = events(self.api, key)
        return self.events[key]


def _status_at(history: list[dict], at: dt.datetime) -> str | None:
    value = None
    for event in history:
        if parse_time(event["created_at"]) > at:
            break
        value = _issue_status(event) or value
    return value


def _parent_at(history: list[dict], at: dt.datetime) -> str | None:
    """The child's parent at AT, or None when it did not exist yet. A current issue-list parent cannot answer this:
    a child may be created or reparented after the close being measured."""
    parent = None
    exists = False
    for event in history:
        if parse_time(event["created_at"]) > at:
            break
        if event["type"] == "issue.created":
            exists = True
            parent = _payload(event).get("parent")
        elif event["type"] in ("issue.updated", "issue.closed") and "parent" in _payload(event):
            parent = _payload(event).get("parent")
    return parent if exists else None


def _done_reason(history: list[dict], index: int) -> str:
    """The reason message the closer posted just before the done event at INDEX, or ""."""
    done = history[index]
    at = parse_time(done["created_at"])
    for event in reversed(history[:index]):
        if at - parse_time(event["created_at"]) > REASON_GAP:
            break
        if event["type"] == "message.created" and event.get("actor", {}).get("id") == done.get("actor", {}).get("id"):
            return _payload(event).get("body") or ""
    return ""


def _its_dones(history: list[dict], session: str, start: dt.datetime, end: dt.datetime) -> list[int]:
    """Indexes of the window's done events that are SESSION's: it made the change, or it held the claim then."""
    holder = None
    out: list[int] = []
    for index, event in enumerate(history):
        if event["type"] == "issue.claimed":
            holder = ((_payload(event).get("claim") or {}).get("actor") or {}).get("id") or _session(event)
        elif event["type"] == "issue.released":
            holder = None
        if _moved_to_done(event) and _in_window(event, start, end) and session in (_session(event), holder):
            out.append(index)
    return out


def _ledger_starts(stream_events: list[dict], start: dt.datetime, end: dt.datetime) -> list[str]:
    return sorted({
        session for event in stream_events
        if _in_window(event, start, end) and (session := _session(event))
        and (_ledger(event) or {}).get("kind") == "start"
    })


def _measure_session(session: str, stream: str, hist: _History, start: dt.datetime, end: dt.datetime) -> dict:
    windowed = {key: [e for e in history if _in_window(e, start, end)] for key, history in hist.events.items()}
    claims = {key: [parse_time(e["created_at"]) for e in history
                    if e["type"] == "issue.claimed" and _session(e) == session]
              for key, history in windowed.items()}
    claimed = sorted(key for key, times in claims.items() if times)
    created_events = sorted((e for history in windowed.values() for e in history
                             if e["type"] == "issue.created" and _session(e) == session),
                            key=lambda e: (e["created_at"], e["issue_key"]))

    sub_issues: list[dict] = []
    for event in created_events:
        key = event["issue_key"]
        parents = {_payload(event).get("parent"), hist.listing.get(key, {}).get("parent")} & set(claimed)
        if parents:
            sub_issues.append({"issue": key, "rule": f"parent {min(parents)}"})
            continue
        made = parse_time(event["created_at"])
        linked = next((ref for ref in claimed if ref in _references(event) and any(
            dt.timedelta() <= made - at <= dt.timedelta(hours=24) for at in claims[ref])), None)
        if linked:
            sub_issues.append({"issue": key, "rule": f"linked to {linked} within 24h of claim"})

    ledger = [(parse_time(e["created_at"]), line) for e in windowed.get(stream, [])
              if _session(e) == session and (line := _ledger(e)) and not line.get("malformed")]
    moved = [(at, {"issue": line["issue"], "kind": "ledger handoff"}) for at, line in ledger if line["kind"] == "handoff"]
    for key in claimed:
        for event in windowed[key]:
            payload = _payload(event)
            holder = ((payload.get("previous_claim") or {}).get("actor") or {}).get("id")
            if event["type"] == "issue.released" and holder == session and payload.get("status") != "done":
                moved.append((parse_time(event["created_at"]), {"issue": key, "kind": "released"}))
    work_moved = [item for _, item in sorted(moved, key=lambda pair: (pair[0], pair[1]["issue"]))]

    # The session's LEDGER done lines, from the stream's whole history: a line just before the window can be the
    # evidence for a close inside it. one-issue-session posts the line, then closes the issue.
    ledger_done = [(parse_time(e["created_at"]), line) for e in hist.of(stream)
                   if _session(e) == session and (line := _ledger(e)) and line.get("kind") == "done"]
    children_by_done: dict[str, set[str]] = {}
    done_without_evidence: set[str] = set()
    for key in claimed:
        history = hist.of(key)
        closes = [parse_time(event["created_at"]) for event in history if _moved_to_done(event)]
        for index in _its_dones(history, session, start, end):
            at = parse_time(history[index]["created_at"])
            previous = max((close for close in closes if close < at), default=None)
            # Candidate discovery may use the current listing, but each candidate must have existed and had this
            # parent at this close; a future child is neither a child nor open at an earlier close.
            children = {k for k, issue in hist.listing.items() if issue.get("parent") == key}
            children |= {
                event["issue_key"] for issue_history in hist.events.values() for event in issue_history
                if event["type"] == "issue.created" and _payload(event).get("parent") == key
            }
            for child in sorted(children):
                child_history = hist.of(child)
                created_by_session = any(
                    event["type"] == "issue.created" and _session(event) == session
                    and parse_time(event["created_at"]) <= at for event in child_history
                )
                if created_by_session and _parent_at(child_history, at) == key and _status_at(child_history, at) != "done":
                    children_by_done.setdefault(key, set()).add(child)
            # A LEDGER done line is this close's evidence only when this is the first close at or after it: it was
            # posted after the previous close and no later than this one. External links on the issue never count.
            ledger_evidence = any(
                line["issue"] == key and (previous is None or posted > previous) and posted <= at
                and _evidence(f"{line['text']} {line['link']}")
                for posted, line in ledger_done
            )
            if not (_evidence(_done_reason(history, index)) or ledger_evidence):
                done_without_evidence.add(key)
    done_with_child = [
        {"issue": key, "open_children": sorted(children)} for key, children in sorted(children_by_done.items())
    ]

    asks = sorted({_payload(e)["id"] for history in windowed.values() for e in history
                   if e["type"] == "ask.opened" and _session(e) == session and _payload(e).get("id")})
    activity = [parse_time(e["created_at"]) for history in windowed.values() for e in history if _session(e) == session]
    return {
        "session": session,
        "issues_claimed": claimed,
        "issues_created": [e["issue_key"] for e in created_events],
        "sub_issues_of_own_work": sub_issues,
        "work_moved": work_moved,
        "done_with_own_child_open": done_with_child,
        "done_without_evidence": sorted(set(done_without_evidence)),
        "asks_opened": asks,
        "last_activity": utc_text(max(activity)) if activity else None,
        "held_one_issue": len(claimed) == 1,
        "exit_after_done": None,
    }


def measure(api, stream: str, start: dt.datetime, end: dt.datetime, session_ids: list[str] | None = None) -> dict:
    """Measure the pilot guard across INFRA, BET, LINUX and OSSV from Dispatch events only, never transcripts.

    A done counts as a session's when it made the change or held the claim at that moment, so an issue it released
    that someone else finished is not its done."""
    start, end = start.astimezone(dt.UTC), end.astimezone(dt.UTC)
    hist = _History(api, start)
    selected = sorted(set(session_ids)) if session_ids is not None else _ledger_starts(hist.of(stream), start, end)
    measured = [_measure_session(session, stream, hist, start, end) for session in selected]
    count = len(measured)
    totals = {
        "sessions": count,
        "issues_created_per_session": sum(len(item["issues_created"]) for item in measured) / count if count else 0,
        "sub_issues_of_own_work": sum(len(item["sub_issues_of_own_work"]) for item in measured),
        "work_moved": sum(len(item["work_moved"]) for item in measured),
        "done_with_own_child_open": sum(len(item["done_with_own_child_open"]) for item in measured),
        "done_without_evidence": sum(len(item["done_without_evidence"]) for item in measured),
        "share_held_one_issue": sum(item["held_one_issue"] for item in measured) / count if count else 0,
    }
    return {
        "stream": stream,
        "start": utc_text(start),
        "end": utc_text(end),
        "sessions": measured,
        "totals": totals,
        "note": "exit_after_done is null: whether a session exited after its done needs its transcript, which this "
                "measurement does not read.",
    }


def measure_markdown(report: dict) -> str:
    """The human counterpart of `--json`: one row per session and the stream totals the pilot compares."""
    lines = [
        "| session | claimed | created | own sub-issues | moved | done with own child open | done without evidence | asks | held one issue | last activity | exit after done |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for item in report["sessions"]:
        lines.append(
            "| {session} | {claimed} | {created} | {sub} | {moved} | {child} | {evidence} | {asks} | {held} | {activity} | null |".format(
                session=item["session"], claimed=len(item["issues_claimed"]), created=len(item["issues_created"]),
                sub=len(item["sub_issues_of_own_work"]), moved=len(item["work_moved"]),
                child=len(item["done_with_own_child_open"]), evidence=len(item["done_without_evidence"]),
                asks=len(item["asks_opened"]), held="yes" if item["held_one_issue"] else "no",
                activity=item["last_activity"] or "—",
            )
        )
    totals = report["totals"]
    lines.extend([
        "",
        "| total | sessions | created/session | own sub-issues | moved | done with own child open | done without evidence | share held one issue |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| stream | {sessions} | {created:.2f} | {sub} | {moved} | {child} | {evidence} | {share:.0%} |".format(
            sessions=totals["sessions"], created=totals["issues_created_per_session"],
            sub=totals["sub_issues_of_own_work"], moved=totals["work_moved"],
            child=totals["done_with_own_child_open"], evidence=totals["done_without_evidence"],
            share=totals["share_held_one_issue"],
        ),
        "",
        report["note"],
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None, api: Dispatch | None = None) -> int:
    """The `chief-dashboard pilot` CLI with an injectable Dispatch reader for its recorded-response tests."""
    parser = argparse.ArgumentParser(prog="chief-dashboard pilot")
    command = parser.add_subparsers(dest="command", required=True)
    facts_parser = command.add_parser("facts")
    facts_parser.add_argument("--stream", required=True)
    facts_parser.add_argument("--state", type=Path, required=True)
    facts_parser.add_argument("--now", help="ISO 8601; a naive timestamp is UTC")
    facts_parser.add_argument("--pilot-start", help="ISO 8601 pilot boundary for persistent LEDGER start sessions; naive is UTC")
    summary_parser = command.add_parser("summary")
    summary_parser.add_argument("--stream", required=True)
    summary_parser.add_argument("--state", type=Path, required=True)
    summary_parser.add_argument("--window", required=True)
    summary_parser.add_argument("--dry-run", action="store_true")
    schedule_parser = command.add_parser("schedule")
    schedule_parser.add_argument("--jobs", type=Path, required=True)
    schedule_parser.add_argument("--state", type=Path, required=True)
    schedule_parser.add_argument("--max-jobs", type=int, choices=range(1, 65), metavar="N", default=MAX_CONCURRENT_JOBS,
                                 help=f"jobs that may run at once (1-64, default {MAX_CONCURRENT_JOBS})")
    census_parser = command.add_parser("census")
    census_parser.add_argument("--stream", required=True)
    census_parser.add_argument("--out", type=Path)
    measure_parser = command.add_parser("measure")
    measure_parser.add_argument("--stream", required=True)
    measure_parser.add_argument("--start", required=True, help="ISO 8601; a naive timestamp is UTC")
    measure_parser.add_argument("--end", required=True, help="ISO 8601; a naive timestamp is UTC")
    measure_parser.add_argument("--sessions-file", type=Path)
    measure_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "schedule":
            schedule(args.jobs, args.state, max_jobs=args.max_jobs)
            return 0
        if args.command == "census":
            census(args.stream, args.out)
            return 0
        reader = api or Dispatch()
        if args.command == "facts":
            now = parse_time(args.now) if args.now else dt.datetime.now(dt.UTC)
            pilot_start = parse_time(args.pilot_start) if args.pilot_start else PILOT_START
            result, _ = facts(reader, args.stream, args.state, now, pilot_start)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command == "summary":
            return summary(reader, args.stream, args.state, args.window, args.dry_run)
        session_ids = ([line.strip() for line in args.sessions_file.read_text().splitlines() if line.strip()]
                       if args.sessions_file else None)
        report = measure(reader, args.stream, parse_time(args.start), parse_time(args.end), session_ids)
        print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else measure_markdown(report))
        return 0
    except (OSError, ValueError, SourceError, subprocess.SubprocessError) as error:
        print(f"pilot: {error}", file=sys.stderr)
        return 1
