"""The Pace label of a main number and the label of a guard (dispatch://INFRA-224, "How on pace is computed").

- The line: straight from the number's first reading, at that reading's time, to its target at the end of the target
  date (Pacific).
- On pace: the target is met, or the newest reading is on the line or on its better side.
- Behind: the newest reading is on the worse side of the line.
- Can't tell: no target, fewer than two readings, or the newest reading older than twice the measuring interval
  (`every`, in hours; 6 unless the lead sets more).
- Needed per day: the target minus the newest reading, divided by the days left.
- A reading that is only a lower bound (`Bound`; the true value is at least it) is a reading for freshness. For a
  lower-is-better number it is Behind when the bound is already worse than the line, else Can't tell ("only a lower
  bound"); for a higher-is-better number it is On pace at or above the line (or the target), else Can't tell.

A guard is Holding unless its newest reading is on the worse side of its first reading (Worse); with fewer than two
readings it is Can't tell."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
ON_PACE, BEHIND, CANT_TELL, HOLDING, WORSE = "On pace", "Behind", "Can't tell", "Holding", "Worse"
DEFAULT_EVERY_HOURS = 6.0


class Bound(float):
    """A reading that is only a lower bound (the scoreboard's `value: null` with `lower_bound`): the true value is at
    least this. It is a float, so it orders and draws like any reading."""


Reading = tuple[datetime, float]  # the value is a Bound when the reading is only a lower bound


def readings(values: list[dict]) -> list[Reading]:
    """(time, value) per distinct time, oldest first; several values at one time are one reading, the file's last."""
    by_time: dict[datetime, float] = {}
    for v in values:
        x = v["value"]
        by_time[v["_t"]] = x if isinstance(x, Bound) else float(x)
    return sorted(by_time.items())


def target_end(by) -> datetime | None:
    """The end of the target date as a Pacific calendar day: midnight Pacific at the start of the next day."""
    try:
        d = datetime.fromisoformat(str(by)[:10]).date()
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=PACIFIC) + timedelta(days=1)


def every_hours(num: dict) -> float:
    e = num.get("every")
    if isinstance(e, int | float) and not isinstance(e, bool) and e > 0:
        return max(float(e), DEFAULT_EVERY_HOURS)
    return DEFAULT_EVERY_HOURS


def line_at(first: Reading, target: float, end: datetime, t: datetime) -> float:
    t0, v0 = first
    span = (end - t0).total_seconds()
    if span <= 0 or t >= end:
        return target
    return v0 + (target - v0) * max(0.0, (t - t0).total_seconds()) / span


def better_or_equal(value: float, ref: float, better: str) -> bool:
    return value <= ref if better == "lower" else value >= ref


@dataclass
class Pace:
    label: str  # ON_PACE, BEHIND or CANT_TELL
    reason: str | None = None  # why Can't tell, or "target met" / "target date passed"
    needed_per_day: float | None = None  # target minus newest reading, per day left
    end: datetime | None = None  # end of the target date
    target: float | None = None
    met: bool = False


def hours_text(hours: float) -> str:
    return f"{round(hours)} h" if hours < 48 else f"{round(hours / 24)} d"


def pace(rs: list[Reading], target, by, better: str, every_h: float, now: datetime) -> Pace:
    end = target_end(by) if by else None
    if not isinstance(target, int | float) or isinstance(target, bool) or end is None:
        return Pace(CANT_TELL, "no target")
    target = float(target)
    if not rs:
        return Pace(CANT_TELL, "no reading", end=end, target=target)
    t_new, v_new = rs[-1]
    days_left = (end - now).total_seconds() / 86400
    needed = (target - v_new) / days_left if days_left > 0 else None
    age = (now - t_new).total_seconds() / 3600
    if age > 2 * every_h:
        return Pace(CANT_TELL, f"no reading for {hours_text(age)}", needed, end, target)
    if better_or_equal(v_new, target, better) and not (isinstance(v_new, Bound) and better == "lower"):
        return Pace(ON_PACE, "target met", None, end, target, met=True)
    if len(rs) < 2:
        return Pace(CANT_TELL, "one reading", needed, end, target)
    on_line = line_at(rs[0], target, end, t_new)
    if isinstance(v_new, Bound):
        # The true value is at least the bound: only "worse than the line" (lower is better) or "on or above the
        # line" (higher is better) follows from it.
        if better == "lower" and v_new > on_line:
            return Pace(BEHIND, "target date passed" if days_left <= 0 else None, needed, end, target)
        if better == "higher" and v_new >= on_line:
            return Pace(ON_PACE, "target date passed" if days_left <= 0 else None, needed, end, target)
        return Pace(CANT_TELL, "only a lower bound", needed, end, target)
    label = ON_PACE if better_or_equal(v_new, on_line, better) else BEHIND
    return Pace(label, "target date passed" if days_left <= 0 else None, needed, end, target)


def guard(rs: list[Reading], better: str) -> Pace:
    if len(rs) < 2:
        return Pace(CANT_TELL, "no reading" if not rs else "one reading")
    return Pace(HOLDING if better_or_equal(rs[-1][1], rs[0][1], better) else WORSE)
