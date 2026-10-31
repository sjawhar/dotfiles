"""`chief-dashboard check scoreboard.json`: what a lead runs before uploading a scoreboard.

It prints the file's format errors (against ~/src/scrum/workstreams/scoreboard-schema.md, versions 1 and
1.1, plus `headline`), then exactly what the dashboard will show for the file: the stream's glance row (headline,
headline number, status of every main number) and every loop flag. The flags come from the same code the page runs,
so the page cannot disagree with this output.

Exit status: 0 when the file has no format errors (flags are warnings for the lead to act on), 1 otherwise."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import model
from .model import fmt_value, parse_time
from .plan import due_day, issue_key

ROLES = ("main", "fast", "guard")
VERDICTS = ("running", "kept", "reverted", "inconclusive", "provisional")
# Known kinds of wait (format 1.2 added "lead"). Any other kind is a warning, never an error, so a lead is not
# blocked by this checker for a new kind of wait.
WAITS = ("person", "lead", "merge", "compute", "other")


def _num(x) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def format_errors(b) -> list[str]:
    """Every way the file departs from the format. Empty when it is well formed."""
    if not isinstance(b, dict):
        return ["the file is not a JSON object"]
    err: list[str] = []
    if not str(b.get("schema", "")).startswith("stream-scoreboard/"):
        err.append('schema: must be "stream-scoreboard/1" or "stream-scoreboard/1.1"')
    for k in ("stream", "name", "issue"):
        if not isinstance(b.get(k), str) or not b[k].strip():
            err.append(f"{k}: required, a non-empty string")
    lead = b.get("lead")
    if not isinstance(lead, dict) or not lead.get("title") or not lead.get("session"):
        err.append("lead: required, with title and session (the session a note from Sami reaches)")
    if parse_time(b.get("updated_at")) is None:
        err.append("updated_at: required, a UTC time such as 2026-10-24T05:00:00Z")
    h = b.get("headline")
    if h is not None and (not isinstance(h, str) or len(h) > model.HEADLINE_MAX):
        err.append(f"headline: one sentence of at most {model.HEADLINE_MAX} characters")
    nums = b.get("numbers")
    if not isinstance(nums, list) or not nums:
        err.append("numbers: required, a non-empty list")
        nums = []
    ids: set[str] = set()
    mains: set[str] = set()
    for i, n in enumerate(nums):
        where = f"numbers[{i}]" + (f" ({n.get('id')})" if isinstance(n, dict) and n.get("id") else "")
        if not isinstance(n, dict):
            err.append(f"{where}: must be an object")
            continue
        if not n.get("id"):
            err.append(f"{where}: id required")
        elif n["id"] in ids:
            err.append(f"{where}: id {n['id']!r} is used twice")
        ids.add(str(n.get("id")))
        if not n.get("name"):
            err.append(f"{where}: name required")
        if n.get("role") not in ROLES:
            err.append(f"{where}: role must be one of {', '.join(ROLES)}")
        if n.get("role") == "main":
            mains.add(str(n.get("id")))
        if n.get("better") not in ("lower", "higher"):
            err.append(f"{where}: better must be lower or higher")
        if "unit" not in n:
            err.append(f"{where}: unit required")
        t = n.get("target")
        if t is not None and (not isinstance(t, dict) or not _num(t.get("value")) or model.deadline(str(t.get("by"))) is None):
            err.append(f"{where}: target must be {{value: number, by: date, why: text}}")
        vals = n.get("values")
        if not isinstance(vals, list):
            err.append(f"{where}: values required, a list (empty when not measured, with `missing` saying why)")
            vals = []
        for j, v in enumerate(vals):
            bound_only = isinstance(v, dict) and v.get("value") is None and _num(v.get("lower_bound"))
            if not isinstance(v, dict) or parse_time(v.get("at")) is None or not (_num(v.get("value")) or bound_only):
                err.append(
                    f"{where}: values[{j}] needs at (UTC time) and value (a number), or value null with lower_bound "
                    "(a number)"
                )
            elif not v.get("source"):
                err.append(f"{where}: values[{j}] needs a source (the command, file or link that gives it)")
        if not vals and not n.get("missing"):
            err.append(f"{where}: no values and no `missing` text saying what is missing and who builds it")
        status = n.get("validation_status")
        if status is not None and status not in ("unvalidated", "holding", "failed"):
            err.append(f"{where}: validation_status must be unvalidated, holding or failed")
    for i, n in enumerate(nums):
        if isinstance(n, dict) and n.get("guards") is not None and str(n["guards"]) not in mains:
            err.append(f"numbers[{i}] ({n.get('id')}): guards names {n['guards']!r}, which is not a main number's id")
    exps = b.get("experiments", [])
    if not isinstance(exps, list):
        err.append("experiments: must be a list")
        exps = []
    for i, x in enumerate(exps):
        where = f"experiments[{i}]" + (f" ({x.get('id')})" if isinstance(x, dict) and x.get("id") else "")
        if not isinstance(x, dict):
            err.append(f"{where}: must be an object")
            continue
        for k in ("id", "change", "hypothesis"):
            if not x.get(k):
                err.append(f"{where}: {k} required")
        if x.get("number") not in ids:
            err.append(f"{where}: number {x.get('number')!r} is not the id of a number in this file")
        if x.get("verdict") not in VERDICTS:
            err.append(f"{where}: verdict must be one of {', '.join(VERDICTS)}")
        if parse_time(x.get("started")) is None:
            err.append(f"{where}: started required, a UTC time")
        if x.get("ended") is not None and parse_time(x.get("ended")) is None:
            err.append(f"{where}: ended must be a UTC time or null")
        if x.get("verdict") == "running" and x.get("ended"):
            err.append(f"{where}: it has ended, so its verdict cannot be running")
    bott = b.get("bottleneck")
    if bott is not None:
        if not isinstance(bott, dict):
            err.append("bottleneck: must be {text, evidence, since}")
        elif bott.get("share") is not None and not (_num(bott["share"]) and 0 <= bott["share"] <= 1):
            err.append("bottleneck.share: a fraction from 0 to 1")
    for i, w in enumerate(b.get("waits") or []):
        if not isinstance(w, dict) or not isinstance(w.get("on"), str) or not w["on"] or not _num(w.get("count")):
            err.append(f"waits[{i}]: needs on (one of {', '.join(WAITS)}, or a new kind) and count")
    err += plan_errors(b.get("today"), "today")
    week = b.get("week")
    err += plan_errors(week, "week")
    if isinstance(week, dict):
        start = due_day(week.get("start"))
        if start is None or start.weekday() != 0 or str(week.get("start")) != start.isoformat():
            err.append(f"week.start: {week.get('start')!r} must be the Monday of the Pacific week, as YYYY-MM-DD")
        for i, it in enumerate(week.get("items") or []):
            if isinstance(it, dict) and it.get("due") is not None and due_day(it["due"]) is None:
                err.append(f"week.items[{i}].due: {it['due']!r} must be a Pacific day, as YYYY-MM-DD")
    return err


def plan_errors(plan, where: str) -> list[str]:
    """A plan's items (`today`): each an object with `what`; `issue`, when given, a Dispatch key such as OSSV-150."""
    if plan is None:
        return []
    if not isinstance(plan, dict) or not isinstance(plan.get("items", []), list):
        return [f"{where}: must be {{date, items: [...]}}"]
    err = []
    for i, it in enumerate(plan.get("items") or []):
        if not isinstance(it, dict) or not str(it.get("what") or "").strip():
            err.append(f"{where}.items[{i}]: must be an object with what")
        elif it.get("issue") is not None and issue_key(it["issue"]) is None:
            err.append(f"{where}.items[{i}].issue: {it['issue']!r} is not a Dispatch issue key such as OSSV-150")
    return err


def plan_warnings(plan, where: str) -> list[str]:
    """Items that name no Dispatch issue: the page then looks for the issue their pull request delivers."""
    if not isinstance(plan, dict):
        return []
    return [f"Warning: {where}.items[{i}] ({str(it.get('what'))[:50]!r}) names no Dispatch issue; the page shows "
            "the issue its pull request delivers, else 'No Dispatch issue'."
            for i, it in enumerate(plan.get("items") or []) if isinstance(it, dict) and not it.get("issue")]


def number_line(n: model.Number, now: datetime) -> str:
    if n.latest is None:
        return f"{n.status or n.role}: {n.short}: not measured ({n.missing or 'no missing text'})"
    t = n.target or {}
    goal = ""
    if _num(t.get("value")) and n.target_at:
        goal = f" → {'≤' if n.better == 'lower' else '≥'} {fmt_value(t['value'], n.unit)} by {n.target_at:%b %-d}"
    return f"{n.status or n.role}: {n.short}: {fmt_value(n.latest['value'], n.unit)}{goal}"


def report(b: dict, now: datetime) -> tuple[list[str], list[str]]:
    """(format errors, lines describing what the page shows)."""
    errors = format_errors(b)
    if not isinstance(b, dict):
        return errors, []
    issue = str(b.get("issue") or "THIS-ISSUE")
    snap = {
        "stream_issues": [{"key": issue, "title": b.get("name"), "project": issue.split("-")[0], "claim": None}],
        "scoreboards": {issue: {"versions": [{"number": 1, "created_at": b.get("updated_at"), "data": b}]}},
    }
    st = model.build_streams(snap, now)[0]
    lines = [f"Glance row for {st.name} ({issue}), as of {now:%Y-%m-%d %H:%MZ}:"]
    lines.append(f"  headline: {st.headline or '(none)'}")
    if st.headline_number:
        lines.append(f"  headline number (your first main number): {number_line(st.headline_number, now)}")
    for n in st.mains[1:]:
        lines.append(f"  main number: {number_line(n, now)}")
    b2 = b.get("bottleneck") if isinstance(b.get("bottleneck"), dict) else {}
    lines.append(f"  bottleneck: {b2.get('text') or '(none)'}")
    if st.flags:
        lines.append(f"Loop flags the page shows ({len(st.flags)}):")
        lines += [f"  [{'problem' if f.level == 'bad' else 'warning'}] {f.text}" for f in st.flags]
    else:
        lines.append("Loop flags the page shows: none (the row reads 'loop working').")
    for i, w in enumerate(b.get("waits") or []):
        if isinstance(w, dict) and isinstance(w.get("on"), str) and w["on"] and w["on"] not in WAITS:
            lines.append(
                f"Warning: waits[{i}] is on {w['on']!r}, a kind the page does not know; it shows the word as written "
                f"(known: {', '.join(WAITS)})."
            )
    lines += plan_warnings(b.get("today"), "today") + plan_warnings(b.get("week"), "week")
    if b.get("week") is None:
        lines.append("Warning: no `week` list; the page shows 'No plan' under This week's plan.")
    return errors, lines


def main(path: str) -> int:
    try:
        b = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        print(f"{path}: cannot read as JSON: {e}")
        return 1
    errors, lines = report(b, datetime.now(UTC))
    if errors:
        print(f"{path}: {len(errors)} format error{'s' if len(errors) != 1 else ''}:")
        print("\n".join(f"  {x}" for x in errors))
    else:
        print(f"{path}: format OK")
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
