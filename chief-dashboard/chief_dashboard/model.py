"""The page model: pure functions from a snapshot (collect.py), the time now and the start of the change window to
what the page shows. The status rules are the brief's:

- main number with a target: met when its latest value meets the target; otherwise, with values at two or more
  distinct times, a least-squares line through them projected to the end of the target date: on track if the
  projection meets the target, behind if not (and behind once the date has passed). One time: baseline only. No
  value: not measured, with the scoreboard's `missing` text.
- stale: scoreboard `updated_at` older than 24 h, or a fast number not measured in 48 h.
- loop stalled: no experiment started or ended in 48 h.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from . import videos
from .pace import Bound

SAMI = "sami@theorem.dev"
# Sami's own Dispatch projects: an ask waiting on a human on an issue nobody is assigned to there is his to see.
SAMI_PROJECTS = ("INFRA", "BET", "LINUX", "OSSV")
HEADLINE_MAX = 160  # characters of the lead's `headline` the glance row shows
STALE_BOARD = timedelta(hours=24)
STALE_FAST = timedelta(hours=48)
STALLED_LOOP = timedelta(hours=48)

MET, ON_TRACK, BEHIND, BASELINE, NOT_MEASURED, NO_TARGET, NO_BOARD = (
    "met",
    "on track",
    "behind",
    "baseline only",
    "not measured",
    "no target",
    "no scoreboard",
)
BAD_TARGET = "target not better than start"
# Worst first: the order a stream's main numbers are summarised in and streams are sorted by.
SEVERITY = {BEHIND: 0, NO_BOARD: 1, NOT_MEASURED: 1, NO_TARGET: 2, BAD_TARGET: 2, BASELINE: 3, ON_TRACK: 4, MET: 5}
NEEDS_LOOK = {BEHIND, NO_BOARD, NOT_MEASURED, NO_TARGET, BAD_TARGET}


def first_reading(values: list[dict]) -> float | None:
    """The value of the earliest reading. Several values at that one time are one reading: the file's last of them."""
    if not values:
        return None
    t0 = min(v["_t"] for v in values)
    return float([v for v in values if v["_t"] == t0][-1]["value"])


def target_not_better(start: float | None, target, better: str) -> bool:
    """A target at or worse than where the number started: a `lower` target at or above the first value, a
    `higher` one at or below it. Meeting it says nothing."""
    if start is None or not isinstance(target, int | float):
        return False
    return target >= start if better == "lower" else target <= start


def parse_time(s) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            t = datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def deadline(by: str) -> datetime | None:
    """The end (UTC) of a target's `by` date."""
    d = parse_time(by)
    if d is None:
        return None
    if len(by) <= 10:
        d = d + timedelta(days=1) - timedelta(seconds=1)
    return d


def short_name(name: str, limit: int = 72) -> str:
    s = (name or "").split(" (")[0].strip()
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return s


def meets(value: float, target: float, better: str) -> bool:
    return value <= target if better == "lower" else value >= target


def _num(x) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def _values(num: dict) -> list[dict]:
    """The number's values with a parseable time and a numeric value, in the file's order (newest last). A reading
    with `value` null and a numeric `lower_bound` keeps its bound as the value, as a `Bound`."""
    out = []
    for v in num.get("values") or []:
        t = parse_time(v.get("at"))
        if t is None:
            continue
        if _num(v.get("value")):
            out.append({**v, "_t": t})
        elif v.get("value") is None and _num(v.get("lower_bound")):
            out.append({**v, "value": Bound(v["lower_bound"]), "_t": t})
    return out


def project_line(values: list[dict], at: datetime) -> float | None:
    """The least-squares line through the values, evaluated at `at`; None with fewer than two distinct times."""
    xs = [v["_t"].timestamp() for v in values]
    ys = [float(v["value"]) for v in values]
    if len(set(xs)) < 2:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sum((x - mx) ** 2 for x in xs)
    return my + slope * (at.timestamp() - mx)


@dataclass
class Number:
    id: str
    name: str
    short: str
    role: str
    unit: str
    better: str
    status: str | None  # main numbers only
    latest: dict | None
    values: list[dict]
    target: dict | None
    target_at: datetime | None
    projection: float | None
    missing: str | None
    raw: dict = field(default_factory=dict)  # the file's entry, for optional version 1.1 fields


def number_view(num: dict, now: datetime) -> Number:
    values = _values(num)
    latest = values[-1] if values else None
    target = num.get("target") if isinstance(num.get("target"), dict) else None
    better = num.get("better") if num.get("better") in ("lower", "higher") else "higher"
    target_at = deadline(str(target.get("by"))) if target and target.get("by") else None
    tvalue = target.get("value") if target else None
    status = projection = None
    if num.get("role") == "main":
        if not isinstance(tvalue, int | float) or target_at is None:
            status = NOT_MEASURED if latest is None else NO_TARGET
        elif latest is None:
            status = NOT_MEASURED
        elif target_not_better(first_reading(values), tvalue, better):
            status = BAD_TARGET
        elif meets(latest["value"], tvalue, better) and not (isinstance(latest["value"], Bound) and better == "lower"):
            status = MET
        elif now > target_at:
            status = BEHIND
        else:
            projection = project_line(values, target_at)
            if projection is None:
                status = BASELINE
            else:
                status = ON_TRACK if meets(projection, tvalue, better) else BEHIND
    return Number(
        id=str(num.get("id", "")),
        name=str(num.get("name", "")),
        short=short_name(str(num.get("name", ""))),
        role=str(num.get("role", "")),
        unit=str(num.get("unit", "")),
        better=better,
        status=status,
        latest=latest,
        values=values,
        target=target,
        target_at=target_at,
        projection=projection,
        missing=num.get("missing"),
        raw=num,
    )


@dataclass
class Flag:
    level: str  # "bad" or "warn"
    short: str  # a few words for the overview
    text: str  # the full finding for the stream's card


@dataclass
class Stream:
    slug: str
    name: str
    rank: int
    attention: bool
    lead_title: str
    lead_session: str
    lead_machine: str | None
    lead_active: datetime | None
    lead_live_title: str | None
    issue: str | None
    board: dict | None
    board_version: dict | None
    board_error: str | None
    updated_at: datetime | None
    mains: list[Number] = field(default_factory=list)
    others: list[Number] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    worst: str = NO_BOARD
    scope: set[str] = field(default_factory=set)
    issue_title: str | None = None
    project_key: str | None = None

    @property
    def needs_look(self) -> bool:
        return self.worst in NEEDS_LOOK

    @property
    def headline(self) -> str | None:
        """The lead's one sentence for Sami today (scoreboard `headline`)."""
        h = (self.board or {}).get("headline")
        return h.strip() if isinstance(h, str) and h.strip() else None

    @property
    def headline_number(self) -> Number | None:
        """The lead's first main number: the one the lead ranks most important."""
        return self.mains[0] if self.mains else None

    @property
    def lead_name(self) -> str:
        """The lead in plain words, with the session's codename after it: "Task making lead (TASKCOACH)"."""
        plain = f"{self.name} lead"
        return plain if self.lead_title.lower() == plain.lower() else f"{plain} ({self.lead_title})"


def ago(t: datetime | None, now: datetime) -> str:
    if t is None:
        return "never"
    s = (now - t).total_seconds()
    if s < 0:
        return "in the future"
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{round(s / 60)} min ago"
    if s < 48 * 3600:
        return f"{round(s / 3600)} h ago"
    return f"{round(s / 86400)} d ago"


def latest_board(entry: dict | None) -> tuple[dict | None, dict | None, str | None]:
    """(data, version row, error) of the newest readable scoreboard version."""
    if not entry:
        return None, None, None
    if entry.get("error") and not entry.get("versions"):
        return None, None, entry["error"]
    for v in reversed(entry.get("versions") or []):
        if "data" in v:
            return v["data"], v, None
    errs = [v.get("error") for v in entry.get("versions") or [] if v.get("error")]
    return None, None, errs[-1] if errs else None


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


ENDED_VERDICTS = ("kept", "reverted", "inconclusive", "provisional")


def loop_health(board: dict, nums: list[Number], now: datetime) -> tuple[list[Flag], list[str]]:
    """The loop's failing checks (worst first) and its passing facts, each in words. Version 1.1 fields are used
    when present and never required."""
    flags: list[Flag] = []
    facts: list[str] = []
    updated = parse_time(board.get("updated_at"))
    if updated is None:
        flags.append(Flag("bad", "scoreboard undated", "The scoreboard has no readable updated_at"))
    elif now - updated > STALE_BOARD:
        flags.append(Flag("bad", "stale scoreboard", f"Scoreboard stale: last updated {ago(updated, now)} (over 24 h)"))
    else:
        facts.append(f"scoreboard updated {ago(updated, now)}")

    exps = [e for e in board.get("experiments") or [] if isinstance(e, dict)]
    events = [t for e in exps for t in (parse_time(e.get("started")), parse_time(e.get("ended"))) if t]
    if not exps:
        flags.append(Flag("bad", "loop stalled", "Loop stalled: no experiments recorded"))
    elif not events or now - max(events) > STALLED_LOOP:
        if not events:
            flags.append(Flag("bad", "loop stalled", "Loop stalled: no experiment has a start time yet"))
        else:
            last = ago(max(events), now)
            flags.append(Flag("bad", "loop stalled", f"Loop stalled: the last experiment started or ended {last} (over 48 h)"))
    else:

        def touched(e: dict) -> bool:
            return any(t and now - t <= STALLED_LOOP for t in (parse_time(e.get("started")), parse_time(e.get("ended"))))

        recent = [e for e in exps if touched(e)]
        verdicts: dict[str, int] = {}
        for e in recent:
            verdicts[str(e.get("verdict"))] = verdicts.get(str(e.get("verdict")), 0) + 1
        facts.append(
            _plural(len(recent), "experiment")
            + " started or ended in 48 h ("
            + ", ".join(f"{c} {v}" for v, c in sorted(verdicts.items()))
            + ")"
        )

    fasts = [n for n in nums if n.role == "fast"]
    never = [n for n in fasts if n.latest is None]
    stale = [n for n in fasts if n.latest is not None and now - n.latest["_t"] > STALE_FAST]
    fresh = [n for n in fasts if n.latest is not None and now - n.latest["_t"] <= STALE_FAST]
    failed = [n for n in fasts if n.raw.get("validation_status") == "failed"]
    if not fasts:
        flags.append(Flag("bad", "no fast number", "No fast number defined"))
    if never:
        flags.append(
            Flag(
                "bad",
                f"{_plural(len(never), 'fast number')} never measured",
                f"{_plural(len(never), 'fast number')} never measured: " + "; ".join(n.short for n in never),
            )
        )
    if stale:
        flags.append(
            Flag(
                "bad",
                f"{_plural(len(stale), 'fast number')} stale",
                f"{_plural(len(stale), 'fast number')} not measured in 48 h: "
                + "; ".join(f"{n.short} (last {ago(n.latest['_t'], now)})" for n in stale),
            )
        )
    if failed:
        flags.append(
            Flag(
                "bad",
                "fast number failed validation",
                "Fast number moved without the main number following (validation failed): "
                + "; ".join(n.short for n in failed),
            )
        )
    if fresh:
        newest = max(fresh, key=lambda n: n.latest["_t"])
        facts.append(f"fast number measured {ago(newest.latest['_t'], now)}")

    untargeted = [
        n for n in nums if n.role == "main" and (not n.target or not isinstance(n.target.get("value"), int | float))
    ]
    if untargeted:
        flags.append(
            Flag(
                "warn",
                f"{_plural(len(untargeted), 'main number')} without target",
                f"{_plural(len(untargeted), 'main number')} without a target: " + "; ".join(n.short for n in untargeted),
            )
        )
    bad_targets = [n for n in nums if n.status == BAD_TARGET]
    if bad_targets:
        flags.append(
            Flag(
                "warn",
                "target is not better than the start",
                "Target is not better than the start (the first value already meets it): "
                + "; ".join(
                    f"{n.short} (first {fmt_value(first_reading(n.values), n.unit)}, target "
                    f"{'≤' if n.better == 'lower' else '≥'} {fmt_value(n.target['value'], n.unit)})"
                    for n in bad_targets
                ),
            )
        )
    guarded = {str(n.raw.get("guards")) for n in nums if n.role == "guard" and n.raw.get("guards")}
    unguarded = [n for n in nums if n.role == "main" and n.id not in guarded]
    if unguarded:
        flags.append(
            Flag(
                "warn",
                f"{_plural(len(unguarded), 'main number')} without guard",
                f"{_plural(len(unguarded), 'main number')} with no guard number protecting it (a guard's \"guards\" field): "
                + "; ".join(n.short for n in unguarded),
            )
        )

    b = board.get("bottleneck")
    if not isinstance(b, dict) or not (b.get("text") or "").strip():
        flags.append(Flag("bad", "no bottleneck", "No bottleneck named"))
    elif not (b.get("evidence") or "").strip():
        flags.append(Flag("bad", "bottleneck unbacked", "The bottleneck has no evidence"))
    else:
        facts.append("bottleneck has evidence")

    def ids(rows: list[dict]) -> str:
        return ", ".join(str(e.get("id") or "?") for e in rows)

    no_after = [e for e in exps if e.get("ended") and e.get("after") is None]
    no_before = [e for e in exps if e.get("before") is None]
    if no_after:
        flags.append(Flag("warn", "ended without 'after'", f"Ended with no 'after' value: experiment {ids(no_after)}"))
    if no_before:
        flags.append(
            Flag(
                "warn",
                f"{_plural(len(no_before), 'experiment')} without 'before'",
                f"No 'before' value, so no verdict can rest on it: experiment {ids(no_before)}",
            )
        )
    unpredicted = [e for e in exps if not e.get("ended") and e.get("predicted") is None]
    if unpredicted:
        flags.append(
            Flag(
                "warn",
                f"{_plural(len(unpredicted), 'experiment')} without prediction",
                f"Running with no predicted value written before it started: experiment {ids(unpredicted)}",
            )
        )
    headline = board.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        flags.append(Flag("warn", "no headline", "No headline: the one sentence Sami should know today"))
    elif len(headline) > HEADLINE_MAX:
        flags.append(
            Flag("warn", "headline too long", f"The headline is {len(headline)} characters; the page shows {HEADLINE_MAX}")
        )
    dropped = [
        e
        for e in exps
        if e.get("verdict") == "reverted" and isinstance(e.get("designs_tried"), int) and e["designs_tried"] < 3
    ]
    for e in dropped:
        why = e.get("why_stopped") or "no reason given"
        flags.append(
            Flag(
                "warn",
                "direction dropped early",
                f"Experiment {e.get('id')} reverted after {_plural(e['designs_tried'], 'design')} of its direction: {why}",
            )
        )
    flags.sort(key=lambda f: f.level != "bad")
    return flags, facts


UNRANKED = 1000  # a stream the overrides file does not rank sorts after every ranked one


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def build_streams(snap: dict, now: datetime) -> list[Stream]:
    """One stream per discovered stream issue (label `stream`). Name, lead and numbers come from its scoreboard;
    the overrides file (streams.json) adds only rank and attention, matched by issue key or slug."""
    agents = snap.get("agents") or {}
    overrides = [o for o in snap.get("registry", {}).get("streams", []) if isinstance(o, dict)]
    out = []
    for si in snap.get("stream_issues") or []:
        issue = si["key"]
        entry = (snap.get("scoreboards") or {}).get(issue)
        board, version, err = latest_board(entry)
        b = board or {}
        by_issue = next((o for o in overrides if o.get("issue") == issue), None)
        slug = str(b.get("stream") or (by_issue or {}).get("slug") or "") or _slugify(issue)
        ov = by_issue or next((o for o in overrides if o.get("slug") == slug), {})
        lead = b.get("lead") if isinstance(b.get("lead"), dict) else {}
        claim = si.get("claim") or {}
        lead_session = lead.get("session") or (claim.get("id") if claim.get("kind") == "session" else "") or ""
        lead_title = lead.get("title") or (claim.get("origin") or {}).get("session_title") or "no lead named"
        live = agents.get(lead_session)
        st = Stream(
            slug=slug,
            name=str(b.get("name") or ov.get("name") or (si.get("title") or issue).split(":")[0]),
            rank=int(ov.get("rank") or UNRANKED),
            attention=bool(ov.get("attention")),
            lead_title=str(lead_title),
            lead_session=str(lead_session),
            lead_machine=lead.get("machine"),
            lead_active=parse_time((live or {}).get("last_activity")),
            lead_live_title=(live or {}).get("title"),
            issue=issue,
            board=board,
            board_version=version,
            board_error=err,
            updated_at=parse_time(b.get("updated_at")),
            issue_title=(entry or {}).get("issue_title") or si.get("title"),
            project_key=si.get("project"),
        )
        if board is not None:
            nums = [number_view(n, now) for n in board.get("numbers") or [] if isinstance(n, dict)]
            st.mains = [n for n in nums if n.role == "main"]  # the lead's order: the first is the headline number
            st.others = [n for n in nums if n.role != "main"]
            st.flags, st.facts = loop_health(board, nums, now)
            if not st.mains:
                st.flags.insert(0, Flag("bad", "no main number", "No main number defined"))
                st.worst = NOT_MEASURED
            else:
                st.worst = min((n.status or NOT_MEASURED for n in st.mains), key=lambda s: SEVERITY.get(s, 9))
            st.scope = {issue, *[k for k in board.get("scope_issues") or [] if isinstance(k, str)]}
        else:
            st.scope = {issue}
        out.append(st)
    out.sort(key=lambda st: (not st.attention, not st.needs_look, st.rank, st.name))
    return out


# ------------------------------------------------------------------------------------- asks waiting on Sami

_SENT = re.compile(r"(?<=[.?!])\s+(?=[A-Z\"“(])")


def sentences(text: str) -> list[str]:
    flat = " ".join(line.strip() for line in (text or "").splitlines() if line.strip())
    return [s.strip() for s in _SENT.split(flat) if s.strip()]


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def split_question(text: str) -> tuple[str, str | None]:
    """(headline, context): the first sentence that asks something (else the first sentence), and the first
    sentence when it is not the headline."""
    ss = sentences(text)
    if not ss:
        return "(empty ask)", None
    head = next((s for s in ss if s.endswith("?")), None) or ss[0]
    return head, (ss[0] if ss[0] != head else None)


def inbox_url(snap: dict) -> str:
    """Sami's Dispatch Inbox: the web app's home page (its "Go to Inbox" shortcut navigates to "/")."""
    return snap.get("dispatch_web", "").rstrip("/") + "/"


@dataclass
class OpenAsk:
    """An open Dispatch ask waiting on Sami. The page counts these per stream, lists new ones under "what changed"
    and names them in the Needs-you box; answering them happens in his Dispatch Inbox."""

    headline: str
    context: str | None
    stream: Stream | None
    since: datetime | None
    issue_key: str | None
    ask_id: str | None = None
    ref: str | None = None  # the ask's path on the Dispatch web app, e.g. /issues/BET-9/asks/<id>


def ancestry(key: str | None, issues: dict, limit: int = 8) -> list[str]:
    """`key` then its parents, nearest first, from the issues the collector read (cycle-safe, bounded)."""
    out: list[str] = []
    while key and key not in out and len(out) < limit:
        out.append(key)
        key = (issues.get(key) or {}).get("parent")
    return out


def asks_waiting_on_sami(snap: dict, streams: list[Stream]) -> list[OpenAsk]:
    """Open asks waiting on a person, on issues assigned to Sami (or unassigned in his projects), each joined to the
    stream whose issue or scope_issues hold its issue or any ancestor of it."""
    issues = snap.get("ask_issues") or {}
    out = []
    for a in snap.get("asks") or []:
        if a.get("waiting_on") != "human":
            continue
        key = ((a.get("owner") or {}).get("issue") or {}).get("key")
        info = issues.get(key) or {}
        if not (
            info.get("assignee") == SAMI
            or (key in issues and info.get("assignee") is None and a.get("project") in SAMI_PROJECTS)
        ):
            continue
        head, context = split_question(a.get("question", ""))
        chain = ancestry(key, issues)
        stream = next((st for st in sorted(streams, key=lambda s: s.rank) if any(k in st.scope for k in chain)), None)
        out.append(
            OpenAsk(head, context, stream, parse_time(a.get("created_at")), key, a.get("id"), a.get("ref"))
        )
    return out


# ------------------------------------------------------------------------------------------------------ changes


@dataclass
class Change:
    at: datetime | None
    stream: str
    kind: str  # "number", "experiment", "decision", "scoreboard", "bottleneck"
    text: str


def board_at(entry: dict | None, t: datetime) -> dict | None:
    """The scoreboard as it stood at `t`: the newest readable version created at or before it."""
    best = None
    for v in (entry or {}).get("versions") or []:
        ct = parse_time(v.get("created_at"))
        if "data" in v and ct and ct <= t:
            best = v["data"]
    return best


def fmt_value(v, unit: str = "") -> str:
    if v is None:
        return "–"
    if isinstance(v, bool) or not isinstance(v, int | float):
        return str(v)
    if isinstance(v, Bound):
        return "more than " + fmt_value(float(v), unit)
    if abs(v) >= 1000:
        s = f"{v:,.0f}"
    elif float(v).is_integer():
        s = str(int(v))
    else:
        s = f"{v:.3g}"
    if not unit:
        return s
    if v == 1:
        # "1 trees of 4" → "1 tree of 4": the unit's first word loses its plural s (never a bare "s" or "ss").
        unit = re.sub(r"^([A-Za-z]+[^sS\W])s\b", r"\1", unit)
    return f"{s}%" if unit == "%" else f"{s} {unit}"


def build_changes(snap: dict, streams: list[Stream], since: datetime, now: datetime) -> list[Change]:
    out: list[Change] = []
    for st in streams:
        entry = (snap.get("scoreboards") or {}).get(st.issue) if st.issue else None
        if st.board is None:
            continue
        old = board_at(entry, since)
        first = (entry or {}).get("versions", [{}])[0]
        if old is None:
            exps = len(st.board.get("experiments") or [])
            out.append(
                Change(
                    parse_time(first.get("created_at")),
                    st.name,
                    "scoreboard",
                    f"First scoreboard published ({_plural(len(st.mains), 'main number')}, "
                    f"{_plural(exps, 'experiment')})",
                )
            )
            old = {}
        old_nums = {n.get("id"): n for n in old.get("numbers") or [] if isinstance(n, dict)}
        for n in st.mains + st.others:
            if old:
                prev_vals = _values(old_nums.get(n.id, {}))
                seen = {(v.get("at"), v.get("value")) for v in prev_vals}
                new = [v for v in n.values if (v.get("at"), v.get("value")) not in seen]
            else:
                # A scoreboard first published inside the window carries its history: values measured inside the
                # window are the changes, against the last one measured before it.
                prev_vals = [v for v in n.values if v["_t"] <= since]
                new = [v for v in n.values if v["_t"] > since]
            if not new:
                continue
            before = prev_vals[-1]["value"] if prev_vals else None
            after = n.values[-1]["value"]
            role = {"main": "Main number", "fast": "Fast number", "guard": "Guard number"}.get(n.role, "Number")
            # Full names and full text: a change line clipped to a few words tells the reader nothing.
            if before is None:
                text = f"{role} first measured: {n.name} = {fmt_value(after, n.unit)}"
            else:
                text = f"{role}: {n.name} {fmt_value(before, n.unit)} → {fmt_value(after, n.unit)}"
            out.append(Change(new[-1]["_t"], st.name, "number", text))
        old_exps = {e.get("id"): e for e in old.get("experiments") or [] if isinstance(e, dict)}
        for e in st.board.get("experiments") or []:
            if not isinstance(e, dict):
                continue
            o = old_exps.get(e.get("id"))
            started, ended = parse_time(e.get("started")), parse_time(e.get("ended"))
            change = str(e.get("change") or "")
            # A first scoreboard carries its history: only events inside the window are changes.
            if e.get("ended") and (o is None or not o.get("ended")) and (old or (ended and ended > since)):
                out.append(
                    Change(
                        ended,
                        st.name,
                        "experiment",
                        f"Experiment ended, {e.get('verdict')}: {change} "
                        f"({fmt_value(e.get('before'))} → {fmt_value(e.get('after'))} on {e.get('number')})",
                    )
                )
            elif o is None and not e.get("ended") and (old or (started and started > since)):
                out.append(Change(started, st.name, "experiment", f"Experiment started: {change}"))
        ob, nb = (old.get("bottleneck") or {}), (st.board.get("bottleneck") or {})
        if old and isinstance(nb, dict) and isinstance(ob, dict) and ob.get("text") != nb.get("text"):
            out.append(Change(st.updated_at, st.name, "bottleneck", f"Bottleneck changed: {nb.get('text')}"))
    return out


def ask_changes(asks: list[OpenAsk], since: datetime) -> list[Change]:
    """Each open ask waiting on Sami that was opened inside the window."""

    def text(a: OpenAsk) -> str:
        head = a.headline if len(a.headline) >= 60 or not a.context else f"{a.headline} ({a.context})"
        return f"New decision for you: {head}"

    return [
        Change(a.since, a.stream.name if a.stream else "No stream", "decision", text(a))
        for a in asks
        if a.since and a.since > since
    ]


@dataclass
class Page:
    now: datetime
    read_at: datetime | None
    errors: list[str]
    streams: list[Stream]
    asks: list[OpenAsk]  # open Dispatch asks waiting on Sami: counted per stream, never listed (that is his Inbox)
    inbox: str
    changes: list[Change]
    since: datetime
    since_label: str


def build(snap: dict, now: datetime, since: datetime, since_label: str) -> Page:
    streams = build_streams(snap, now)
    asks = asks_waiting_on_sami(snap, streams)
    changes = build_changes(snap, streams, since, now) + ask_changes(asks, since)
    # Outcomes first: new decisions, then numbers that moved, then experiments; newest first within each kind.
    kind_order = {"decision": 0, "number": 1, "experiment": 2, "bottleneck": 3, "scoreboard": 4}
    changes.sort(key=lambda c: (c.at or now).timestamp(), reverse=True)
    changes.sort(key=lambda c: (kind_order.get(c.kind, 9), c.kind == "experiment" and "started" in c.text))
    return Page(
        now=now,
        read_at=parse_time(snap.get("read_at")),
        errors=list(snap.get("errors") or []),
        streams=streams,
        asks=asks,
        inbox=inbox_url(snap),
        changes=changes,
        since=since,
        since_label=since_label,
    )


# ------------------------------------------------------------------------------------------------------- videos


@dataclass
class Video:
    row: dict  # the collected row: {stem, name, uploaded, sha, seconds, title, error}
    title: str
    uploaded: datetime | None


def video_key(issue: str, row: dict) -> tuple:
    """What makes two collected rows the same video: the sha256 of the file. A row whose file could not be
    downloaded has no sha256 and is its own video, keyed by its issue and file name."""
    return ("sha", row["sha"]) if row.get("sha") else ("file", issue, row.get("name"))


def stream_videos(rows: list[dict] | None, issue: str, stream_name: str) -> list[Video]:
    """One stream's videos, newest upload first, each once (same sha256 = same video): what the stream's own player
    offers."""
    out: list[Video] = []
    seen: set[tuple] = set()
    for v in rows or []:
        key = video_key(issue, v)
        if key in seen:
            continue
        seen.add(key)
        title = v.get("title") or videos.title_from_name(v["stem"], stream_name)
        out.append(Video(v, title, parse_time(v.get("uploaded"))))
    out.sort(key=lambda x: x.uploaded or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out
