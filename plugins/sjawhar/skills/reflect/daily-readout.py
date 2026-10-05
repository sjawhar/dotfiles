#!/usr/bin/env python3
"""Print every failure class's daily rate series from reflect-store.py's store, next to
the latest weekly report's "Landed this run" table (section 7), so a fix's effect shows
up by date instead of waiting for the next weekly sitting. Under the turn series it
also prints Jev's agreement with the Claude series (overall and by Jev's confidence
threshold), from `turn_labels_alt` -- how far Jev could be trusted, measured daily.

This does not auto-match a landed change to the label(s) it targets -- that free-text
"targets" column is a human judgment call, same as the report's own "verdict" column
(improved/unchanged/regressed) is. It juxtaposes the two tables so a reader lines up
dates by eye, the way SKILL.md step 2 ("before/after the prior report's rates") already
asked of the weekly run; this script is what that step now reads instead of recomputing.

Usage:
    python3 daily-readout.py [--db PATH] [--days N] [--reports-dir PATH]
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("reflect_store", HERE / "reflect-store.py")
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)


def get_db_path() -> Path:
    return Path.home() / ".dotfiles" / ".claude" / "reflect-store.db"


def get_reports_dir() -> Path:
    return Path.home() / ".dotfiles" / ".claude" / "session-analysis"


def latest_report(reports_dir: Path) -> Path | None:
    """The newest `YYYY-MM-DD.md` report (not a `-work`/`-drafts`/`-problem-catalog`
    sibling file or directory), by filename date -- same convention SKILL.md uses to
    find "the newest prior report" as this run's baseline."""
    candidates = sorted(
        p for p in reports_dir.glob("????-??-??.md")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", p.name)
    )
    return candidates[-1] if candidates else None


def parse_landed_table(report_path: Path) -> list[dict]:
    """Section 7's "| change | where | when (UTC) | targets |" rows."""
    text = report_path.read_text()
    m = re.search(r"^## 7\. Landed this run\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return []
    rows = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= {"-", " "}:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0].lower() == "change":
            continue
        rows.append({"change": cells[0], "where": cells[1], "when": cells[2], "targets": cells[3]})
    return rows


def rate_series(conn: sqlite3.Connection, kind: str, since_day: str | None) -> dict[str, list[tuple[str, int, int, float]]]:
    q = "SELECT day, label, count, total, rate FROM daily_rates WHERE kind = ?"
    params: list = [kind]
    if since_day:
        q += " AND day >= ?"
        params.append(since_day)
    q += " ORDER BY label, day"
    series: dict[str, list[tuple[str, int, int, float]]] = {}
    for day, label, count, total, rate in conn.execute(q, params):
        series.setdefault(label, []).append((day, count, total, rate))
    return series


# Classes whose daily rate the hand-checked sample cannot vouch for, with the measured
# numbers (`reflect-store.py agreement --gold`, 462 events). Refresh at each monthly
# calibration sample (SKILL.md step 2), replacing a class's text with the new finding.
CALIBRATION_CAVEATS: dict[str, str] = {
    "wrong_claim": (
        "precision 0.15 / recall 0.56 against the 462-event hand-checked sample (2026-10-05, "
        "claude-fable-5-1:xhigh), and 0.18 / 0.70 for the earlier no-thinking labels: that "
        "sample was not tagged for this class systematically, so it cannot say whether these "
        "counts are right. Read the trend, not the level."
    ),
}


def render(dispatch_series: dict, turn_series: dict, turn_agreement: dict,
           refusals: dict, landed: list[dict], report_name: str) -> str:
    lines = ["# Daily failure-class rates\n"]
    lines.append("## Dispatch events (classify-sami-events.py codebook)\n")
    for label in sorted(dispatch_series):
        lines.append(f"### {label}")
        if label in CALIBRATION_CAVEATS:
            lines.append(f"  CAVEAT: {CALIBRATION_CAVEATS[label]}")
        for day, count, total, rate in dispatch_series[label]:
            lines.append(f"  {day}  {count:>4}/{total:<4}  {rate:.3f}")
        lines.append("")
    lines.append("## Session turns (correction / other / not_sami)\n")
    for label in sorted(turn_series):
        lines.append(f"### {label}")
        for day, count, total, rate in turn_series[label]:
            lines.append(f"  {day}  {count:>4}/{total:<4}  {rate:.3f}")
        lines.append("")
    for alt_model, result in turn_agreement.items():
        lines.append(f"### {alt_model} vs the series (turn_labels_alt, comparison only)")
        lines.append("  level    coverage  agreement  kappa    n")
        for level, d in result["levels"].items():
            if d["n"] == 0:
                lines.append(f"  {level:<8} {d['coverage']:>8.3f}  {'-':>9}  {'-':>6}  {0:>4}")
                continue
            lines.append(f"  {level:<8} {d['coverage']:>8.3f}  {d['agreement']:>9.3f}  "
                         f"{d['kappa']:>6.3f}  {d['n']:>4}")
        lines.append("")
    lines.append("## Refused by the labeller (left out of the series)\n")
    if not refusals:
        lines.append("(none)")
    for (kind, model), n in refusals.items():
        lines.append(f"  {kind:<9} {model}: {n}")
    lines.append("")
    lines.append(f"## Landed this run ({report_name}, section 7)\n")
    if not landed:
        lines.append("(no landed-changes table found)")
    else:
        lines.append("| change | where | when (UTC) | targets |")
        lines.append("|---|---|---|---|")
        for row in landed:
            lines.append(f"| {row['change']} | {row['where']} | {row['when']} | {row['targets']} |")
    lines.append("")
    lines.append(
        "Line up a row's `when` against the rate series above by date; this script "
        "does not decide which label a change targets -- read `targets` and the day's "
        "rate yourself, the same judgment call the weekly report's own \"verdict\" "
        "column makes."
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    ap.add_argument("--days", type=int, default=None, help="Only show the last N days")
    ap.add_argument("--reports-dir", default=None)
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else get_db_path()
    if not db_path.exists():
        raise SystemExit(f"Error: {db_path} does not exist; run daily-measure.py seed first")
    conn = sqlite3.connect(str(db_path))

    since_day = None
    if args.days:
        (max_day,) = conn.execute("SELECT MAX(day) FROM daily_rates").fetchone()
        if max_day:
            from datetime import date, timedelta
            since_day = (date.fromisoformat(max_day) - timedelta(days=args.days)).isoformat()

    dispatch_series = rate_series(conn, "dispatch", since_day)
    turn_series = rate_series(conn, "turn", since_day)
    turn_agreement = {
        model: result for model, result in store.compute_turn_agreement(conn).items()
        if model == store.JEV_MODEL
    }

    reports_dir = Path(args.reports_dir) if args.reports_dir else get_reports_dir()
    report_path = latest_report(reports_dir)
    landed = parse_landed_table(report_path) if report_path else []
    report_name = report_path.name if report_path else "(none found)"

    print(render(dispatch_series, turn_series, turn_agreement, store.refusal_counts(conn),
                 landed, report_name))


if __name__ == "__main__":
    main()
