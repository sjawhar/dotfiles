"""Each stream's plan for a Pacific day, from its scoreboard's `today` field (dispatch://INFRA-224, "Today's merges").

- The plan for a day is fixed at 09:00 Pacific: the list in the newest scoreboard version uploaded before then whose
  `today.date` is that day. Before 09:00 the current list is shown as it stands. When no list existed by 09:00, the
  first list uploaded that day is the plan, marked late with its upload time.
- After the plan is fixed, an item that is not in it is "added" at the upload time of the first version that had it,
  and does not count as planned. A planned item no longer in the list is "removed": struck through, still planned.
- An item is the same item when its `what` is the same, or when it names the same pull request; so a pull request
  added to a planned item later in the day is not an addition.
- A planned item counts as merged only if its pull request merged inside that Pacific day.
- An item may name the Dispatch issue it delivers (`"issue": "OSSV-150"`); `pr` may be "owner/repo#123" or the pull
  request's GitHub URL. plan_issues.py finds the issue of an item that names none from its pull request."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .model import parse_time
from .pace import PACIFIC
from .prs import parse_ref

FIX_AT = time(9, 0)


def pacific_day(t: datetime) -> date:
    return t.astimezone(PACIFIC).date()


def day_start(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=PACIFIC)


def fix_time(d: date) -> datetime:
    return datetime.combine(d, FIX_AT, tzinfo=PACIFIC)


@dataclass
class Item:
    what: str
    pr: str | None
    owner: str | None = None
    planned: bool = True
    added_at: datetime | None = None
    removed: bool = False
    example: bool = False
    issue: str | None = None  # the Dispatch issue the item delivers, as its key ("OSSV-150"), when the lead names it
    due: date | None = None  # week items: the Pacific day it is due, when the lead names one


@dataclass
class DayPlan:
    day: date
    before_fix: bool  # the day's 09:00 Pacific has not come yet: the list may still change
    fixed: bool  # a list was fixed at 09:00, or set late (or, before 09:00, a list exists)
    items: list[Item] = field(default_factory=list)
    late_at: datetime | None = None  # the plan was set after 09:00: the upload time of the day's first list

    @property
    def planned(self) -> list[Item]:
        return [i for i in self.items if i.planned]

    @property
    def added(self) -> list[Item]:
        return [i for i in self.items if not i.planned]


@dataclass
class WeekPlan:
    """The stream's plan for the current Pacific week (Monday to Sunday): the `week` list of its newest scoreboard
    version whose `week.start` is this week's Monday. It is not fixed at a time: the lead keeps it current."""

    start: date
    items: list[Item] = field(default_factory=list)
    set_at: datetime | None = None  # upload time of the version it comes from; None when the week has no plan


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_plan(versions: list[dict], now: datetime) -> WeekPlan:
    start = week_start(pacific_day(now))
    best: tuple[datetime, list[Item]] | None = None
    for v in versions or []:
        t = parse_time(v.get("created_at"))
        week = (v.get("data") or {}).get("week")
        if t is None or t > now or not isinstance(week, dict) or str(week.get("start")) != start.isoformat():
            continue
        if best is None or t >= best[0]:
            best = (t, _items(week))
    return WeekPlan(start, best[1], best[0]) if best else WeekPlan(start)


_PR_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)/?$")
_KEY = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def pr_ref(value) -> str | None:
    """The item's pull request as "owner/repo#123": that form as given, or a GitHub pull request URL rewritten to it."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    m = _PR_URL.match(v)
    return f"{m.group(1)}/{m.group(2)}#{m.group(3)}" if m else v


def issue_key(value) -> str | None:
    """A Dispatch issue key ("OSSV-150", or "dispatch://OSSV-150"), else None."""
    if not isinstance(value, str):
        return None
    v = value.strip().removeprefix("dispatch://").upper()
    return v if _KEY.match(v) else None


def due_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _items(plan: dict) -> list[Item]:
    """The items of a `today` or `week` list: each needs `what`; `pr`, `owner`, `issue` and `due` are optional."""
    out = []
    for it in plan.get("items") or []:
        if isinstance(it, dict) and str(it.get("what") or "").strip():
            owner = it.get("owner") if isinstance(it.get("owner"), str) else None
            out.append(Item(str(it["what"]).strip(), pr_ref(it.get("pr")), owner, issue=issue_key(it.get("issue")),
                            due=due_day(it.get("due"))))
    return out


def lists_for(versions: list[dict], d: date) -> list[tuple[datetime, list[Item]]]:
    """(upload time, items) of every scoreboard version whose `today.date` is `d`, oldest first."""
    out = []
    for v in versions or []:
        t = parse_time(v.get("created_at"))
        today = (v.get("data") or {}).get("today")
        if t is None or not isinstance(today, dict) or str(today.get("date")) != d.isoformat():
            continue
        out.append((t, _items(today)))
    out.sort(key=lambda x: x[0])
    return out


def same(a: Item, b: Item) -> bool:
    return a.what == b.what or (a.pr is not None and a.pr == b.pr)


def day_plan(versions: list[dict], d: date, now: datetime) -> DayPlan:
    """The plan for Pacific day `d` as it stands at `now` (for a past day, `now` past its end gives the final plan)."""
    lists = [(t, items) for t, items in lists_for(versions, d) if t <= now]
    fix = fix_time(d)
    current = lists[-1][1] if lists else []
    if now < fix:
        return DayPlan(d, True, bool(lists), [Item(i.what, i.pr, i.owner, issue=i.issue) for i in current])
    fixed_lists = [items for t, items in lists if t < fix]
    late_at = None
    if fixed_lists:
        fixed = fixed_lists[-1]
    elif lists:
        late_at, fixed = lists[0]
    else:
        fixed = []
    out: list[Item] = []
    for f in fixed:
        now_item = next((c for c in current if same(c, f)), None)
        if now_item is None:
            out.append(Item(f.what, f.pr, f.owner, removed=True, issue=f.issue))
        else:
            out.append(Item(now_item.what, now_item.pr or f.pr, now_item.owner or f.owner,
                            issue=now_item.issue or f.issue))
    for c in current:
        if any(same(c, f) for f in fixed):
            continue
        after = (lambda t: t > late_at) if late_at else (lambda t: t >= fix)
        first = next((t for t, items in lists if after(t) and any(same(c, x) for x in items)), None)
        out.append(Item(c.what, c.pr, c.owner, planned=False, added_at=first, issue=c.issue))
    return DayPlan(d, False, bool(fixed_lists) or late_at is not None, out, late_at)


def merged_in_day(item: Item, d: date, states: dict[str, dict]) -> bool:
    st = states.get(item.pr or "") or {}
    t = parse_time(st.get("merged_at"))
    return t is not None and day_start(d) <= t < day_start(d + timedelta(days=1))


def tally(plan: DayPlan, states: dict[str, dict]) -> tuple[int, int]:
    """(merged, planned) for the day: planned items, removed ones included, merged inside that Pacific day."""
    planned = plan.planned
    return sum(1 for i in planned if merged_in_day(i, plan.day, states)), len(planned)


def day_record(versions: list[dict], d: date, states: dict[str, dict]) -> tuple[int, int] | None:
    """The count recorded at midnight Pacific closing day `d`: (merged, planned), or None when no plan was fixed."""
    plan = day_plan(versions, d, day_start(d + timedelta(days=1)))
    return tally(plan, states) if plan.fixed else None


def refs_in(scoreboards: dict, now: datetime, days: int = 8) -> set[str]:
    """Every pull request named in a plan of the last `days` Pacific days, in any version of any stream, and in each
    stream's current week plan."""
    first = pacific_day(now) - timedelta(days=days - 1)
    out = set()
    for entry in (scoreboards or {}).values():
        versions = (entry or {}).get("versions") or []
        out.update(i.pr for i in week_plan(versions, now).items if i.pr and parse_ref(i.pr))
        for v in versions:
            today = (v.get("data") or {}).get("today")
            if not isinstance(today, dict):
                continue
            try:
                d = date.fromisoformat(str(today.get("date")))
            except ValueError:
                continue
            if d >= first:
                out.update(i.pr for i in _items(today) if i.pr and parse_ref(i.pr))
    return out
