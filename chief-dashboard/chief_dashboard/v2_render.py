"""HTML of the front page (dispatch://INFRA-224 v6, "Layout"): a top line, the main numbers behind their line across
every stream, then one section per stream in rank order, each with its header line, one card per main number (the
verdict, the value large, the target, the gap to the line, a chart, its guards), today's plan and a footer. Colour
marks only On pace / Behind / Can't tell (and Holding / Worse) and merged / open / failing."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from . import model, render, rounds
from .pace import BEHIND, CANT_TELL, HOLDING, ON_PACE, PACIFIC, WORSE, Bound, Pace, line_at
from .plan_issues import live_url
from .prs import parse_ref
from .render import MAX_NOTE, e
from .v2_model import FAILING, MERGED, GuardRow, KpiRow, PageV2, PlanLine, StreamView, clock

LABEL_CLASS = {ON_PACE: "ok", HOLDING: "ok", BEHIND: "bad", WORSE: "bad", CANT_TELL: "grey"}


# ------------------------------------------------------------------------------------------------- values


def is_share(unit: str) -> bool:
    return unit.lower().startswith("share")


def num(v: float | None, unit: str = "") -> str:
    """A value as the page shows it: a share as a percentage, thousands separated, at most 3 significant digits."""
    if v is None:
        return "–"
    if is_share(unit):
        p = v * 100
        return f"{p:.0f}%" if p == 0 or abs(p) >= 10 else f"{p:.2g}%"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.3g}" if abs(v) >= 0.01 else f"{v:.2g}"


def unit_text(unit: str) -> str:
    return "share, %" if is_share(unit) else unit


def kpi_name(n: model.Number) -> str:
    """The number's name in plain words, in full: no name on this page is ever cut."""
    return (n.name or "").split(" (")[0].strip()


def round2(v: float) -> str:
    """A derived figure — a gap, a rate — to two significant digits. A six-day forecast carries no more than that,
    and three digits beside a headline rounded to 45% reads as false precision (Few, *Information Dashboard
    Design*: a number's precision is the precision the decision needs)."""
    if v == 0:
        return "0"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    return f"{float(f'{v:.2g}'):g}"


def per_day(p: Pace, unit: str) -> str:
    """The change per day needed from now to reach the target: "+6.1 per day". The unit is the one the Now line
    names; repeating it here would read "needs +2.47 requests per week per day"."""
    if p.needed_per_day is None:
        return ""
    v = p.needed_per_day
    size = round2(abs(v) * 100 if is_share(unit) else abs(v))
    return f"{'+' if v >= 0 else '−'}{size}{' pts' if is_share(unit) else ''} per day"


def age(t: datetime, now: datetime) -> str:
    h = (now - t).total_seconds() / 3600
    if h < 1:
        return f"{max(1, round(h * 60))} min ago"
    return f"{round(h)} h ago" if h < 48 else f"{round(h / 24)} d ago"


# ------------------------------------------------------------------------------------------------- chart

# The chart's own coordinates are its drawn pixels at the width the row gives it, so its labels are the page's
# small size (12) rather than a scaled-down copy of it. One geometry for every chart on the page: Tufte's small
# multiples — the same shape repeated, so the eye compares streams instead of re-reading each chart's frame.
CHART_W, CHART_H = 480, 136
PAD_L, PAD_R, PAD_T, PAD_B = 46, 100, 16, 24  # the right pad is the gutter the target and pace labels are written in


def chart_domain(values: list[float], target: float | None) -> tuple[float, float]:
    """The value range the chart draws: the readings and today's expected value, plus the target only when it lies
    within one span of them, so a far target never flattens the readings into a line (Sami: "the trend is very hard
    to actually read")."""
    lo, hi = min(values), max(values)
    span = hi - lo or abs(hi) * 0.2 or 1.0
    if target is not None and lo - span <= target <= hi + span:
        lo, hi = min(lo, target), max(hi, target)
        span = hi - lo or span
    floor = lo - span * 0.08
    return (max(0.0, floor) if lo >= 0 else floor), hi + span * 0.08  # a count never reads as negative


def axis_num(v: float, unit: str) -> str:
    return num(v, unit) if is_share(unit) else num(v)


def chart(k: KpiRow | GuardRow, now: datetime, cls: str, title: str) -> str:
    """The number over time, from its first reading to now: the readings joined by a line (a dashed segment and an
    open dot for a reading that is only a lower bound), the dashed line Pace judges against, a hollow dot where that
    line says the number should be at its newest reading, the gap between the two shaded, and the target drawn when
    it is in range or named at the top right when it is not. The target and the pace line are named at their own
    right-hand ends, in the plot's right gutter: Tufte's direct labelling, so the chart needs no legend and no
    glance away from the data."""
    rs = k.readings
    if not rs:
        return '<p class="dim small nochart">No reading yet.</p>'
    p = k.label
    unit = k.num.unit
    is_main = isinstance(k, KpiRow)
    t_first, t_last = rs[0][0], rs[-1][0]
    x1_time = max(now, t_last)
    line = is_main and p.target is not None and p.end is not None
    line_end = line_at(rs[0], p.target, p.end, x1_time) if line else None
    values = [float(v) for _, v in rs] + ([line_end] if line_end is not None else [])
    y0, y1 = chart_domain(values, p.target if line else None)
    x0s, x1s = t_first.timestamp(), x1_time.timestamp()

    def X(t: datetime) -> float:
        return PAD_L + (CHART_W - PAD_L - PAD_R) * (0.5 if x1s == x0s else (t.timestamp() - x0s) / (x1s - x0s))

    def Y(v: float) -> float:
        return PAD_T + (CHART_H - PAD_T - PAD_B) * (1 - (v - y0) / (y1 - y0))

    out = [f'<svg class="kchart" viewBox="0 0 {CHART_W} {CHART_H}" role="img"><title>{e(title)}</title>']
    # Axis: the value range at the left, the first reading's day and "now" under the plot.
    out.append(f'<line class="ax" x1="{PAD_L}" y1="{Y(y1):.1f}" x2="{CHART_W - PAD_R}" y2="{Y(y1):.1f}"/>'
               f'<line class="ax" x1="{PAD_L}" y1="{Y(y0):.1f}" x2="{CHART_W - PAD_R}" y2="{Y(y0):.1f}"/>')
    out.append(f'<text x="{PAD_L - 4}" y="{Y(y1) + 4:.1f}" text-anchor="end">{e(axis_num(y1, unit))}</text>'
               f'<text x="{PAD_L - 4}" y="{Y(y0):.1f}" text-anchor="end">{e(axis_num(y0, unit))}</text>')
    out.append(f'<text x="{PAD_L}" y="{CHART_H - 4}">{e(t_first.astimezone(PACIFIC).strftime("%b %-d"))}</text>'
               f'<text x="{CHART_W - PAD_R}" y="{CHART_H - 4}" text-anchor="end">now</text>')
    if line:
        plot_r = CHART_W - PAD_R
        label_x = plot_r + 5
        y_pace = Y(line_end)
        out.append(f'<line class="pl" x1="{X(t_first):.1f}" y1="{Y(float(rs[0][1])):.1f}" x2="{X(x1_time):.1f}" '
                   f'y2="{y_pace:.1f}"/>')
        if y0 <= p.target <= y1:
            y_target = Y(p.target)
            out.append(f'<line class="tg" x1="{PAD_L}" y1="{y_target:.1f}" x2="{plot_r}" y2="{y_target:.1f}"/>'
                       f'<text class="tgl" x="{label_x}" y="{y_target + 4:.1f}">target {e(axis_num(p.target, unit))}</text>')
            if abs(y_pace - y_target) < 13:  # the two labels would sit on each other: push the pace label clear
                y_pace = y_target + (13 if y_pace >= y_target else -13)
        else:
            arrow = "↑" if p.target > y1 else "↓"
            out.append(f'<text class="tgl" x="{CHART_W - 4}" y="10" text-anchor="end">target {e(axis_num(p.target, unit))} '
                       f'by {e((p.end - timedelta(days=1)).astimezone(PACIFIC).strftime("%b %-d"))} {arrow}</text>')
            y_pace = max(y_pace, 26)  # that note holds the chart's top line: the pace label goes under it
        out.append(f'<text class="pll" x="{label_x}" y="{min(max(y_pace + 4, 10), CHART_H - 4):.1f}">'
                   f'pace {e(axis_num(line_end, unit))}</text>')
    pts = [(X(t), Y(float(v)), isinstance(v, Bound)) for t, v in rs]
    for (xa, ya, _), (xb, yb, bound) in zip(pts, pts[1:], strict=False):
        out.append(f'<line class="rl{" b" if bound else ""}" x1="{xa:.1f}" y1="{ya:.1f}" x2="{xb:.1f}" y2="{yb:.1f}"/>')
    if is_main and k.expected is not None:
        xe, ye = X(t_last), Y(k.expected)
        out.append(f'<line class="gap {cls}" x1="{xe:.1f}" y1="{ye:.1f}" x2="{xe:.1f}" y2="{pts[-1][1]:.1f}"/>'
                   f'<circle class="exp" cx="{xe:.1f}" cy="{ye:.1f}" r="3.2"/>')
    # A dot per reading becomes a smear once the readings are dense, and carries nothing the line does not: past
    # 20 readings only the newest and the bounded ones (which the line alone cannot show) keep theirs.
    dense = len(pts) > 20
    for i, (x, y, bound) in enumerate(pts):
        last = i == len(pts) - 1
        if dense and not last and not bound:
            continue
        c = ("dot " + cls if last else "dot") + (" open" if bound else "")
        out.append(f'<circle class="{c}" cx="{x:.1f}" cy="{y:.1f}" r="{3.6 if last else 2.2}"/>')
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------------------------------------- cards


def label_html(p: Pace) -> str:
    return f'<span class="lbl {LABEL_CLASS[p.label]}">{e(p.label)}</span>'


def fig(label: str, value: str, qual: str, cls: str = "") -> str:
    """One line of a row's figures: what it is, the figure itself, and the words that qualify it. The figures of a
    row stand in one column, tabular and right-aligned, because they are compared with each other (Stephen Few,
    *Information Dashboard Design*: numbers that are compared are aligned on their digits, not scattered through
    sentences). `value` and `qual` are already HTML."""
    return (f'<div class="fig {cls}"><span class="fig-k">{e(label)}</span>'
            f'<span class="fig-v">{value}</span><span class="fig-q">{qual}</span></div>')


def now_value(rs, unit: str, now: datetime) -> str:
    """The newest reading as the row's one large figure, with the unit and age beside it."""
    if not rs:
        return fig("Now", '<span class="dim">–</span>', "no reading yet", "now")
    t, v = rs[-1]
    pre = '<span class="pre">more than</span>' if isinstance(v, Bound) else ""
    words = [x for x in ("" if is_share(unit) else unit, age(t, now)) if x]
    return fig("Now", f'{pre}<span class="n">{e(num(v, unit))}</span>', e(" · ".join(words)), "now")


def amount(v: float, unit: str) -> str:
    """A difference in the number's own unit: a share's in percentage points."""
    if is_share(unit):
        return f"{round2(v * 100)} pts"
    return f"{round2(v)}" + (f" {unit}" if unit else "")


def target_text(n: model.Number, p: Pace) -> str:
    t = n.target or {}
    if not isinstance(t.get("value"), int | float) or p.end is None:
        return "No target"
    sign = "≤" if n.better == "lower" else "≥"
    by = model.parse_time(str(t.get("by")))
    return f"Target {sign} {num(t['value'], n.unit)}" + (f" by {by:%b %-d}" if by else "")


def pace_value(k: KpiRow) -> str:
    """Where the line Pace judges against says the number should be today, and how far the reading is from it. The
    row's key and the chart's own "pace" label say what the figure is, so the words carry only the gap."""
    if k.expected is None:
        return ""
    qual = ""
    if k.gap is not None and k.label.label != CANT_TELL:
        word = "ahead" if k.gap >= 0 else "behind"
        qual = f'<b class="gapw">{e(amount(abs(k.gap), k.num.unit))} {word}</b>'
    return fig("Pace line", f'<span class="n">{e(num(k.expected, k.num.unit))}</span>', qual, "pace")


def target_value(k: KpiRow, now: datetime) -> str:
    """The target with its date, what is needed per day and the days left. Which direction is better is the ≥ or ≤
    in front of the figure, not another clause."""
    n, p = k.num, k.label
    t = n.target or {}
    if not isinstance(t.get("value"), int | float) or p.end is None:
        return fig("Target", '<span class="dim">–</span>', "none set", "target")
    sign = "≤" if n.better == "lower" else "≥"
    by = model.parse_time(str(t.get("by")))
    parts = [f"by {by:%b %-d}"] if by else []
    if p.met:
        parts.append("met")
    else:
        if p.needed_per_day is not None:
            parts.append(per_day(p, n.unit))
        days = (p.end - now).total_seconds() / 86400
        parts.append(f"{round(days)} d left" if days >= 1 else "target date passed" if days <= 0 else "last day")
    value = f'<span class="pre">{sign}</span><span class="n">{e(num(t["value"], n.unit))}</span>'
    return fig("Target", value, e(" · ".join(parts)), "target")


def guard_line(g: GuardRow, now: datetime) -> str:
    """One guard: its name, then its newest value, its verdict and its first reading, each separated, so the name
    and the number do not read as one sentence."""
    n, p = g.num, g.label
    first = f"first {num(g.readings[0][1], n.unit)}" if g.readings else ""
    latest = num(g.readings[-1][1], n.unit) if g.readings else "–"
    why = p.reason if p.label == CANT_TELL else first
    return (f'<li class="kg {LABEL_CLASS[p.label]}"><span class="gn" title="{e(n.name)}">{e(kpi_name(n))}</span>'
            f'<span class="gv"><b>{e(latest)}</b> {label_html(p)}'
            f'{f" <span class=dim>{e(why)}</span>" if why else ""}</span></li>')


def guards_block(guards: list[GuardRow], now: datetime, prefix: str = "") -> str:
    """A guard that got worse is shown in full; the others fold into one line of counts."""
    if not guards:
        return ""
    worse = [g for g in guards if g.label.label == WORSE]
    rest = [g for g in guards if g.label.label != WORSE]
    out = ""
    if worse:
        out += f'<ul class="kgs">{"".join(guard_line(g, now) for g in worse)}</ul>'
    if rest:
        holding = sum(1 for g in rest if g.label.label == HOLDING)
        unknown = len(rest) - holding
        words = [f"{holding} holding"] if holding else []
        words += [f"{unknown} can't tell"] if unknown else []
        noun = "guard" if len(rest) == 1 else "guards"
        out += (f'<details class="kgd"><summary>{e(prefix)}{len(rest)} {noun}: {e(", ".join(words))}</summary>'
                f'<ul class="kgs">{"".join(guard_line(g, now) for g in rest)}</ul></details>')
    return out


def kpi_row(k: KpiRow, now: datetime) -> str:
    """One main number as one row. Its name is a full-width line of ordinary text with the verdict at the end of it
    (Sami, 2026-10-26: a KPI name "is stuck into a table column that is like 15% of the width ... That's just never
    going to work"). Under the name, the figures that are compared — the reading, the pace line and the target —
    stand in one right-aligned column, and the chart of the same number sits beside them at the same size in every
    row. Its guards follow under it. Sami chose rows over cards at 04:28Z
    (dispatch://INFRA-202/ask/33247edc-a856-4589-9c2c-42d200f10a2a)."""
    n, p = k.num, k.label
    cls = LABEL_CLASS[p.label]
    name = kpi_name(n)
    # Why the verdict is Can't tell, unless there is no reading at all and the figures have already said so.
    why = f'<p class="kwhy">{e(p.reason)}</p>' if p.label == CANT_TELL and p.reason and k.readings else ""
    guards = guards_block(k.guards, now)
    return (
        f'<div class="kr {cls}">'
        f'<div class="k-head"><h3 class="kn" title="{e(n.name)}">{e(name)} {label_html(p)}</h3></div>'
        f'<div class="k-figs">{now_value(k.readings, n.unit, now)}{pace_value(k)}{target_value(k, now)}{why}</div>'
        + (f"<div class=\"c-trend\">{chart(k, now, cls, f'{name}: readings, the line to the target and where it says the number should be')}</div>"
           if k.readings else "")
        + (f'<div class="c-guards">{guards}</div>' if guards else "")
        + "</div>"
    )


def kpi_rows(v: StreamView, now: datetime) -> str:
    if v.st.board is None:
        why = v.st.board_error or f"{v.st.issue} has no scoreboard.json yet"
        return f'<p class="empty kpad">No scoreboard: {e(why)}</p>'
    rows = "".join(kpi_row(k, now) for k in v.kpis) or '<p class="empty">No main number defined.</p>'
    loose = guards_block(v.loose_guards, now, "Guarding no main number: ")
    return f'<div class="kpis">{rows}</div>' + (f'<div class="kpad">{loose}</div>' if loose else "")


def behind_strip(page: PageV2) -> str:
    """Every main number behind its line, across the streams: the one list to read first."""
    if not page.behind:
        return '<section class="behind none small" id="behind">No main number is behind its line.</section>'
    items = []
    for v, k in page.behind:
        n = k.num
        gap = f"{amount(abs(k.gap), n.unit)} behind" if k.gap is not None else ""
        latest = num(k.readings[-1][1], n.unit) if k.readings else "–"
        items.append(
            f'<li><a href="#s-{e(v.st.slug)}"><b>{e(v.st.name)}</b> · {e(kpi_name(n))}</a> '
            f'<span class="bv">{e(latest)}</span> <span class="bad">{e(gap)}</span> '
            f'<span class="dim">{e(target_text(n, k.label))}</span></li>'
        )
    return (f'<section class="behind" id="behind"><h2 class="fl">Behind their line ({len(page.behind)})</h2>'
            f'<ul>{"".join(items)}</ul></section>')


# ------------------------------------------------------------------------------------------------- plan


def mark(kind: str, title: str = "") -> str:
    t = f' title="{e(title)}"' if title else ""
    return f'<span class="mk {kind}"{t}></span>'


def issue_cells(line: PlanLine) -> str:
    """The item's Dispatch issue with its priority and status, and its owner linked to the owner's live conversation
    in Dispatch; an item with no issue says so."""
    ref = line.issue
    if ref is None:
        return '<span class="iss none">No Dispatch issue</span>'
    key = f'<a class="ik" href="{e(ref.url)}" target="_blank" rel="noopener" title="{e(ref.title or ref.key)} ({e(ref.how)})">{e(ref.key)}</a>'
    if ref.error:
        return f'<span class="iss">{key} <span class="bad">could not be read</span></span><span class="own dim">owner unknown</span>'
    prio = f'<span class="pri p{ref.priority if isinstance(ref.priority, int) else "x"}">{e(ref.priority_text)}</span>'
    iss = f'<span class="iss">{key} {prio} <span class="ist">{e(ref.status_text)}</span></span>'
    if ref.owner_url:
        state = "running" if ref.owner_running else "not running"
        own = (f'<a class="own" href="{e(ref.owner_url)}" target="_blank" rel="noopener" '
               f'title="Open the live conversation of {e(ref.owner)} in Dispatch ({state})">'
               f'<span class="ld{" on" if ref.owner_running else ""}"></span>{e(ref.owner)} ›</a>')
    elif ref.owner:
        own = f'<span class="own">{e(ref.owner)}</span>'
    else:
        own = '<span class="own dim">no owner</span>'
    return iss + own


def pr_cells(line: PlanLine) -> str:
    """The pull request as repository#number, and its author."""
    pr = line.item.pr
    who = f'<span class="who">{e(line.author)}</span>' if line.author else ""
    if not pr:
        return f'<span class="pr dim">no pull request</span>{who}'
    ref = parse_ref(pr)
    text = f"{ref[1]}#{ref[2]}" if ref else pr
    link = (
        f'<a href="{e(line.url)}" target="_blank" rel="noopener" title="{e(pr)}">{e(text)}</a>'
        if line.url
        else f'<span title="{e(pr)}">{e(text)}</span>'
    )
    return f'<span class="pr">{link}</span>{who}'


def plan_item(line: PlanLine, now: datetime) -> str:
    """Two lines: the mark, what it is and its state on GitHub; under them its Dispatch issue, priority, status and
    owner, then its pull request and author."""
    it = line.item
    tags = ""
    if it.example:
        tags += '<span class="tag demo">Example</span>'
    if not it.planned:
        tags += f'<span class="tag">added {e(clock(it.added_at, now))}</span>'
    if it.removed:
        tags += '<span class="tag">removed</span>'
    state_cls = {MERGED: "ok", FAILING: "bad"}.get(line.kind, "dim")
    cls = "pi" + (" removed" if it.removed else "") + ("" if line.issue else " noissue")
    return (
        f'<li class="{cls}">{mark(line.kind)}'
        f'<span class="pw">{e(it.what)}{tags}</span>'
        f'<span class="ps {state_cls}">{e(line.state)}</span>'
        f'<span class="pm">{issue_cells(line)}{pr_cells(line)}</span></li>'
    )


def kinds_text(lines: list[PlanLine]) -> str:
    merged = sum(1 for ln in lines if ln.kind == MERGED)
    failing = sum(1 for ln in lines if ln.kind == FAILING)
    parts = [f"{merged} merged"] if merged else []
    parts += [f"{failing} CI failing"] if failing else []
    rest = len(lines) - merged - failing
    parts += [f"{rest} open"] if rest else []
    return ", ".join(parts)


def plan_status(v: StreamView) -> str:
    p = v.plan
    if p.before_fix:
        return '<span class="pstat dim">fixes at 09:00</span>' if p.fixed else '<span class="pstat dim">due at 09:00</span>'
    if not p.fixed:
        return '<span class="pstat bad">No plan</span>'
    head = f"{v.merged} of {v.planned} merged"
    late = f' <span class="pstat dim">set late, {e(p.late_at.astimezone(PACIFIC).strftime("%-I:%M %p"))}</span>' if p.late_at else ""
    return f'<span class="pstat">{e(head)}</span>{late}'


def history(v: StreamView) -> str:
    cells = []
    for d, rec in v.history:
        val = f"{rec[0]}/{rec[1]}" if rec else "–"
        title = f"{d.strftime('%a %b %-d')}: " + (f"{rec[0]} of {rec[1]} planned merged" if rec else "no plan")
        cells.append(f'<span class="hd{"" if rec else " none"}" title="{e(title)}"><b>{e(d.strftime("%a"))}</b> {e(val)}</span>')
    return f'<div class="hist"><span class="hl">Last 7 days</span>{"".join(cells)}</div>'


def plan_section(v: StreamView, now: datetime) -> str:
    planned = [ln for ln in v.lines if ln.item.planned]
    added = [ln for ln in v.lines if not ln.item.planned]
    rows = "".join(plan_item(ln, now) for ln in planned)
    if not rows and not added:
        rows = '<li class="pi none"><span class="dim">Nothing listed for today.</span></li>'
    more = ""
    if added:
        # Items added after 09:00 do not count; they stay one tap away so the planned list reads first.
        marks = "".join(mark(ln.kind) for ln in added)
        more = (
            f'<details class="added" id="added-{e(v.st.slug)}"><summary><span class="marks">{marks}</span>'
            f"<span>{len(added)} added after the plan was set, not counted: {e(kinds_text(added))}</span></summary>"
            f'<ul class="pl">{"".join(plan_item(ln, now) for ln in added)}</ul></details>'
        )
    ul = f'<ul class="pl">{rows}</ul>' if rows else ""
    return (
        f'<div class="plan"><div class="sec-h"><span class="sec-t">Today\'s plan</span>{plan_status(v)}</div>'
        f"{ul}{more}{history(v)}</div>"
    )


def week_section(v: StreamView, now: datetime) -> str:
    """This week's plan: one line per item with its due day, state, Dispatch issue and owner; or that the lead has
    not set one (the `week` field, /mnt/nvme/sami/chief/design/scoreboard-week-contract.md)."""
    w = v.week
    if w is None:
        return ""
    label = f"week of {w.start:%b %-d}"
    if not w.items:
        return (f'<div class="plan week"><div class="sec-h"><span class="sec-t">This week\'s plan</span>'
                f'<span class="pstat bad">No plan</span><span class="dim small">{e(label)}: the scoreboard has no '
                "`week` list for this week</span></div></div>")
    merged = sum(1 for ln in v.week_lines if ln.kind == MERGED)
    rows = "".join(week_item(ln, now) for ln in sorted(v.week_lines, key=lambda ln: (ln.item.due is None, ln.item.due)))
    return (
        f'<div class="plan week"><div class="sec-h"><span class="sec-t">This week\'s plan</span>'
        f'<span class="pstat">{merged} of {len(v.week_lines)} merged</span>'
        f'<span class="dim small">{e(label)} · set {e(clock(w.set_at, now))}</span></div>'
        f'<ul class="pl">{rows}</ul></div>'
    )


def week_item(line: PlanLine, now: datetime) -> str:
    due = line.item.due
    tag = f'<span class="tag due">due {e(due.strftime("%a %b %-d"))}</span>' if due else ""
    return plan_item(line, now).replace('<span class="pw">', f'<span class="pw">{tag}', 1)


# ------------------------------------------------------------------------------------------------- stream


def header_marks(v: StreamView) -> str:
    marks = "".join(mark(ln.kind, f"{ln.item.what}: {ln.state}") for ln in v.lines if ln.item.planned)
    if v.plan.before_fix:
        text = "plan due 09:00" if not v.plan.fixed else f"{v.planned} planned"
    elif not v.plan.fixed:
        text = "no plan"
    else:
        text = f"{v.merged} of {v.planned} merged"
    return f'<span class="hm">{f"<span class=marks>{marks}</span>" if marks else ""}<span>{e(text)}</span></span>'


def first_sentence(text: str) -> str:
    """The bottleneck's first sentence, in full; the format checker is what holds it to 200 characters."""
    ss = model.sentences(text)
    return ss[0] if ss else text


def footer(v: StreamView, page: PageV2) -> str:
    st = v.st
    b = (st.board or {}).get("bottleneck") if isinstance((st.board or {}).get("bottleneck"), dict) else {}
    bott = (
        f'<p class="bott"><span class="sec-t">Bottleneck</span>{render.linkify(first_sentence(str(b["text"])), page.web)}</p>'
        if b.get("text")
        else '<p class="bott"><span class="sec-t">Bottleneck</span><span class="dim">none named</span></p>'
    )
    links = []
    if st.issue:
        links.append(f'<a href="{e(page.web)}/issues/{e(st.issue)}" target="_blank" rel="noopener">{e(st.issue)}</a>')
        links.append(f'<a href="{e(page.web)}{e(v.board_link)}" target="_blank" rel="noopener">Scoreboard</a>')
    return f'<div class="foot">{bott}<div class="links">{" · ".join(links)}</div></div>'


def video_option(vid: model.Video, now: datetime) -> str:
    """One line of the stream's dropdown: the day it was made, its title, its length."""
    day = f"{vid.uploaded.astimezone(PACIFIC):%b %-d}" if vid.uploaded else "date unknown"
    label = f"{day} · {vid.title} · {render.length_text(vid.row.get('seconds'))}"
    return f'<option value="/video/{e(vid.row["sha"])}.mp4">{e(label)}</option>'


def player(st, rows: list[dict], now: datetime) -> str:
    """The stream's own videos, newest first in a dropdown over one player; choosing one swaps the source in
    place. The block is kept across live updates (`data-keep`) until its set of videos changes and nothing in it
    plays, so an update never restarts a video."""
    vids = [x for x in model.stream_videos(rows, st.issue or st.slug, st.name) if x.row.get("sha")]
    if not vids:
        return '<div class="vid"><p class="dim small">No explainer video for this stream yet.</p></div>'
    pid = f"v-{st.slug}"
    options = "".join(video_option(x, now) for x in vids)
    key = ",".join(x.row["sha"][:12] for x in vids)
    return (
        f'<div class="vid" id="vid-{e(st.slug)}" data-keep="1" data-key="{e(key)}">'
        f'<select class="vpick" data-player="{e(pid)}" '
        f'aria-label="Which {e(st.name)} video to play">{options}</select>'
        f'<video id="{e(pid)}" class="vplay" controls playsinline preload="metadata" '
        f'src="/video/{e(vids[0].row["sha"])}.mp4"></video></div>'
    )


CHIEF_SESSION = "01a13dca-e979-7264-af32-f654ef57a6f5"  # the chief of staff's session


def live_link(web: str, session: str, who: str, running: bool, label: str) -> str:
    """A link into a session's live conversation in Dispatch (`/agents/<session>/live`, the route Dispatch's web app
    links a running session to), with a dot for whether the session runs now."""
    state = "running now" if running else "not running now"
    return (f'<a class="live-link" href="{e(live_url(web, session))}" target="_blank" rel="noopener" '
            f'title="{e(who)}: {state}"><span class="ld{" on" if running else ""}"></span>{e(label)} ›</a>'
            f'<span class="dim small">{e(who)} · {state}</span>')


def lead_panel(st, page: PageV2, notes: list[dict], can_send: bool) -> str:
    """Beside the player: a note to the lead that posts without reloading the page, so the video keeps playing
    (Sami, 2026-10-26: "You took away my ability to send a message to the workstream lead while watching the video"),
    and under it a link into the lead's live conversation in Dispatch."""
    if not st.lead_session:
        return '<div class="notep"><p class="dim small">No lead session named, so a note has nowhere to go.</p></div>'
    disabled = "" if can_send else " disabled"
    why = "" if can_send else '<span class="dim small"> Sending needs you signed in to the tailnet as Sami.</span>'
    past = []
    for n in notes[-2:][::-1]:
        state = n.get("delivery", "pending")
        past.append(f'<li><span class="dim">{e(render.when(model.parse_time(n.get("at")), page.now))}</span> '
                    f'{e(model.clip(n.get("text", ""), 90))} '
                    f'<span class="{"ok" if state == "delivered" else "bad"}">{e(state)}</span></li>')
    hist = f'<ul class="notes small">{"".join(past)}</ul>' if past else ""
    running = st.lead_session in page.agents
    return (
        '<div class="notep">'
        '<form class="note-form" method="post" action="/note" data-async="1">'
        f'<input type="hidden" name="stream" value="{e(st.slug)}"><input type="hidden" name="back" value="/">'
        f'<textarea name="text" id="note-{e(st.slug)}" rows="6" maxlength="{MAX_NOTE}" required '
        f'placeholder="A note to {e(st.lead_title or "the lead")}, as a message from you"></textarea>'
        f'<div><button type="submit"{disabled}>Send to the {e(st.name)} lead</button>'
        f'<span class="notemsg small"></span>{why}</div></form>{hist}'
        f'<div class="cp-live">{live_link(page.web, st.lead_session, st.lead_title, running, "Open the conversation in Dispatch")}</div>'
        "</div>"
    )


ASK_STATES = {"needs-you": ("needs you", "bad"), "running": ("in progress", "grey"), "not-done": ("not done", "bad"),
              "done": ("done", "ok")}


def asks_shown(ledger: list[dict] | None, now: datetime) -> list[dict]:
    """The asks ledger's entries that are not done, plus those done in the last 48 hours, newest first."""
    def at(a: dict) -> datetime | None:
        return model.parse_time(a.get("at") or a.get("asked"))
    rows = [a for a in ledger or [] if isinstance(a, dict)]
    keep = [a for a in rows if a.get("state") != "done" or ((t := at(a)) and now - t <= timedelta(hours=48))]
    return sorted(keep, key=lambda a: str(a.get("at") or a.get("asked") or ""), reverse=True)


def ask_line(a: dict) -> str:
    """One request: its title, its state, the date it was asked in US Pacific, one link to its result, and one line on
    what exists and what is left (`status`)."""
    word, cls = ASK_STATES.get(str(a.get("state")), (str(a.get("state") or "unknown"), "grey"))
    t = model.parse_time(a.get("at") or a.get("asked"))
    date = f' <span class="dim small">{e(t.astimezone(PACIFIC).strftime("%b %-d"))}</span>' if t else ""
    title, url = a.get("title"), a.get("answer_url")
    status = f'<br><span class="small">{e(str(a["status"]))}</span>' if a.get("status") else ""
    if title and isinstance(url, str) and url.startswith("https://"):
        return f'<li>{e(title)} <span class="lbl {cls}">{e(word)}</span>{date} <a href="{e(url)}">result</a>{status}</li>'
    return f'<li>{e(title or a.get("id", ""))} <span class="lbl {cls}">{e(word)}</span>{date}{status}</li>'


def asks_list(ledger: list[dict] | None, now: datetime) -> str:
    if ledger is None:
        return '<p class="dim small">The list of your requests could not be read.</p>'
    rows = asks_shown(ledger, now)
    items = "".join(ask_line(a) for a in rows) or '<li class="dim">Nothing open, nothing done in the last 48 hours.</li>'
    return f'<div class="cp-asks"><h3 class="fl">What you asked me ({len(rows)})</h3><ul class="asks">{items}</ul></div>'


def chief_panel(page: PageV2, notes: list[dict], can_send: bool, running: bool, session: str = CHIEF_SESSION,
                ledger: list[dict] | None = None) -> str:
    """Talk to the chief of staff: a note box that sends over Envoy, and a link into its live conversation in
    Dispatch; then what Sami asked it, one line each with a link to the answer in Dispatch (no answer text here)."""
    disabled = "" if can_send else " disabled"
    why = "" if can_send else '<span class="dim small"> Sending needs you signed in to the tailnet as Sami.</span>'
    past = "".join(
        f'<li><span class="dim">{e(render.when(model.parse_time(n.get("at")), page.now))}</span> '
        f'{e(model.clip(n.get("text", ""), 80))} <span class="{"ok" if n.get("delivery") == "delivered" else "bad"}">'
        f'{e(n.get("delivery", "pending"))}</span></li>' for n in notes[-2:][::-1])
    return (
        '<section class="chief" id="chief"><h2 class="fl">Chief of staff</h2>'
        '<form class="note-form" method="post" action="/note" data-async="1">'
        '<input type="hidden" name="stream" value="chief"><input type="hidden" name="back" value="/">'
        f'<textarea name="text" id="note-chief" rows="6" maxlength="{MAX_NOTE}" required '
        'placeholder="A message to your chief of staff"></textarea>'
        f'<div><button type="submit"{disabled}>Send to the chief of staff</button><span class="notemsg small"></span>{why}</div>'
        f'</form>{f"<ul class=notes>{past}</ul>" if past else ""}'
        f'<div class="cp-live">{live_link(page.web, session, "Chief of staff", running, "Open the chief of staff in Dispatch")}</div>'
        f"{asks_list(ledger, page.now)}</section>"
    )


def board_links(st, web: str) -> str:
    """The scoreboard's optional `links` ([{label, url}], url a dispatch:// reference or https), shown in the stream's
    header so a lead can point Sami at its results page (Verified Linux, 2026-10-26: "Eval results (LINUX-29)")."""
    out = []
    for ln in (st.board or {}).get("links") or []:
        if not isinstance(ln, dict) or not ln.get("label") or not isinstance(ln.get("url"), str):
            continue
        url = ln["url"]
        if url.startswith("dispatch://"):
            url = render._dispatch_url(url, web)
        elif not url.startswith("https://"):
            continue
        out.append(f'<a class="blink" href="{e(url)}" target="_blank" rel="noopener">{e(ln["label"])} ›</a>')
    return "".join(out)


def stream_section(v: StreamView, page: PageV2, rows: list[dict], notes: list[dict] | None = None,
                   can_send: bool = False) -> str:
    """The header line, the main numbers as rows, and under them the stream's video player beside the note to its lead, both
    always open, then the plan, the bottleneck and the last 7 days, folded: the header's merged marks summarise the
    plan (Sami, 2026-10-26: "Today's Plan section is always expanded, which is really annoying")."""
    st = v.st
    # "Decisions", not "asks": these are Dispatch decisions waiting on him, counted and linked to his Dispatch Inbox.
    asks = (
        f'<a class="asks" href="{e(page.inbox)}" target="_blank" rel="noopener">'
        f'{v.asks} decision{"s wait" if v.asks != 1 else " waits"} on you</a>'
        if v.asks
        else '<span class="asks none">no decisions waiting on you</span>'
    )
    studio = f'<div class="studio">{player(st, rows, page.now)}{lead_panel(st, page, notes or [], can_send)}</div>'
    rest = (
        f'<details class="srest" id="rest-{e(st.slug)}">'
        "<summary>Today's plan, this week's plan, bottleneck, last 7 days</summary>"
        f"{plan_section(v, page.now)}{week_section(v, page.now)}{footer(v, page)}</details>"
    )
    return (
        f'<section class="stream" id="s-{e(st.slug)}">'
        f'<div class="sh"><div class="sh-name"><h2>{e(st.name)}</h2><span class="lead">{e(st.lead_title)}</span></div>'
        f'<div class="sh-right">{board_links(st, page.web)}{asks}{header_marks(v)}</div></div>'
        f"{kpi_rows(v, page.now)}{studio}{rest}"
        "</section>"
    )


def top_line(page: PageV2) -> str:
    now = page.now.astimezone(PACIFIC)
    k = page.kpis
    total = sum(k.values())
    read = clock(page.read_at, page.now) if page.read_at else "not yet"
    gh = f" · GitHub {clock(page.prs_read_at, page.now)}" if page.prs_read_at else ""
    ex = ' · <b class="exnote">Example plans shown</b> <a href="/v2">hide</a>' if page.example else ""
    return (
        '<header class="top">'
        f'<div class="when"><h1>Streams</h1><span class="date">{e(now.strftime("%A, %b %-d · %H:%M"))} PT</span>'
        f'<span class="read">Read <span data-read-at="{e(page.read_at.isoformat() if page.read_at else "")}">'
        f"{e(read)}</span>{e(gh)}{ex}</span>"
        '<span class="read"><a href="/standup">Standup room &rsaquo;</a></span></div>'
        # Three tiles of one shape: a label of words, then the figures. "Behind" links to the list of which ones.
        '<div class="facts">'
        f'<div class="fact kpi"><span class="fl">Main numbers ({total})</span><span class="fv">'
        f'<span class="n ok">{k[ON_PACE]}</span> on pace '
        f'<a href="#behind"><span class="n bad">{k[BEHIND]}</span> behind ›</a> '
        f'<span class="n grey">{k[CANT_TELL]}</span> can\'t tell</span></div>'
        f'<div class="fact"><span class="fl">Merged today</span><span class="fv"><span class="n">{page.merged}</span> of '
        f"{page.planned} planned</span></div>"
        f"{decisions_fact(page)}"
        "</div></header>"
    )


def decisions_fact(page: PageV2) -> str:
    """The header's Decisions: how many Dispatch decisions wait on Sami, linked to his Dispatch Inbox, where he reads
    and answers them. The page lists none of them: Dispatch is where they live."""
    return (
        f'<div class="fact"><span class="fl">Decisions</span>'
        f'<a class="fv" href="{e(page.inbox)}" target="_blank" rel="noopener"><span class="n">{page.asks}</span> '
        f"wait on you ›</a></div>"
    )


ANSWER_MARK = {"ADOPT": ("ok", "✓ adopted"), "NOT ADOPT": ("bad", "✗ not adopted")}


def loop_section(rnd: rounds.Round | None) -> str:
    """The learning loop's newest round, short: each change sent to other streams with its numbers and each
    recipient's answer, the leads that missed the round, and links to the round's files."""
    if rnd is None:
        return '<p class="loop small dim" id="loop">Learning loop: no round recorded yet.</p>'
    files = [(rnd.name, "round"), (rnd.adoptions_name, "replies"), (rnd.inputs_name, "inputs"),
             (rnd.redteam_name, "red-team")]
    links = " · ".join(f'<a href="/rounds/{e(n)}" target="_blank" rel="noopener">{e(t)}</a>' for n, t in files if n)
    when = re.search(r"\d{4}-\d\d-\d\d \d\d:\d\dZ", rnd.title)
    label = when.group(0) if when else rnd.name.removesuffix(".md")
    summary = (f'<summary><span class="fl">Learning loop</span> round {e(label)}: '
               f'{len(rnd.changes)} change{"s" if len(rnd.changes) != 1 else ""} sent to other streams</summary>')
    head = f'<p class="small">{e(rnd.final_title or "not final yet")} · {links}</p>'
    missed = f'<p class="small"><b>Missed the round:</b> {e(rnd.missed)}</p>' if rnd.missed else ""
    if not rnd.final:
        return (f'<details class="loop" id="loop">{summary}{head}{missed}'
                '<p class="small dim">No FINAL section yet.</p></details>')
    rows = []
    for c in rnd.changes:
        name, _, detail = c.text.partition(":")
        to = []
        for lead in c.to:
            cls, word = ANSWER_MARK.get(rnd.answer(lead, c.key) or "", ("dim", "no reply"))
            to.append(f'{e(lead)} <span class="{cls}">{e(word)}</span>')
        rows.append(f'<li><b>{e(c.key)}. {e(name)}</b> <span class="small">{e(detail.strip())}</span>'
                    f'<div class="small">Sent to: {", ".join(to) or "nobody named"}</div></li>')
    keys = {c.key for c in rnd.changes}
    other = []
    for r in rnd.replies:
        extra = {k: v for k, v in r.answers.items() if k not in keys}
        if extra:
            other.append(f"{e(r.lead)}: " + ", ".join(
                f'{e(k)} <span class="{ANSWER_MARK[v][0]}">{e(ANSWER_MARK[v][1])}</span>' for k, v in extra.items()))
    extra_line = f'<p class="small">Answers to draft items: {"; ".join(other)}</p>' if other else ""
    silent = rnd.silent()
    silent_line = f'<p class="small bad">No reply yet: {e(", ".join(silent))}</p>' if silent else ""
    changes = f'<ul class="loopc">{"".join(rows)}</ul>' if rows else '<p class="small dim">No change was sent.</p>'
    return f'<details class="loop" id="loop">{summary}{head}{missed}{changes}{extra_line}{silent_line}</details>'


def body(page: PageV2, videos: dict, notice=None, notice_error=False, chief: str = "", loop: str = "",
         notes: dict | None = None, can_send: bool = False) -> str:
    """`videos` is each stream issue's videos, by issue key, for the stream's own player; `notes` each stream's notes
    to its lead, by slug. `chief` is the chief-of-staff panel; the streams follow, in rank order, under one line that
    says so."""
    errs = "".join(f"<li>{e(x)}</li>" for x in page.errors)
    err_box = f'<div class="errors"><b>Some data could not be read:</b><ul>{errs}</ul></div>' if errs else ""
    note = f'<div class="notice{" bad-bg" if notice_error else ""}">{e(notice)}</div>' if notice else ""
    notes = notes or {}
    streams = "".join(stream_section(v, page, videos.get(v.st.issue) or [], notes.get(v.st.slug, []), can_send)
                      for v in page.streams)
    n = len(page.streams)
    block = (
        f'<div id="streams"><p class="small dim streamnote">{n} stream{"s" if n != 1 else ""}, in rank order '
        "(the chief of staff&rsquo;s streams.json; an unranked stream sorts last). "
        # One key for every chart below, written once rather than beside each of them.
        "Every chart runs from the number&rsquo;s first reading to now: the solid line is the readings (an open "
        "dot and a dashed step where a reading is only a lower bound), the dashed line runs to the target, the "
        "hollow dot is where that line says today&rsquo;s reading should be, and the bar between it and the "
        "reading is the gap.</p>"
        f"{streams}</div>"
    )
    return f"{top_line(page)}{note}{err_box}{chief}{behind_strip(page)}{loop}{block}"


CSS = """
/* One type scale for the whole page: four sizes, two weights (400, 600). Every rule below uses these and nothing
   else (Sami, 2026-10-26: the cards had "a completely incoherent set of font sizes and weights"). */
:root{--fs-meta:12px;--fs-body:14px;--fs-title:17px;--fs-num:28px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:400 var(--fs-body)/1.45 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-text-size-adjust:100%}
main{max-width:1240px;margin:0 auto;padding:14px 24px 64px}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
b,strong{font-weight:600}
.dim{color:var(--dim)}.small{font-size:var(--fs-meta)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.grey{color:var(--grey)}
h1{font-size:20px;font-weight:600;margin:0}
h2{font-size:var(--fs-title);font-weight:600;margin:0}
.fl,.sec-t,.hl{font-size:var(--fs-meta);font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--dim)}
details>summary{cursor:pointer;color:var(--blue);font-size:var(--fs-body)}
/* header */
.top{display:flex;flex-direction:column;gap:10px;margin-bottom:16px}
.when{display:flex;align-items:baseline;gap:6px 14px;flex-wrap:wrap}
.date{font-weight:600}
.read{color:var(--dim);font-size:var(--fs-meta)}
.exnote{color:var(--ink)}
.facts{display:grid;grid-template-columns:1.6fr 1fr 1fr;gap:10px}
.fact{display:flex;flex-direction:column;gap:2px;background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:10px 14px;color:var(--ink)}
.fv{font-size:var(--fs-body);white-space:nowrap}
.fv .n{font-size:var(--fs-num);font-weight:600;line-height:1.1;font-variant-numeric:tabular-nums;margin-right:3px}
.fact.kpi .fv .n:not(:first-child){margin-left:10px}
.fact a.fv{color:var(--blue)}.fact a.fv .n{color:var(--ink)}
.errors{background:var(--badbg);color:var(--bad);padding:8px 12px;border-radius:8px;margin:0 0 12px}
.errors ul{margin:4px 0 0 18px;padding:0}
.notice{background:var(--okbg);color:var(--ok);padding:8px 12px;border-radius:8px;margin:0 0 12px}
.notice.bad-bg{background:var(--badbg);color:var(--bad)}
/* the strips under the header: chief of staff, behind its line, learning loop */
.chief,.behind,.loop{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--grey);
border-radius:10px;padding:9px 14px;margin:0 0 12px}
.chief{border-left-color:var(--blue);display:flex;flex-direction:column;gap:8px}
.chief h2,.behind h2{margin:0 0 4px}
.cp-talk,.notep{display:flex;flex-direction:column;gap:8px;min-width:0}
.cp-live{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px}
.cp-asks h3{margin:0 0 4px}
.asks{list-style:none;margin:0;padding:0;font-size:var(--fs-meta);max-height:16em;overflow-y:auto}
.asks li{padding:3px 0;border-top:1px dashed var(--line)}.asks li:first-child{border-top:0}
.live-link{display:inline-flex;align-items:center;font-weight:600;padding:5px 12px;border:1px solid var(--blue);
border-radius:6px;max-width:100%}
.live-link:hover{background:color-mix(in srgb,var(--blue) 8%,var(--card));text-decoration:none}
.ld{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--grey);margin-right:6px}
.ld.on{background:var(--ok)}
.behind{border-left-color:var(--bad)}
.behind.none{border-left-color:var(--ok);color:var(--dim)}
.behind ul{margin:0;padding:0;list-style:none;display:grid;gap:3px}
.behind .bv{font-weight:600;font-variant-numeric:tabular-nums;margin-left:4px}
.loop>summary{color:var(--ink)}
.loop>summary .fl{margin-right:6px}
.loop p{margin:4px 0}
.loopc{list-style:none;margin:4px 0;padding:0}
.loopc li{padding:5px 0;border-top:1px solid var(--line)}
.loopc li:first-child{border-top:0}
/* the line above the streams */
.streamnote{margin:0 0 8px}
/* a stream */
.stream{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:0 0 18px;overflow:hidden;
scroll-margin-top:10px}
.sh{display:flex;justify-content:space-between;align-items:center;gap:8px 16px;flex-wrap:wrap;padding:12px 18px;
border-bottom:1px solid var(--line)}
.sh-name{display:flex;align-items:baseline;gap:4px 10px;flex-wrap:wrap;min-width:0}
.lead{color:var(--dim)}
.sh-right{display:flex;align-items:center;gap:6px 16px;flex-wrap:wrap}
a.asks{font-weight:600}
a.blink{font-weight:600}
.asks.none{color:var(--dim)}
.hm{display:inline-flex;align-items:center;gap:8px;white-space:nowrap;font-variant-numeric:tabular-nums}
.marks{display:inline-flex;gap:3px;flex-wrap:wrap}
.mk{display:inline-block;flex:none;width:11px;height:11px;border-radius:50%;border:1.6px solid var(--grey);
background:transparent}
.mk.merged{background:var(--ok);border-color:var(--ok)}
.mk.failing{background:var(--bad);border-color:var(--bad)}
/* Main numbers: one row each. The name is a full-width line of ordinary text with the verdict at its end; under it
   the three figures that are compared (reading, pace line, target) in one right-aligned tabular column, and the
   chart beside them at one size for every row. The verdict colours only the row's left mark, its pill and its gap
   words: every other word stays ink or dim. */
.kpis{padding:4px 18px 8px}
.kpad{padding:0 18px 12px}
.kr{display:grid;grid-template-columns:minmax(320px,1fr) minmax(300px,500px);
grid-template-areas:"head head" "figs trend";gap:6px 24px;align-items:start;padding:14px 0 14px 12px;
border-top:1px solid var(--line);border-left:4px solid var(--grey)}
.kr:first-child{border-top:0}
.kr.ok,.kr.bad,.kr.grey,.kg.ok,.kg.bad,.kg.grey{color:var(--ink)}
.kr.ok{border-left-color:var(--ok)}.kr.bad{border-left-color:var(--bad)}
/* The verdict reads as the last word of the name, so it is never far from what it judges and never squeezes the
   name into a narrow column on a phone. */
.k-head{grid-area:head;min-width:0}
.kn{font-size:var(--fs-body);font-weight:600;margin:0;min-width:0;overflow-wrap:break-word}
.kn .lbl{margin-left:4px;position:relative;top:-1px}
.k-figs{grid-area:figs;display:grid;grid-template-columns:72px minmax(56px,auto) minmax(0,1fr);gap:2px 10px;
align-items:baseline;min-width:0}
.fig{display:contents}
.fig-k{font-size:var(--fs-meta);color:var(--dim)}
.fig-v{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.fig-v .n{font-weight:600}
.fig-v .pre{font-size:var(--fs-meta);color:var(--dim);margin-right:3px}
.fig.now .fig-v .n{font-size:var(--fs-num);line-height:1.05}
.fig-q{color:var(--dim);min-width:0;overflow-wrap:break-word}
.gapw{color:var(--dim)}.kr.bad .gapw{color:var(--bad)}.kr.ok .gapw{color:var(--ok)}
.kwhy{grid-column:3/-1;margin:2px 0 0;color:var(--dim)}
.c-trend{grid-area:trend;min-width:0;align-self:center}
.c-guards{grid-column:1/-1;padding-left:2px}
.lbl{display:inline-block;font-size:var(--fs-meta);font-weight:600;padding:1px 8px;border-radius:999px;white-space:nowrap}
.lbl.ok{background:var(--okbg);color:var(--ok)}
.lbl.bad{background:var(--badbg);color:var(--bad)}
.lbl.grey{background:var(--greybg);color:var(--grey)}
/* The chart is drawn at the size it is read at, so its labels are the page's small size rather than a scaled copy
   of it; --ct is what that costs where the column is narrower than the drawing. */
.kchart{display:block;width:100%;max-width:500px;height:auto;--ct:12px}
.kchart text{font-size:var(--ct);fill:var(--dim);font-variant-numeric:tabular-nums}
.kchart .ax{stroke:var(--line);stroke-width:1}
.kchart .tg{stroke:var(--grey);stroke-width:1;stroke-dasharray:2 3}
.kchart .tgl,.kchart .pll{font-weight:600;fill:var(--ink)}
.kchart .pll{fill:var(--grey)}
.kchart .pl{stroke:var(--grey);stroke-width:1.4;stroke-dasharray:5 4}
.kchart .rl{stroke:var(--ink);stroke-width:1.8;opacity:.75}.kchart .rl.b{stroke-dasharray:3 3}
.kchart .gap{stroke-width:5;opacity:.25}.kchart .gap.bad{stroke:var(--bad)}.kchart .gap.ok{stroke:var(--ok)}.kchart .gap.grey{stroke:var(--grey)}
.kchart .exp{fill:var(--card);stroke:var(--grey);stroke-width:1.6}
.kchart .dot{fill:var(--ink);opacity:.6}
.kchart .dot.ok{fill:var(--ok);opacity:1}.kchart .dot.bad{fill:var(--bad);opacity:1}.kchart .dot.grey{fill:var(--grey);opacity:1}
.kchart .dot.open{fill:var(--card);stroke:var(--ink);stroke-width:1.4}.kchart .dot.open.ok{stroke:var(--ok)}.kchart .dot.open.bad{stroke:var(--bad)}.kchart .dot.open.grey{stroke:var(--grey)}
.kgs{list-style:none;margin:4px 0 0;padding:0;font-size:var(--fs-meta)}
.kg{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 12px;align-items:baseline;padding:3px 0;
border-top:1px dashed var(--line)}
.gv{white-space:nowrap;font-variant-numeric:tabular-nums}
.kg .lbl{padding:0 7px}
.kgd>summary{font-size:var(--fs-meta);color:var(--dim);margin-top:4px}
.empty{margin:10px 0;color:var(--dim)}
/* the studio: the stream's video player and the note to its lead, side by side, both always open */
.studio{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:14px;padding:10px 18px 14px;
border-top:1px solid var(--line);background:color-mix(in srgb,var(--bg) 55%,var(--card))}
.vid{display:flex;flex-direction:column;gap:6px;min-width:0}
.vpick{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);
color:var(--ink);max-width:100%}
.vplay{width:100%;max-height:46vh;background:#000;border-radius:8px}
.note-form{display:grid;gap:6px;max-width:720px}
/* A box you can write a paragraph in: six lines to start, taller as he types, and resizable by hand (vertically;
   a horizontal drag would break the column). */
textarea{width:100%;min-height:8.5em;resize:vertical;font:inherit;line-height:1.45;padding:8px;
border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
button{font:inherit;font-weight:600;background:var(--blue);color:#fff;border:0;border-radius:6px;padding:6px 14px;
cursor:pointer}
button[disabled]{opacity:.5;cursor:not-allowed}
button.small{padding:2px 8px;font-size:var(--fs-meta)}
.notemsg{margin-left:8px}
.notemsg.ok{color:var(--ok)}.notemsg.bad{color:var(--bad)}
.notes{font-size:var(--fs-meta);margin:0;padding-left:18px}
.inline{display:inline;margin-left:6px}
/* the folded part: plan, week, bottleneck, last 7 days */
.srest>summary{padding:9px 18px;border-top:1px solid var(--line)}
.plan{padding:12px 18px;border-top:1px solid var(--line);background:color-mix(in srgb,var(--bg) 55%,var(--card))}
.sec-h{display:flex;align-items:baseline;gap:4px 12px;flex-wrap:wrap;margin-bottom:4px}
.sec-t{margin-right:8px}
.pstat{font-weight:600}
.pl{list-style:none;margin:0;padding:0}
.pi{display:grid;grid-template-columns:14px minmax(0,1fr) auto;grid-template-areas:"mk what state" ". meta meta";
gap:2px 14px;align-items:baseline;padding:7px 0;border-top:1px solid var(--line)}
.pi:first-child{border-top:0}
.pi .mk{grid-area:mk;align-self:start;margin-top:4px}
.pi.none{display:block}
.pw{grid-area:what;min-width:0;overflow-wrap:break-word}
.pi.removed .pw{text-decoration:line-through;color:var(--dim)}
.pm{grid-area:meta;display:flex;flex-wrap:wrap;gap:3px 14px;align-items:baseline;font-size:var(--fs-meta)}
.iss{white-space:nowrap}
.iss.none{color:var(--warn,#a15c00);font-weight:600}
.ik,.own{font-weight:600}
.pri{display:inline-block;font-size:var(--fs-meta);font-weight:600;padding:0 6px;border-radius:4px;
border:1px solid var(--line);color:var(--dim)}
.pri.p0{background:var(--badbg);color:var(--bad);border-color:transparent}
.pri.p1{background:color-mix(in srgb,var(--badbg) 45%,var(--card));color:var(--ink);border-color:transparent}
.ist{color:var(--dim)}
.pr{min-width:0;overflow-wrap:anywhere}
.who{color:var(--dim);min-width:0;overflow-wrap:anywhere}
.ps{grid-area:state;text-align:right;font-size:var(--fs-meta);font-weight:600}
.ps.dim{font-weight:400}
.added{margin-top:4px}
.added>summary{display:flex;align-items:center;flex-wrap:wrap;gap:4px 8px;padding:6px 0}
.added>summary .marks{flex:0 1 auto;min-width:0}
.added[open]>summary{border-bottom:1px solid var(--line)}
.added .pw{color:var(--dim)}
.tag{display:inline-block;margin-left:8px;font-size:var(--fs-meta);font-weight:600;padding:0 6px;
border:1px solid var(--line);border-radius:4px;color:var(--dim);white-space:nowrap}
.tag.demo{border-style:dashed;color:var(--ink)}
.tag.due{margin:0 8px 0 0;color:var(--ink);font-variant-numeric:tabular-nums}
.hist{display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center;margin-top:8px;font-size:var(--fs-meta);
font-variant-numeric:tabular-nums}
.hl{margin-right:4px}
.hd{padding:1px 7px;border:1px solid var(--line);border-radius:4px;background:var(--card);white-space:nowrap}
.hd b{color:var(--dim);margin-right:3px}
.hd.none{color:var(--dim)}
.foot{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 18px;padding:11px 18px 12px;
border-top:1px solid var(--line);align-items:baseline}
.bott{margin:0;min-width:0}
.links{white-space:nowrap;text-align:right}
.foot .tell{grid-column:1/-1;margin:0}
@media (max-width:1100px){
/* The figures keep their column; the chart takes what is left, down to a width that still reads. */
.kr{grid-template-columns:minmax(260px,1fr) minmax(280px,500px);gap:6px 16px}
.k-figs{grid-template-columns:68px minmax(50px,auto) minmax(0,1fr)}
.ps{max-width:30vw}
}
@media (max-width:860px){
/* Too narrow for both: the chart goes full width under the figures, same shape as every other row, and back at
   about its own size. */
.kr{grid-template-columns:minmax(0,1fr);grid-template-areas:"head" "figs" "trend"}
}
@media (max-width:520px){
/* A phone is narrower than the chart's own drawing, so its labels are drawn larger to land at the page's size. */
.kchart{--ct:16px}
.k-figs{grid-template-columns:62px minmax(0,1fr);gap:0 8px}
.fig-q{grid-column:1/-1;padding:0 0 4px 62px}
.kwhy{grid-column:1/-1;padding-left:62px}
}
@media (max-width:720px){
:root{--fs-num:24px}
main{padding:10px 12px 56px}
/* Phone: the player first, the note field right under it, both on the screen together. */
.studio{grid-template-columns:1fr;gap:10px;padding:10px 12px 12px}
.vplay{max-height:34vh}
h1{font-size:18px}
.facts{grid-template-columns:1fr 1fr;gap:8px}
.fact{padding:8px 11px}
.fact.kpi{grid-column:1/-1}
.fv{white-space:normal}
.stream{border-radius:10px;margin-bottom:14px}
.sh{padding:11px 12px}
.kpis{padding:2px 12px 6px}
.kr{padding-left:10px}
.kpad{padding:0 12px 10px}
.plan{padding:10px 12px}
.ps{max-width:38vw}
.pm{gap:3px 10px}
.foot{grid-template-columns:1fr;padding:10px 12px}
.links{text-align:left;white-space:normal}
}
"""


# The page's only JavaScript of its own, for the two things a server render cannot do: swap the player's source
# when the dropdown changes, and post a note without reloading the page (which would stop the video and lose what
# he typed). The note POST is the same /note endpoint as the plain form; `Accept: application/json` asks it for a
# result instead of the redirect, so nothing about the form's own behaviour changes.
STUDIO_SCRIPT = """
(function(){
  document.addEventListener('change', ev => {
    const s = ev.target.closest('select.vpick'); if (!s) return;
    const v = document.getElementById(s.dataset.player); if (!v) return;
    v.src = s.value; v.play().catch(() => {});   // the note field beside it is untouched
  });
  document.addEventListener('submit', async ev => {
    const f = ev.target.closest('form[data-async]'); if (!f) return;
    ev.preventDefault();
    const msg = f.querySelector('.notemsg'), btn = f.querySelector('button[type=submit]');
    const box = f.querySelector('textarea');
    if (!box.value.trim()) { msg.className = 'notemsg small bad'; msg.textContent = 'the note is empty'; return; }
    msg.className = 'notemsg small dim'; msg.textContent = 'sending…'; btn.disabled = true;
    try {
      const r = await fetch('/note', {method:'POST', headers:{'Accept':'application/json',
        'Content-Type':'application/x-www-form-urlencoded'}, body:new URLSearchParams(new FormData(f))});
      let d; try { d = await r.json(); } catch (e) { d = {ok:false, message:'HTTP ' + r.status + ' ' + r.statusText}; }
      msg.className = 'notemsg small ' + (d.ok ? 'ok' : 'bad');
      msg.textContent = d.message || (d.ok ? 'sent' : 'not sent');
      if (d.ok) { box.value = ''; grow(box); }
    } catch (err) {
      msg.className = 'notemsg small bad'; msg.textContent = 'not sent: ' + err.message;
    }
    btn.disabled = false;
  });
  // The note box grows with what he writes, up to half the window, and stops growing for good the moment he drags
  // its own corner: from then on the size is his (Sami, 2026-10-26: "When I resize the input text box, it goes
  // back to being tiny"). The size lives in the box's inline style, which a live update never writes over.
  function sized(t){
    // A height this script did not set is one he set, whether he dragged the corner before typing or after.
    return !!t.style.height && (!t.dataset.autoh || Math.abs(parseFloat(t.dataset.autoh) - t.clientHeight) > 1);
  }
  function grow(t){
    if (t.dataset.sized) return;
    if (sized(t)) { t.dataset.sized = '1'; return; }
    t.style.height = 'auto';
    t.style.height = Math.min(t.scrollHeight + 2, Math.round(window.innerHeight * 0.5)) + 'px';
    t.dataset.autoh = t.clientHeight;
  }
  document.addEventListener('input', ev => { const t = ev.target.closest('textarea'); if (t) grow(t); });
  document.addEventListener('focusin', ev => { const t = ev.target.closest('textarea'); if (t) grow(t); });
})();
"""


def page_html(inner: str, title: str = "Streams", version: str = "") -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(title)}</title><style>{render.THEME}{CSS}{render.LIVE_CSS}</style></head><body>"
        f'<main id="page" data-version="{e(version)}">{inner}</main>{render.LIVE_PILL}'
        f"<script>const SINCE = {json.dumps('')};{render.SCRIPT}{STUDIO_SCRIPT}</script></body></html>"
    )
