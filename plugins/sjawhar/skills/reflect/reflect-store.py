#!/usr/bin/env python3
"""SQLite store for the reflect skill's daily measurement (AGENTC daily-measure job).

Makes the weekly reflect run's two label corpora -- Sami's Dispatch events
(classify-sami-events.py's codebook) and session turns (experiments-readout.py's
correction/other/not_sami labels) -- daily and incremental: one row per labelled
event/turn, keyed by a stable id so a rerun over the same data is a no-op, plus a
`daily_rates` table recomputed from those rows each run so a reader (daily-readout.py,
the weekly reflect run) never recomputes the corpus itself.

One series, one labeller: Claude (`standalone-model.py`, `claude-fable-5-1` at `xhigh`
effort through the Hawk middleman gateway) labels both corpora. `dispatch_labels`/
`turn_labels` -- the tables `daily_rates` reads -- carry exactly one label per
event/turn, from whichever model is this run's standard (a `model` column on every
row, never implicit), so a model swap is visible in the series as a `model` change,
never a silent step in the rate. A DIFFERENT labeller's opinion on the same event/turn
(used for calibration, never for the series) goes in `dispatch_labels_alt`/
`turn_labels_alt`, keyed by `(event_id, model)`/`(turn_id, model)` so more than one
comparison model can coexist; `agreement`/`compute_turn_agreement`/`gold-precision-
recall` below read them, `daily_rates` never does. Jev (`jev-turn-label.py`, TypeSafe
System One) labels every turn into `turn_labels_alt` with its own confidence, so its
quality against the Claude series can be measured over time -- it never writes
`turn_labels`.

Default path: ~/.dotfiles/.claude/reflect-store.db (gitignored, alongside sessions.db).

Tables:
  job_state(key, value, updated_at)            -- resume watermarks, one row per stream
  dispatch_events(id, issue_key, project, seq, type, created_at, actor_id, asking_session)
  dispatch_labels(event_id, labels, labeled_at, model)     -- labels is a JSON array; the series
  dispatch_labels_alt(event_id, model, labels, labeled_at) -- a comparison labeller's opinion
  turns(id, session_id, host, project, ts, chars)          -- id = "<session_id>|<ts>"
  turn_labels(turn_id, label, source, model, labeled_at)   -- source: claude|propagated; the series
  turn_labels_alt(turn_id, model, label, confidence, labeled_at) -- a comparison labeller's
    opinion (Jev, always with confidence; other comparison models may have confidence=NULL)
  daily_rates(day, kind, label, count, total, rate)        -- kind: dispatch|turn

Every insert is `INSERT OR IGNORE` on the row's natural id, so re-running the daily job
over an overlapping window (the resumability safety margin) never re-labels or
double-counts a row already stored -- that is the idempotence the daily job relies on.
`dispatch_labels`/`turn_labels` are the one exception where overwriting is deliberate: a
(re)seed replaces them with the standing labeller's fresh opinion
(`replace_dispatch_label`/`replace_turn_label`), never silently mixing a different
labeller's old opinion into the series.
`daily_rates` is always a full recompute (DELETE + INSERT): cheap aggregate SQL over label
rows that already exist, never a model call, so redoing it in full on every run is simpler
and safer than tracking which days a late-arriving label touched.
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

CREATE TABLE IF NOT EXISTS dispatch_labels_alt (
    event_id INTEGER NOT NULL REFERENCES dispatch_events(id),
    model TEXT NOT NULL,
    labels TEXT NOT NULL,
    labeled_at TEXT NOT NULL,
    PRIMARY KEY (event_id, model)
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
    labeled_at TEXT NOT NULL,
    model TEXT
);

CREATE TABLE IF NOT EXISTS turn_labels_alt (
    turn_id TEXT NOT NULL REFERENCES turns(id),
    model TEXT NOT NULL,
    label TEXT NOT NULL,
    confidence REAL,
    labeled_at TEXT NOT NULL,
    PRIMARY KEY (turn_id, model)
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

CREATE TABLE IF NOT EXISTS refusals (
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    model TEXT NOT NULL,
    refused_at TEXT NOT NULL,
    PRIMARY KEY (kind, item_id, model)
);
"""

# Additive, idempotent (ignore "duplicate column" on a rerun) -- same pattern as
# index-sessions.py's MIGRATIONS for a table shipped before a column existed.
MIGRATIONS = [
    "ALTER TABLE turn_labels ADD COLUMN model TEXT",
]

# turn_labels_alt's model id for Jev's live comparison pass (jev-turn-label.py --out-all,
# every turn, with confidence) -- the comparison daily-readout.py reports.
JEV_MODEL = "typesafe-jev"


def get_db_path() -> Path:
    return Path.home() / ".dotfiles" / ".claude" / "reflect-store.db"


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise
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
    """The incremental daily path: a brand-new event the series has never labelled.
    INSERT OR IGNORE, so a rerun over the resumability overlap never touches an
    already-labelled row -- use `replace_dispatch_label` to deliberately relabel one."""
    conn.execute(
        "INSERT OR IGNORE INTO dispatch_labels (event_id, labels, labeled_at, model)"
        " VALUES (?, ?, ?, ?)",
        (event_id, json.dumps(labels), now_iso, model),
    )


def replace_dispatch_label(
    conn: sqlite3.Connection, event_id: int, labels: list[str], model: str, now_iso: str
) -> None:
    """Deliberately overwrite the series' opinion on an event -- a (re)seed relabelling
    with the standing labeller, never a daily-run no-op path."""
    conn.execute(
        "INSERT OR REPLACE INTO dispatch_labels (event_id, labels, labeled_at, model)"
        " VALUES (?, ?, ?, ?)",
        (event_id, json.dumps(labels), now_iso, model),
    )


def archive_alt_dispatch_label(
    conn: sqlite3.Connection, event_id: int, model: str, labels: list[str], labeled_at: str
) -> None:
    """A different labeller's opinion on an event the series already has one for --
    comparison only, read by `compute_agreement`/`gold_precision_recall`, never by
    `daily_rates`. INSERT OR IGNORE: the archived opinion is a historical fact (what
    that model said at seed time) and is never overwritten."""
    conn.execute(
        "INSERT OR IGNORE INTO dispatch_labels_alt (event_id, model, labels, labeled_at)"
        " VALUES (?, ?, ?, ?)",
        (event_id, model, json.dumps(labels), labeled_at),
    )


def load_seed_events(dispatch_human_path: str, labels_path: str) -> list[tuple[int, dict, list[str] | None]]:
    """Pure file I/O, no DB, no model call: classify-sami-events.py's `labels.jsonl`
    (keyed by a run-local `dn`, the 1-based index in created_at order) joined back to
    its event's real, stable Dispatch id via the (issue, created_at, kind) triple --
    unique across the 2,352-event 2026-10-04 corpus, verified zero-collision before this
    was written. Returns (dn, event_dict, kernel_labels) in `labels_path`'s order;
    kernel_labels is None for classify-sami-events.py's own "unlabeled" marker (never
    seed a null)."""
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

    out = []
    with open(labels_path) as f:
        for line in f:
            lb = json.loads(line)
            key = (lb["issue"], lb["created_at"], lb["kind"])
            e = key_to_event.get(key)
            if e is None:
                raise ValueError(
                    f"load_seed_events: no event in {dispatch_human_path} matches "
                    f"labels.jsonl dn={lb['dn']} (issue={lb['issue']!r}, "
                    f"created_at={lb['created_at']!r}, kind={lb['kind']!r}); "
                    "the two files must come from the same extraction run"
                )
            out.append((lb["dn"], e, lb["labels"]))
    return out


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


def turn_label_and_model(conn: sqlite3.Connection, turn_id: str) -> tuple[str, str | None]:
    row = conn.execute(
        "SELECT label, model FROM turn_labels WHERE turn_id = ?", (turn_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no turn_labels row for {turn_id!r}")
    return row


def upsert_turn_label(conn: sqlite3.Connection, turn_id: str, label: str, source: str,
                       now_iso: str, model: str | None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO turn_labels (turn_id, label, source, labeled_at, model)"
        " VALUES (?, ?, ?, ?, ?)",
        (turn_id, label, source, now_iso, model),
    )


def replace_turn_label(conn: sqlite3.Connection, turn_id: str, label: str, source: str,
                        now_iso: str, model: str | None) -> None:
    """Deliberately overwrite the series' opinion on a turn -- a (re)seed relabelling
    with the standing labeller, never a daily-run no-op path."""
    conn.execute(
        "INSERT OR REPLACE INTO turn_labels (turn_id, label, source, labeled_at, model)"
        " VALUES (?, ?, ?, ?, ?)",
        (turn_id, label, source, now_iso, model),
    )


def record_refusal(conn: sqlite3.Connection, kind: str, item_id: str, model: str,
                   now_iso: str) -> None:
    """`model` refused to label this item on every attempt (`kind` is "dispatch" or
    "turn"). The item counts as handled for that model, so it neither holds a stream's
    watermark nor gets resent every night; a different labeller tries it afresh."""
    conn.execute(
        "INSERT OR IGNORE INTO refusals (kind, item_id, model, refused_at) VALUES (?, ?, ?, ?)",
        (kind, str(item_id), model, now_iso),
    )


def is_refused(conn: sqlite3.Connection, kind: str, item_id: str, model: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM refusals WHERE kind = ? AND item_id = ? AND model = ?",
        (kind, str(item_id), model),
    ).fetchone() is not None


def refusal_counts(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """{(kind, model): count}: the items each labeller refused, which the series omits."""
    return {
        (kind, model): n
        for kind, model, n in conn.execute(
            "SELECT kind, model, COUNT(*) FROM refusals GROUP BY kind, model ORDER BY kind, model"
        )
    }


def archive_turn_label_alt(conn: sqlite3.Connection, turn_id: str, model: str, label: str,
                            confidence: float | None, labeled_at: str) -> None:
    """A different labeller's opinion on a turn (Jev, always with confidence) --
    comparison only, read by `compute_turn_agreement`, never by `daily_rates`. INSERT OR
    IGNORE: the archived opinion is a historical fact and is never overwritten."""
    conn.execute(
        "INSERT OR IGNORE INTO turn_labels_alt (turn_id, model, label, confidence, labeled_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (turn_id, model, label, confidence, labeled_at),
    )


# --- daily rates ----------------------------------------------------------------------

def recompute_daily_rates(conn: sqlite3.Connection) -> int:
    """Rebuild daily_rates from dispatch_labels and turn_labels -- the one-series
    tables, never dispatch_labels_alt."""
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


# --- calibration: agreement between the series and an archived alt labeller ----------

def compute_agreement(conn: sqlite3.Connection) -> dict[str, dict[str, dict]]:
    """Per-label simple agreement + Cohen's kappa between the current series
    (dispatch_labels) and every labeller archived in dispatch_labels_alt, over the
    event ids both have an opinion on. {alt_model: {label: {agreement, kappa, n}}}."""
    rows = conn.execute(
        "SELECT dl.labels, da.model, da.labels"
        " FROM dispatch_labels dl JOIN dispatch_labels_alt da ON da.event_id = dl.event_id"
    ).fetchall()
    by_alt_model: dict[str, list[tuple[set, set]]] = {}
    for cur_labels, alt_model, alt_labels in rows:
        by_alt_model.setdefault(alt_model, []).append(
            (set(json.loads(cur_labels)), set(json.loads(alt_labels)))
        )
    out: dict[str, dict[str, dict]] = {}
    for alt_model, pairs in by_alt_model.items():
        n = len(pairs)
        all_labels = sorted({label for cur, alt in pairs for label in cur | alt})
        per_label = {}
        for label in all_labels:
            cur_yes = [label in cur for cur, _alt in pairs]
            alt_yes = [label in alt for _cur, alt in pairs]
            po = sum(1 for x, y in zip(cur_yes, alt_yes) if x == y) / n
            p_cur = sum(cur_yes) / n
            p_alt = sum(alt_yes) / n
            pe = p_cur * p_alt + (1 - p_cur) * (1 - p_alt)
            kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
            per_label[label] = {"agreement": po, "kappa": kappa, "n": n}
        out[alt_model] = per_label
    return out


TURN_AGREEMENT_THRESHOLDS = (0.5, 0.7, 0.9)


def compute_turn_agreement(
    conn: sqlite3.Connection, thresholds: tuple[float, ...] = TURN_AGREEMENT_THRESHOLDS
) -> dict[str, dict]:
    """Agreement/kappa between the current turn series (turn_labels, Claude) and every
    labeller archived in turn_labels_alt (Jev), over the turn ids both have an opinion
    on -- overall (every turn with a comparison opinion) and at each confidence
    threshold (coverage = the share of those turns at or above it). Single-label
    (correction/other/not_sami), so kappa here is the standard multiclass Cohen's kappa,
    not the per-label binary kappa `compute_agreement` uses for multi-label Dispatch
    events.
    {alt_model: {"levels": {"overall"|"0.5"|"0.7"|"0.9": {coverage, agreement, kappa,
    n}}, "confusion": {alt_label: {cur_label: count}}}}."""
    rows = conn.execute(
        "SELECT tl.label, ta.model, ta.label, ta.confidence"
        " FROM turn_labels tl JOIN turn_labels_alt ta ON ta.turn_id = tl.turn_id"
    ).fetchall()
    by_alt_model: dict[str, list[tuple[str, str, float | None]]] = {}
    for cur_label, alt_model, alt_label, confidence in rows:
        by_alt_model.setdefault(alt_model, []).append((cur_label, alt_label, confidence))

    def score(pairs: list[tuple[str, str]]) -> dict:
        n = len(pairs)
        if n == 0:
            return {"agreement": None, "kappa": None, "n": 0}
        agreement = sum(1 for cur, alt in pairs if cur == alt) / n
        labels = sorted({x for pair in pairs for x in pair})
        p_cur = {l: sum(1 for cur, _alt in pairs if cur == l) / n for l in labels}
        p_alt = {l: sum(1 for _cur, alt in pairs if alt == l) / n for l in labels}
        pe = sum(p_cur[l] * p_alt[l] for l in labels)
        kappa = (agreement - pe) / (1 - pe) if pe < 1 else float("nan")
        return {"agreement": agreement, "kappa": kappa, "n": n}

    out: dict[str, dict] = {}
    for alt_model, triples in by_alt_model.items():
        n_total = len(triples)
        confusion: dict[str, dict[str, int]] = {}
        for cur, alt, _confidence in triples:
            confusion.setdefault(alt, {}).setdefault(cur, 0)
            confusion[alt][cur] += 1
        levels = {"overall": {**score([(cur, alt) for cur, alt, _c in triples]), "coverage": 1.0}}
        for t in thresholds:
            subset = [(cur, alt) for cur, alt, c in triples if c is not None and c >= t]
            levels[f"{t:.1f}"] = {
                **score(subset), "coverage": len(subset) / n_total if n_total else 0.0,
            }
        out[alt_model] = {"levels": levels, "confusion": confusion}
    return out


def rate_table_by_week(conn: sqlite3.Connection, table: str,
                        week_boundary: str = "2026-09-27") -> dict[str, dict[str, tuple[int, int]]]:
    """{label: {week: (count, total)}} from `dispatch_labels` or `dispatch_labels_alt`,
    restricted (for `dispatch_labels`) to the event ids `dispatch_labels_alt` also has an
    opinion on, so a kernel-vs-current comparison never includes a post-seed event only
    the current series ever saw."""
    if table == "dispatch_labels_alt":
        rows = conn.execute(
            "SELECT de.created_at, da.labels"
            " FROM dispatch_labels_alt da JOIN dispatch_events de ON de.id = da.event_id"
        ).fetchall()
    elif table == "dispatch_labels":
        rows = conn.execute(
            "SELECT de.created_at, dl.labels"
            " FROM dispatch_labels dl JOIN dispatch_events de ON de.id = dl.event_id"
            " WHERE dl.event_id IN (SELECT event_id FROM dispatch_labels_alt)"
        ).fetchall()
    else:
        raise ValueError(f"rate_table_by_week: unknown table {table!r}")
    totals = {"week1": 0, "week2": 0}
    counts: dict[tuple[str, str], int] = {}
    for created_at, labels_json in rows:
        week = "week1" if created_at < week_boundary else "week2"
        totals[week] += 1
        for label in json.loads(labels_json):
            counts[(week, label)] = counts.get((week, label), 0) + 1
    out: dict[str, dict[str, tuple[int, int]]] = {}
    for (week, label), count in counts.items():
        out.setdefault(label, {})[week] = (count, totals[week])
    return out


def gold_precision_recall(conn: sqlite3.Connection, gold_path: str,
                           dn_to_event_id: dict[int, int]) -> dict[str, dict[str, dict]]:
    """Precision/recall of the current series and every archived alt labeller against a
    `{dn: {"labels": [...]}}` gold file (classify-sami-events.py's DN numbering),
    resolved to event ids via `dn_to_event_id` (from `load_seed_events`).
    {"current": {label: {precision, recall, tp, fp, fn}}, alt_model: {...}, ...}."""
    gold = json.load(open(gold_path))
    cur = {eid: set(json.loads(l)) for eid, l in conn.execute("SELECT event_id, labels FROM dispatch_labels")}
    alt_by_model: dict[str, dict[int, set]] = {}
    for eid, model, labels_json in conn.execute("SELECT event_id, model, labels FROM dispatch_labels_alt"):
        alt_by_model.setdefault(model, {})[eid] = set(json.loads(labels_json))

    classes = sorted({c for v in gold.values() for c in v["labels"]})

    def score(table: dict[int, set]) -> dict[str, dict]:
        per_label = {}
        for cls in classes:
            tp = fp = fn = 0
            for dn_str, v in gold.items():
                eid = dn_to_event_id.get(int(dn_str))
                if eid is None:
                    continue
                pred = table.get(eid, set())
                gt, pd = cls in v["labels"], cls in pred
                if pd and gt:
                    tp += 1
                elif pd and not gt:
                    fp += 1
                elif gt and not pd:
                    fn += 1
            per_label[cls] = {
                "precision": tp / (tp + fp) if (tp + fp) else None,
                "recall": tp / (tp + fn) if (tp + fn) else None,
                "tp": tp, "fp": fp, "fn": fn,
            }
        return per_label

    out = {"current": score(cur)}
    for model, table in alt_by_model.items():
        out[model] = score(table)
    return out


def _cli() -> None:
    import argparse
    from datetime import datetime, timezone

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

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

    agree = sub.add_parser(
        "agreement",
        help="Per-class agreement/kappa between the series and an archived alt "
        "labeller, each labeller's own per-week rate over the same (seed) "
        "population, and (with --gold) precision/recall against a gold file -- the "
        "monthly calibration sample SKILL.md step 2 asks for.",
    )
    agree.add_argument("--gold", default=None)
    agree.add_argument("--dispatch-human", default=None, help="required with --gold")
    agree.add_argument("--labels", default=None, help="required with --gold")

    args = ap.parse_args()
    conn = init_db(Path(args.db) if args.db else None)
    now_iso = datetime.now(timezone.utc).isoformat()

    if args.cmd == "rates":
        n = recompute_daily_rates(conn)
        for row in conn.execute("SELECT day, kind, label, count, total, rate FROM daily_rates ORDER BY day, kind, label"):
            print(row)
        print(f"-- {n} rows")
    elif args.cmd == "show":
        for table in ("job_state", "dispatch_events", "dispatch_labels", "dispatch_labels_alt",
                      "turns", "turn_labels", "turn_labels_alt", "daily_rates"):
            (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            print(f"{table}: {n}")
    elif args.cmd == "dump-turn-labels":
        n = 0
        with open(args.out, "w") as f:
            for turn_id, label in conn.execute("SELECT turn_id, label FROM turn_labels"):
                f.write(json.dumps({"id": turn_id, "label": label}) + "\n")
                n += 1
        print(f"wrote {n} turn labels to {args.out}")
    elif args.cmd == "agreement":
        for alt_model, per_label in compute_agreement(conn).items():
            print(f"=== current series vs {alt_model} ===")
            for label in sorted(per_label):
                d = per_label[label]
                print(f"  {label}: agreement={d['agreement']:.3f} kappa={d['kappa']:.3f} n={d['n']}")

            rates_cur = rate_table_by_week(conn, "dispatch_labels")
            rates_alt = rate_table_by_week(conn, "dispatch_labels_alt")
            print(f"\n  per-class rate by week (current vs {alt_model}):")
            for label in sorted(set(rates_cur) | set(rates_alt)):
                for week in ("week1", "week2"):
                    c = rates_cur.get(label, {}).get(week)
                    a = rates_alt.get(label, {}).get(week)
                    c_rate = f"{c[0]}/{c[1]}={c[0]/c[1]:.3f}" if c else "-"
                    a_rate = f"{a[0]}/{a[1]}={a[0]/a[1]:.3f}" if a else "-"
                    print(f"    {label} {week}: current={c_rate}  {alt_model}={a_rate}")

        turn_agreement = compute_turn_agreement(conn)
        if not turn_agreement:
            print("\n(no turn_labels_alt rows to compare yet)")
        for alt_model, result in turn_agreement.items():
            print(f"\n=== turn series (Claude) vs {alt_model} ===")
            for level, d in result["levels"].items():
                if d["n"] == 0:
                    print(f"  {level}: n=0 (no turns at this threshold)")
                    continue
                print(f"  {level}: coverage={d['coverage']:.3f} agreement={d['agreement']:.3f} "
                      f"kappa={d['kappa']:.3f} n={d['n']}")
            print(f"  confusion ({alt_model} row vs current-series column):")
            cur_labels = sorted({cl for row in result["confusion"].values() for cl in row})
            for alt_label in sorted(result["confusion"]):
                row = result["confusion"][alt_label]
                cells = ", ".join(f"{cl}={row.get(cl, 0)}" for cl in cur_labels)
                print(f"    {alt_label}: {cells}")

        if args.gold:
            if not (args.dispatch_human and args.labels):
                raise SystemExit("agreement --gold needs --dispatch-human and --labels for the dn->event mapping")
            seed = load_seed_events(args.dispatch_human, args.labels)
            dn_to_event_id = {dn: e["id"] for dn, e, _kl in seed}
            gpr = gold_precision_recall(conn, args.gold, dn_to_event_id)
            print("\n=== gold precision/recall ===")
            for who, per_label in gpr.items():
                print(f"  -- {who} --")
                for cls in sorted(per_label):
                    d = per_label[cls]
                    p = f"{d['precision']:.3f}" if d["precision"] is not None else "n/a"
                    r = f"{d['recall']:.3f}" if d["recall"] is not None else "n/a"
                    print(f"    {cls}: precision={p} recall={r} (tp={d['tp']} fp={d['fp']} fn={d['fn']})")


if __name__ == "__main__":
    _cli()
