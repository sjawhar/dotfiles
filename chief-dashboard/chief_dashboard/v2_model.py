"""The page model of /v2 (dispatch://INFRA-224 v6): per stream, every main number with its Pace label and its guards,
today's plan with each pull request's state on GitHub, the last 7 days' merged out of planned, and the open Dispatch
asks that wait on Sami. Pure: a snapshot (collect.py) and the time now in, a page out."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import model
from .model import Number, Stream, parse_time
from .pace import BEHIND, CANT_TELL, ON_PACE, PACIFIC, Pace, Reading, every_hours, guard, line_at, pace, readings
from .plan import DayPlan, Item, WeekPlan, day_plan, day_record, fix_time, pacific_day, tally, week_plan
from .plan_issues import IssueRef, resolve

MERGED, OPEN, FAILING = "merged", "open", "failing"


@dataclass
class GuardRow:
    num: Number
    label: Pace
    readings: list[Reading]


@dataclass
class KpiRow:
    num: Number
    label: Pace
    readings: list[Reading]
    guards: list[GuardRow] = field(default_factory=list)
    expected: float | None = None  # where the line says the number should be at its newest reading
    gap: float | None = None  # newest reading minus `expected`, signed so that more is better: < 0 is behind


def line_gap(rs: list[Reading], p: Pace, better: str) -> tuple[float | None, float | None]:
    """(expected, gap) at the newest reading, from the line Pace judges against; (None, None) when there is no line
    (no target, no reading) or the target is met."""
    if not rs or p.target is None or p.end is None or p.met:
        return None, None
    t_new, v_new = rs[-1]
    expected = line_at(rs[0], p.target, p.end, t_new)
    diff = float(v_new) - expected
    return expected, (-diff if better == "lower" else diff)


@dataclass
class PlanLine:
    item: Item
    kind: str  # MERGED, OPEN or FAILING: the mark's fill
    state: str  # GitHub's state in words
    url: str | None
    author: str | None
    merged_today: bool
    issue: IssueRef | None = None  # the item's Dispatch issue (plan_issues.py), None when it has none


@dataclass
class StreamView:
    st: Stream
    kpis: list[KpiRow]
    loose_guards: list[GuardRow]  # guards whose `guards` names no main number of the stream
    plan: DayPlan
    lines: list[PlanLine]
    merged: int
    planned: int
    history: list[tuple[date, tuple[int, int] | None]]  # yesterday first
    asks: int
    example: bool  # the plan shown is example items (?example=1)
    week: WeekPlan | None = None
    week_lines: list[PlanLine] = field(default_factory=list)

    @property
    def board_link(self) -> str | None:
        return f"/issues/{self.st.issue}/artifacts/scoreboard-json" if self.st.issue else None


@dataclass
class PageV2:
    now: datetime
    read_at: datetime | None
    prs_read_at: datetime | None
    errors: list[str]
    streams: list[StreamView]
    inbox: str
    web: str
    asks: int
    example: bool
    kpis: dict[str, int]  # On pace / Behind / Can't tell counts over every main number
    merged: int
    planned: int
    behind: list[tuple[StreamView, KpiRow]] = field(default_factory=list)  # every Behind main number, in page order
    agents: dict = field(default_factory=dict)  # live Dispatch sessions by id, for the running dots


def clock(t: datetime | None, now: datetime) -> str:
    """Pacific time: "14:05", or "Oct 23 14:05" on another day."""
    if t is None:
        return ""
    p = t.astimezone(PACIFIC)
    return p.strftime("%H:%M") if p.date() == now.astimezone(PACIFIC).date() else p.strftime("%b %-d %H:%M")


def pr_line(item: Item, states: dict[str, dict], day: date, now: datetime) -> PlanLine:
    if not item.pr:
        return PlanLine(item, OPEN, "No pull request yet", None, item.owner, False)
    s = states.get(item.pr)
    if s is None:
        return PlanLine(item, OPEN, "State not read yet", None, item.owner, False)
    if s.get("error"):
        return PlanLine(item, OPEN, f"State unknown: {s['error']}", None, item.owner, False)
    url, author = s.get("url"), item.owner or s.get("author")
    merged_at = parse_time(s.get("merged_at"))
    if s.get("state") == "MERGED" and merged_at:
        start = datetime.combine(day, datetime.min.time(), tzinfo=PACIFIC)
        inside = start <= merged_at < start + timedelta(days=1)
        return PlanLine(item, MERGED, f"Merged {clock(merged_at, now)}", url, author, inside)
    if s.get("state") == "CLOSED":
        return PlanLine(item, OPEN, "Closed without merging", url, author, False)
    if s.get("draft"):
        return PlanLine(item, OPEN, "Draft", url, author, False)
    if s.get("ci") in ("FAILURE", "ERROR"):
        since = parse_time(s.get("ci_failing_since"))
        return PlanLine(item, FAILING, f"CI failing since {clock(since, now)}" if since else "CI failing", url, author, False)
    if s.get("review") == "CHANGES_REQUESTED":
        return PlanLine(item, OPEN, "Changes requested", url, author, False)
    if s.get("review_requested") or s.get("review") == "REVIEW_REQUIRED":
        return PlanLine(item, OPEN, "Review requested", url, author, False)
    if s.get("ci") == "SUCCESS":
        return PlanLine(item, OPEN, "CI passing, waiting to merge", url, author, False)
    if s.get("ci") in ("PENDING", "EXPECTED"):
        return PlanLine(item, OPEN, "CI running", url, author, False)
    return PlanLine(item, OPEN, "Open", url, author, False)


def example_plan(d: date, now: datetime) -> tuple[list[Item], dict[str, dict]]:
    """Four invented items, one per kind of state, for ?example=1 on a stream with no plan."""
    at = fix_time(d)
    items = [
        Item("Lean ELF loader checks the program header table", "theorem-labs/example#101", example=True),
        Item("Grader keeps the compiled tree the kernel check ran over", "theorem-labs/example#102", example=True),
        Item("Nightly benchmark compares verdicts across both arms", "theorem-labs/example#103", example=True),
        Item("Scoreboard check reports a stale reading in the page's words", "theorem-labs/example#104", example=True),
        Item("Delete the unused grader settings", None, example=True),
    ]
    iso = lambda t: t.isoformat()  # noqa: E731
    states = {
        "theorem-labs/example#101": {"state": "MERGED", "merged_at": iso(min(now, at + timedelta(hours=1, minutes=42)))},
        "theorem-labs/example#102": {"state": "OPEN", "ci": "SUCCESS"},
        "theorem-labs/example#103": {"state": "OPEN", "ci": "FAILURE", "ci_failing_since": iso(min(now, at + timedelta(hours=2, minutes=5)))},
        "theorem-labs/example#104": {"state": "OPEN", "ci": "SUCCESS", "review_requested": True},
    }
    for s in states.values():
        s["author"] = "example"
    return items, states


def stream_view(st: Stream, versions: list[dict], states: dict[str, dict], asks: int, now: datetime, example: bool,
                issues: dict | None = None, agents: dict | None = None, web: str = "") -> StreamView:
    """`issues` is the collector's `plan_issues` and `agents` its live sessions: each plan line's Dispatch issue and
    owner come from them (plan_issues.resolve)."""
    today = pacific_day(now)
    kpis: list[KpiRow] = []
    by_id: dict[str, KpiRow] = {}
    for n in st.mains:
        t = n.target or {}
        rs = readings(n.values)
        p = pace(rs, t.get("value"), t.get("by"), n.better, every_hours(n.raw), now)
        expected, gap = line_gap(rs, p, n.better)
        row = KpiRow(n, p, rs, expected=expected, gap=gap)
        kpis.append(row)
        by_id[n.id] = row
    loose: list[GuardRow] = []
    for n in st.others:
        if n.role != "guard":
            continue
        rs = readings(n.values)
        g = GuardRow(n, guard(rs, n.better), rs)
        owner = by_id.get(str(n.raw.get("guards") or ""))
        (owner.guards if owner else loose).append(g)

    plan = day_plan(versions, today, now)
    shown_example = False
    if example and not plan.fixed:
        items, fake = example_plan(today, now)
        plan = DayPlan(plan.day, plan.before_fix, True, items + plan.items)
        states = {**states, **fake}
        shown_example = True
    lines = [pr_line(i, states, today, now) for i in plan.items]
    week = week_plan(versions, now)
    week_lines = [pr_line(i, states, today, now) for i in week.items]
    for ln in lines + week_lines:
        ln.issue = resolve(ln.item, states, issues or {}, agents or {}, web)
    merged, planned = tally(plan, states)
    history = [(today - timedelta(days=k), day_record(versions, today - timedelta(days=k), states)) for k in range(1, 8)]
    return StreamView(st, kpis, loose, plan, lines, merged, planned, history, asks, shown_example, week, week_lines)


def build(snap: dict, now: datetime, example: bool = False) -> PageV2:
    streams = model.build_streams(snap, now)
    streams.sort(key=lambda s: (s.rank, s.name))  # the rank order of streams.json
    asks = model.asks_waiting_on_sami(snap, streams)
    per: dict[str, int] = {}
    for a in asks:
        if a.stream:
            per[a.stream.slug] = per.get(a.stream.slug, 0) + 1
    states = (snap.get("prs") or {}).get("states") or {}
    web = str(snap.get("dispatch_web") or "").rstrip("/")
    views = [
        stream_view(
            st,
            ((snap.get("scoreboards") or {}).get(st.issue) or {}).get("versions") or [],
            states,
            per.get(st.slug, 0),
            now,
            example,
            snap.get("plan_issues") or {},
            snap.get("agents") or {},
            web,
        )
        for st in streams
    ]
    counts = {ON_PACE: 0, BEHIND: 0, CANT_TELL: 0}
    behind: list[tuple[StreamView, KpiRow]] = []
    for v in views:
        for k in v.kpis:
            counts[k.label.label] += 1
            if k.label.label == BEHIND:
                behind.append((v, k))
    return PageV2(
        now=now,
        read_at=parse_time(snap.get("read_at")),
        prs_read_at=parse_time((snap.get("prs") or {}).get("read_at")),
        errors=list(snap.get("errors") or []),
        streams=views,
        inbox=model.inbox_url(snap),
        web=web,
        asks=len(asks),
        example=any(v.example for v in views),
        kpis=counts,
        merged=sum(v.merged for v in views),
        planned=sum(v.planned for v in views),
        behind=behind,
        agents=snap.get("agents") or {},
    )
