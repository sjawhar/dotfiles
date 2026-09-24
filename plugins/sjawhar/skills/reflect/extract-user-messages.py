#!/usr/bin/env python3
"""
Extract every genuine human-authored user turn in a window from the sessions.db
index built by index-sessions.py.

The reflect skill's primary analysis requires the orchestrator to READ EVERY ONE
of these personally. This helper only makes that read tractable: it walks the
index (sessions/turns), fetches each user turn from the source JSONL by line
number, drops injected noise (envoy/dispatch notification blocks, XML system
notices, background-job snapshots, history-resume re-prompts, slash-command
wrappers), redacts secrets, and dedupes exact repeats while recording how many
duplicates were suppressed.

Output: JSONL rows {ts, session, project, turn, chars, dups, text}, plus an
optional readable transcript (--text) with `--- #N <ts> <project>` headers.

Usage:
    python extract-user-messages.py [--days N | --since ISO] [--db PATH]
                                    [--out OUT.jsonl] [--text OUT.txt]
                                    [--source omp|claude|all]
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def get_db_path() -> Path:
    return Path.home() / ".dotfiles" / ".claude" / "sessions.db"


# Injected noise: turns stored with role=user that a harness, not the human, wrote.
NOISE_PATTERNS = {
    # Envoy notification blocks relayed into the turn
    "envoy-block": re.compile(r"\benvoy:\s*$|\n\s*to: you \(|reply_with: \"envoy_send"),
    # System-authored XML blocks (reminders, notices, task/async results, memories)
    "xml-injection": re.compile(
        r"</?(system-reminder|system-notice|system-directive|knives-guidance"
        r"|task-result|async-result|files|memories)\b"
    ),
    # Background-job completion snapshots injected as user turns
    "job-snapshot": re.compile(
        r"^##+ (Completed|Still Running)\s*\(|^Background job \w+ has completed"
    ),
    # Dispatch open-asks summaries
    "dispatch-summary": re.compile(r"^Dispatch authored-ask summary"),
    # Goal-loop / continuation re-prompts when resuming a prior conversation
    "history-resume": re.compile(r"^Resume prior conversation"),
}

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|xox[pbc]-[A-Za-z0-9-]{8,}|gh[ps]_[A-Za-z0-9]{8,}"
    r"|AKIA[A-Z0-9]{12,}|Bearer [A-Za-z0-9._-]{12,})"
)

# Slash-command wrapper: keep only the human's own text after "User:", drop the
# injected skill body when there is no human text at all.
SLASH_WRAPPER_RE = re.compile(r"^\s*\[IMPORTANT: User invoked.*?\nUser:\s*(.*)\Z", re.S)

SOURCE_MAP = {
    "omp": ("oh-my-pi",),
    "claude": ("claude-code",),
    "all": ("oh-my-pi", "claude-code"),
}


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text") or ""
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def cutoff_minute(args) -> str:
    """Window start as a minute-resolution ISO string, compared lexically."""
    if args.since:
        return args.since.replace(" ", "T")[:16]
    start = datetime.now(timezone.utc) - timedelta(days=args.days)
    return start.strftime("%Y-%m-%dT%H:%M")


def main():
    parser = argparse.ArgumentParser(
        description="Extract genuine human-authored user turns from the session index"
    )
    parser.add_argument(
        "--days",
        type=float,
        default=7,
        help="Window: turns from the past N days (default: 7)",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Window start as ISO timestamp (e.g. 2026-09-05T22:48); overrides --days",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Database path (default: ~/.dotfiles/.claude/sessions.db)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="-",
        help="JSONL output path (default: stdout)",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="Also write a readable transcript with '--- #N <ts> <project>' headers",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="omp",
        choices=sorted(SOURCE_MAP),
        help="Session source(s) to extract (default: omp). OpenCode turns carry no "
        "line numbers in the index and are not supported here.",
    )
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else get_db_path()
    if not db_path.exists():
        print(f"Error: session index not found: {db_path}", file=sys.stderr)
        print("Run index-sessions.py first (index -> extract -> read).", file=sys.stderr)
        sys.exit(1)

    cut = cutoff_minute(args)
    sources = SOURCE_MAP[args.source]

    conn = sqlite3.connect(str(db_path))
    placeholders = ",".join("?" for _ in sources)
    rows = conn.execute(
        "SELECT s.id, s.project, s.timestamp, s.source_path, t.turn_number, t.line_start"
        " FROM turns t JOIN sessions s ON s.id = t.session_id"
        f" WHERE t.type = 'user' AND s.source IN ({placeholders})"
        " ORDER BY s.source_path, t.turn_number",
        sources,
    ).fetchall()
    conn.close()

    seen = {}
    out = []
    scanned = 0
    sessions_seen = set()
    current_path = None
    current_lines = []

    for sid, proj, sts, path, turn, line_start in rows:
        if not path or not line_start:
            continue
        if path != current_path:
            current_path = path
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    current_lines = fh.readlines()
            except OSError:
                current_lines = []
        if line_start > len(current_lines):
            continue
        try:
            record = json.loads(current_lines[line_start - 1])
        except json.JSONDecodeError:
            continue

        ts = record.get("timestamp") or sts or ""
        if ts[:16].replace(" ", "T") <= cut:
            continue
        scanned += 1

        txt = text_of(record.get("message", {}).get("content"))
        if not txt.strip():
            continue

        if any(rx.search(txt) for rx in NOISE_PATTERNS.values()):
            continue

        m = SLASH_WRAPPER_RE.search(txt)
        if m:
            txt = m.group(1).strip()
        elif txt.startswith("[IMPORTANT: User invoked"):
            continue

        txt = SECRET_RE.sub("<redacted>", txt).strip()
        key = re.sub(r"\s+", " ", txt)[:400]
        if key in seen:
            seen[key]["dups"] += 1
            continue
        rec = {
            "ts": ts,
            "session": sid,
            "project": proj,
            "turn": turn,
            "chars": len(txt),
            "dups": 0,
            "text": txt,
        }
        seen[key] = rec
        out.append(rec)
        sessions_seen.add(sid)

    out.sort(key=lambda r: r["ts"])

    if args.out == "-":
        for rec in out:
            sys.stdout.write(json.dumps(rec) + "\n")
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            for rec in out:
                f.write(json.dumps(rec) + "\n")

    if args.text:
        with open(args.text, "w", encoding="utf-8") as f:
            for i, rec in enumerate(out, 1):
                dup = f" [x{rec['dups'] + 1}]" if rec["dups"] else ""
                f.write(
                    f"--- #{i} {rec['ts'][:16]} {rec['project'] or '?'}{dup}\n"
                    f"{rec['text']}\n\n"
                )

    print(
        f"window since {cut}: {len(out)} unique prompts, "
        f"{sum(r['chars'] for r in out)} chars, "
        f"{sum(r['dups'] for r in out)} duplicates suppressed, "
        f"{len(sessions_seen)} sessions, {scanned} user turns scanned",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
