#!/usr/bin/env python3
"""SQLite store for the reflect skill's daily measurement (AGENTC daily-measure job).

Makes the weekly reflect run's two label corpora -- Sami's Dispatch events
(classify-sami-events.py's codebook) and session turns (experiments-readout.py's
correction/other/not_sami labels) -- daily and incremental: one row per labelled
event/turn, keyed by a stable id so a rerun over the same data is a no-op, plus a
`daily_rates` table recomputed from those rows each run so a reader (daily-readout.py,
the weekly reflect run) never recomputes the corpus itself.

Default path: ~/.dotfiles/.claude/reflect-store.db (gitignored, alongside sessions.db).

Tables:
  job_state(key, value, updated_at)            -- resume watermarks, one row per stream
  dispatch_events(id, issue_key, project, seq, type, created_at, actor_id, asking_session)
  dispatch_labels(event_id, labels, labeled_at, model)   -- labels is a JSON array
  turns(id, session_id, host, project, ts, chars)         -- id = "<session_id>|<ts>"
  turn_labels(turn_id, label, source, labeled_at)         -- source: jev|strong
  daily_rates(day, kind, label, count, total, rate)       -- kind: dispatch|turn

Every insert is `INSERT OR IGNORE` on the row's natural id, so re-running the daily job
over an overlapping window (the resumability safety margin) never re-labels or
double-counts a row already stored -- that is the idempotence the daily job relies on.
`daily_rates` is the exception: it is fully recomputed (DELETE + INSERT) from the label
tables on every call, because aggregating is cheap and a partial recompute risks leaving
a day's rate stale after a late-arriving label.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispatch_events (
    id INTEGER PRIMARY KEY,
    issue_key TEXT NOT NULL,
    project TEXT,
    seq INTEGER,
    type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    actor_id TEXT,
    asking_session TEXT
);
CREATE INDEX IF NOT EXISTS idx_dispatch_events_created ON dispatch_events(created_at);

CREATE TABLE IF NOT EXISTS dispatch_labels (
    event_id INTEGER PRIMARY KEY REFERENCES dispatch_events(id),
    labels TEXT NOT NULL,
    labeled_at TEXT NOT NULL,
    model TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    host TEXT NOT NULL,
    project TEXT,
    ts TEXT NOT NULL,
    chars INTEGER
);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);

CREATE TABLE IF NOT EXISTS turn_labels (
    turn_id TEXT PRIMARY KEY REFERENCES turns(id),
    label TEXT NOT NULL,
    source TEXT NOT NULL,
    labeled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_rates (
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    count INTEGER NOT NULL,
    total INTEGER NOT NULL,
    rate REAL NOT NULL,
    PRIMARY KEY (day, kind, label)
);
"""


def get_db_path() -> Path:
    return Path.home() / ".dotfiles" / ".claude" / "reflect-store.db"


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# --- job_state (resume watermarks) ------------------------------------------------

def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM job_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str, now_iso: str) -> None:
    conn.execute(
        "INSERT INTO job_state (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now_iso),
    )


# --- dispatch events/labels --------------------------------------------------------

def upsert_dispatch_event(conn: sqlite3.Connection, e: dict) -> None:
    author = ((e.get("payload") or {}).get("author") or {}).get("origin")
    conn.execute(
        "INSERT OR IGNORE INTO dispatch_events"
        " (id, issue_key, project, seq, type, created_at, actor_id, asking_session)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            e["id"], e["issue_key"], e.get("project"), e.get("seq"), e["type"],
            e["created_at"], (e.get("actor") or {}).get("id"),
            json.dumps(author) if author else None,
        ),
    )


def dispatch_event_has_label(conn: sqlite3.Connection, event_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM dispatch_labels WHERE event_id = ?", (event_id,)
    ).fetchone() is not None


def upsert_dispatch_label(
    conn: sqlite3.Connection, event_id: int, labels: list[str], model: str, now_iso: str
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO dispatch_labels (event_id, labels, labeled_at, model)"
        " VALUES (?, ?, ?, ?)",
        (event_id, json.dumps(labels), now_iso, model),
    )


# --- turns/turn labels ---------------------------------------------------------------

def upsert_turn(conn: sqlite3.Connection, turn_id: str, session_id: str, host: str,
                 project: str | None, ts: str, chars: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO turns (id, session_id, host, project, ts, chars)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (turn_id, session_id, host, project, ts, chars),
    )


def turn_has_label(conn: sqlite3.Connection, turn_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM turn_labels WHERE turn_id = ?", (turn_id,)
    ).fetchone() is not None


def upsert_turn_label(conn: sqlite3.Connection, turn_id: str, label: str, source: str,
                       now_iso: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO turn_labels (turn_id, label, source, labeled_at)"
        " VALUES (?, ?, ?, ?)",
        (turn_id, label, source, now_iso),
    )


# --- daily rates ----------------------------------------------------------------------

def recompute_daily_rates(conn: sqlite3.Connection) -> int:
    """Rebuild daily_rates from dispatch_labels and turn_labels. Full recompute (DELETE +
    INSERT): cheap aggregate SQL over label rows that already exist, never a model call,
    so redoing it in full on every run is simpler and safer than tracking which days a
    late-arriving label touched."""
    conn.execute("DELETE FROM daily_rates")

    # Dispatch: multi-label, so a row contributes to every label it carries; the
    # denominator is the day's total labelled dispatch events (not a per-label count).
    totals = dict(conn.execute(
        "SELECT substr(de.created_at, 1, 10) AS day, COUNT(*)"
        " FROM dispatch_labels dl JOIN dispatch_events de ON de.id = dl.event_id"
        " GROUP BY day"
    ).fetchall())
    label_rows = conn.execute(
        "SELECT substr(de.created_at, 1, 10) AS day, dl.labels"
        " FROM dispatch_labels dl JOIN dispatch_events de ON de.id = dl.event_id"
    ).fetchall()
    counts: dict[tuple[str, str], int] = {}
    for day, labels_json in label_rows:
        for label in json.loads(labels_json):
            counts[(day, label)] = counts.get((day, label), 0) + 1
    n = 0
    for (day, label), count in counts.items():
        total = totals[day]
        conn.execute(
            "INSERT INTO daily_rates (day, kind, label, count, total, rate) VALUES (?,?,?,?,?,?)",
            (day, "dispatch", label, count, total, count / total),
        )
        n += 1

    # Turns: single-label (correction/other/not_sami), denominator is the day's total
    # labelled turns.
    turn_totals = dict(conn.execute(
        "SELECT substr(t.ts, 1, 10) AS day, COUNT(*)"
        " FROM turn_labels tl JOIN turns t ON t.id = tl.turn_id"
        " GROUP BY day"
    ).fetchall())
    turn_counts = conn.execute(
        "SELECT substr(t.ts, 1, 10) AS day, tl.label, COUNT(*)"
        " FROM turn_labels tl JOIN turns t ON t.id = tl.turn_id"
        " GROUP BY day, tl.label"
    ).fetchall()
    for day, label, count in turn_counts:
        total = turn_totals[day]
        conn.execute(
            "INSERT INTO daily_rates (day, kind, label, count, total, rate) VALUES (?,?,?,?,?,?)",
            (day, "turn", label, count, total, count / total),
        )
        n += 1

    conn.commit()
    return n


def seed_dispatch_labels(conn: sqlite3.Connection, dispatch_human_path: str,
                          labels_path: str, model: str, now_iso: str) -> tuple[int, int]:
    """One-time seed from a prior reflect run's output (classify-sami-events.py's
    `labels.jsonl`, keyed there by a run-local `dn`, joined back to its event's real,
    stable Dispatch id via the (issue, created_at, kind) triple -- unique across the
    2,352-event 2026-10-04 corpus, verified with zero collisions before this was written.
    Returns (events_inserted, labels_inserted)."""
    events = []
    with open(dispatch_human_path) as f:
        for line in f:
            e = json.loads(line)
            if e["actor"]["id"].startswith("sami@"):
                events.append(e)
    events.sort(key=lambda e: e["created_at"])
    key_to_event = {}
    for e in events:
        kind = "ask" if e["type"] == "ask.answered" else "comment"
        key_to_event[(e["issue_key"], e["created_at"], kind)] = e

    n_events = n_labels = 0
    with open(labels_path) as f:
        for line in f:
            lb = json.loads(line)
            key = (lb["issue"], lb["created_at"], lb["kind"])
            e = key_to_event.get(key)
            if e is None:
                raise ValueError(
                    f"seed_dispatch_labels: no event in {dispatch_human_path} matches "
                    f"labels.jsonl dn={lb['dn']} (issue={lb['issue']!r}, "
                    f"created_at={lb['created_at']!r}, kind={lb['kind']!r}); "
                    "the two files must come from the same extraction run"
                )
            if lb["labels"] is None:
                continue  # classify-sami-events.py's own "unlabeled" marker; never seed a null
            upsert_dispatch_event(conn, e)
            n_events += 1
            if not dispatch_event_has_label(conn, e["id"]):
                upsert_dispatch_label(conn, e["id"], lb["labels"], model, now_iso)
                n_labels += 1
    conn.commit()
    return n_events, n_labels


def _cli() -> None:
    import argparse
    from datetime import datetime, timezone

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    seed = sub.add_parser("seed-dispatch", help="Seed dispatch_events/dispatch_labels from a prior run")
    seed.add_argument("--dispatch-human", required=True)
    seed.add_argument("--labels", required=True)
    seed.add_argument("--model", default="claude (2026-10-04 reflect run, model alias 'default')")

    sub.add_parser("rates", help="Recompute and print daily_rates")
    sub.add_parser("show", help="Print row counts per table")

    dump = sub.add_parser(
        "dump-turn-labels",
        help="Write turn_labels as a {id,label} JSONL cache file, in the exact shape "
        "experiments-readout.py's label_turns(cache_path=...) / jev-turn-label.py's "
        "--out already use (turn_id is the same 'session|ts' id) -- so the weekly "
        "run passes this file as turn_labels_path and relabels only what the daily "
        "job has not yet stored, instead of recomputing the whole corpus.",
    )
    dump.add_argument("--out", required=True)
    args = ap.parse_args()
    conn = init_db(Path(args.db) if args.db else None)
    now_iso = datetime.now(timezone.utc).isoformat()

    if args.cmd == "seed-dispatch":
        n_events, n_labels = seed_dispatch_labels(conn, args.dispatch_human, args.labels, args.model, now_iso)
        n_rates = recompute_daily_rates(conn)
        print(f"seeded {n_events} dispatch_events, {n_labels} dispatch_labels, {n_rates} daily_rates rows")
    elif args.cmd == "rates":
        n = recompute_daily_rates(conn)
        for row in conn.execute("SELECT day, kind, label, count, total, rate FROM daily_rates ORDER BY day, kind, label"):
            print(row)
        print(f"-- {n} rows")
    elif args.cmd == "show":
        for table in ("job_state", "dispatch_events", "dispatch_labels", "turns", "turn_labels", "daily_rates"):
            (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            print(f"{table}: {n}")
    elif args.cmd == "dump-turn-labels":
        n = 0
        with open(args.out, "w") as f:
            for turn_id, label in conn.execute("SELECT turn_id, label FROM turn_labels"):
                f.write(json.dumps({"id": turn_id, "label": label}) + "\n")
                n += 1
        print(f"wrote {n} turn labels to {args.out}")


if __name__ == "__main__":
    _cli()
