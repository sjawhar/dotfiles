"""HTML for the page. Server-rendered; a small script re-fetches the body every minute and keeps what Sami typed
and which details he opened.

Layout: a link to Sami's Dispatch Inbox (where his decisions are, and are answered), then streams at a glance (each
row opens its detail in place) and what changed. On a laptop the streams sit right of what changed."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime

from . import model
from .model import Number, Page, Stream, ago, clip, fmt_value

STATUS_CLASS = {
    model.BEHIND: "behind",
    model.NOT_MEASURED: "unmeasured",
    model.NO_BOARD: "unmeasured",
    model.NO_TARGET: "notarget",
    model.BAD_TARGET: "notarget",
    model.BASELINE: "baseline",
    model.ON_TRACK: "ontrack",
    model.MET: "met",
}
CHANGES_SHOWN = 8

# The longest note either page accepts, in characters, and the one `maxlength` on every note box. The Envoy hub
# takes the whole note as one JSON message to NATS, whose server refuses a message past its max payload of
# 1,048,576 bytes with a 413 naming the size (packages/envoy/cmd/listener/api.go, its AGENTS.md "Listener API");
# the envelope's `payload_summary` is cut to 160 characters but the message itself is not. 20,000 characters is
# about 20 KB of UTF-8 plus the envelope, so the hub is nowhere near its limit (Sami, 2026-10-26: the box "has a
# limit that I find too low. It has cut me off twice tonight").
MAX_NOTE = 20_000

_LINK = re.compile(r"(https?://[^\s)<>\"';,]+|dispatch://[A-Z][A-Z0-9]*-\d+(?:/[a-z]+/[A-Za-z0-9._-]+)?)")


def e(s) -> str:
    return html.escape("" if s is None else str(s))


def linkify(text: str, web: str) -> str:
    """Escape `text` and turn URLs and dispatch:// references into links."""
    out, last = [], 0
    for m in _LINK.finditer(text or ""):
        out.append(e(text[last : m.start()]))
        ref = m.group(0).rstrip(".")
        url = ref if ref.startswith("http") else _dispatch_url(ref, web)
        out.append(f'<a href="{e(url)}" target="_blank" rel="noopener">{e(ref)}</a>')
        out.append(e(m.group(0)[len(ref) :]))
        last = m.end()
    out.append(e((text or "")[last:]))
    return "".join(out)


def _dispatch_url(ref: str, web: str) -> str:
    parts = ref[len("dispatch://") :].split("/")
    key = parts[0]
    if len(parts) >= 3 and parts[1] == "ask":
        return f"{web}/issues/{key}/asks/{parts[2]}"
    if len(parts) >= 3 and parts[1] == "artifact":
        return f"{web}/issues/{key}/artifacts/{parts[2]}"
    return f"{web}/issues/{key}"


def stamp(t: datetime, now: datetime) -> str:
    return t.strftime("%H:%MZ") if t.date() == now.date() else t.strftime("%b %-d %H:%MZ")


def when(t: datetime | None, now: datetime) -> str:
    """UTC time with its age: "03:28Z, 2 h ago", with the date when it is not today."""
    if t is None:
        return "never"
    return f"{stamp(t, now)}, {ago(t, now)}"


def chip(status: str | None) -> str:
    if not status:
        return ""
    return f'<span class="chip {STATUS_CLASS.get(status, "")}">{e(status)}</span>'


# ----------------------------------------------------------------------------------------------------- numbers


def target_text(n: Number) -> str:
    t = n.target or {}
    if not isinstance(t.get("value"), int | float):
        return f"no target · {'lower' if n.better == 'lower' else 'higher'} is better"
    sign = "≤" if n.better == "lower" else "≥"
    by = n.target_at.strftime("%b %-d") if n.target_at else str(t.get("by"))
    return f"target {sign} {fmt_value(t['value'], n.unit)} by {by}"


def latest_text(n: Number, now: datetime) -> str:
    v = n.latest
    if v is None:
        return '<span class="dim">no value yet</span>'
    sample = f", n={v['n']}" if v.get("n") is not None else ""
    note = f' <i class="dim">{e(clip(str(v["note"]), 90))}</i>' if v.get("note") else ""
    return f"<b>{e(fmt_value(v['value'], n.unit))}</b> <span class=dim>({e(when(v['_t'], now))}{e(sample)})</span>{note}"


def projection_text(n: Number) -> str:
    if n.status == model.MET:
        return "target met"
    if n.status == model.BASELINE:
        return "one measurement so far: no trend yet"
    if n.projection is not None and n.target_at is not None:
        k = len({v["_t"] for v in n.values})
        by = n.target_at.strftime("%b %-d")
        if n.projection < 0 <= min(float(v["value"]) for v in n.values):
            return f"a straight line through its {k} measurements falls to 0 before {by}"
        return f"a straight line through its {k} measurements reaches {fmt_value(round(n.projection, 2), n.unit)} by {by}"
    if n.status == model.BEHIND:
        return "target date passed"
    return ""


def spark(n: Number, w: int = 120, h: int = 30) -> str:
    """Values over time with the target as a dashed line and the projection to the target date dotted."""
    pts = [(v["_t"].timestamp(), float(v["value"])) for v in n.values]
    if len({p[0] for p in pts}) < 2:
        return ""  # one reading has no trend to draw
    t = n.target or {}
    tv = t.get("value") if isinstance(t.get("value"), int | float) else None
    x0 = pts[0][0]
    x1 = max([p[0] for p in pts] + ([n.target_at.timestamp()] if n.target_at and tv is not None else []))
    ys = [p[1] for p in pts] + ([float(tv)] if tv is not None else []) + ([n.projection] if n.projection is not None else [])
    y0, y1 = min(ys), max(ys)
    if y1 == y0:
        y0, y1 = y0 - 1, y1 + 1
    pad = 3

    def X(x):
        return pad + (w - 2 * pad) * (0.5 if x1 == x0 else (x - x0) / (x1 - x0))

    def Y(y):
        return h - pad - (h - 2 * pad) * (y - y0) / (y1 - y0)

    label = f"{n.short}: {len(pts)} values; dashed line: target; dotted: straight-line projection"
    parts = [f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img"><title>{e(label)}</title>']
    if tv is not None:
        parts.append(f'<line class="tgt" x1="{pad}" x2="{w - pad}" y1="{Y(tv):.1f}" y2="{Y(tv):.1f}"/>')
    if n.projection is not None and n.target_at is not None:
        lx, ly = pts[-1]
        parts.append(
            f'<line class="proj" x1="{X(lx):.1f}" y1="{Y(ly):.1f}" x2="{X(n.target_at.timestamp()):.1f}" '
            f'y2="{Y(n.projection):.1f}"/>'
        )
    if len(pts) > 1:
        path = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
        parts.append(f'<polyline class="val" points="{path}"/>')
    lx, ly = pts[-1]
    parts.append(f'<circle class="dot" cx="{X(lx):.1f}" cy="{Y(ly):.1f}" r="2.5"/>')
    parts.append("</svg>")
    return "".join(parts)


def guard_line(g: Number, now: datetime) -> str:
    worse = "rise" if g.better == "lower" else "fall"
    return f'<div class="guard">Must not {worse}: {e(g.short)}: {latest_text(g, now)}</div>'


def main_row(n: Number, guards: list[Number], now: datetime, web: str) -> str:
    if n.status == model.NOT_MEASURED:
        why = n.missing or "the scoreboard gives no value and no reason"
        detail = f'<div class="missing">Not measured: {linkify(why, web)}</div>'
    else:
        detail = f'<div class="dim">{e(projection_text(n))}</div>'
    g = "".join(guard_line(x, now) for x in guards if x.latest)
    return (
        '<li class="num">'
        f'<div class="num-head">{chip(n.status)} <span class="num-name" title="{e(n.name)}">{e(n.name.split(" (")[0])}</span></div>'
        f'<div class="num-body"><div>{latest_text(n, now)} <span class="dim">· {e(target_text(n))}</span></div>'
        f"{detail}{g}</div>"
        f'<div class="num-spark">{spark(n)}</div>'
        "</li>"
    )


def other_row(n: Number, now: datetime, web: str) -> str:
    if n.latest:
        body = latest_text(n, now)
    else:
        body = f'<span class="missing">never measured: {linkify(n.missing or "no reason given", web)}</span>'
    status = n.raw.get("validation_status")
    val = ""
    if status:
        cls = {"failed": "bad", "holding": "ok"}.get(status, "dim")
        val = f' <span class="{cls}">· checked against the main number: {e(status)}</span>'
    guards = n.raw.get("guards")
    guard = f' <span class="dim">· guards {e(guards)}</span>' if guards else ""
    return (
        f'<li><span class="role">{e({"fast": "fast check", "guard": "must not worsen"}.get(n.role, n.role))}</span> '
        f'<span title="{e(n.name)}">{e(n.short)}</span>: {body} '
        f'<span class="dim">· {e(target_text(n))}</span>{val}{guard}</li>'
    )


def history_table(n: Number, now: datetime, web: str) -> str:
    if not n.values:
        return ""
    rows = "".join(
        f"<tr><td>{e(when(v['_t'], now))}</td><td>{e(fmt_value(v['value'], n.unit))}</td>"
        f"<td>{e(v.get('n', ''))}</td><td>{linkify(str(v.get('source') or ''), web)}"
        f"{(' <i>' + e(v['note']) + '</i>') if v.get('note') else ''}</td></tr>"
        for v in reversed(n.values)
    )
    return (
        f'<div class="hist"><div class="hist-name">{e(n.name)}</div>'
        f"<table><tr><th>measured</th><th>value</th><th>n</th><th>source</th></tr>{rows}</table></div>"
    )


# ----------------------------------------------------------------------------------------------------- streams


def counts(st: Stream) -> str:
    if st.board is None:
        return chip(model.NO_BOARD)
    if not st.mains:
        return chip(model.NOT_MEASURED)
    c: dict[str, int] = {}
    for n in st.mains:
        c[n.status or ""] = c.get(n.status or "", 0) + 1
    return " ".join(
        f'<span class="chip {STATUS_CLASS.get(s, "")}">{k} {e(s)}</span>'
        for s, k in sorted(c.items(), key=lambda kv: model.SEVERITY.get(kv[0], 9))
    )


def loop_cell(st: Stream) -> str:
    if st.board is None:
        return ""
    if not st.flags:
        return '<span class="ok">✓ loop working</span>'
    shown = " · ".join(f'<span class="{f.level}">{e(f.short)}</span>' for f in st.flags[:2])
    more = f' <span class="dim">· {len(st.flags) - 2} more</span>' if len(st.flags) > 2 else ""
    return f'<span class="loop-h">Loop</span> {shown}{more}'


def previous_value(n: Number) -> dict | None:
    """The latest value measured strictly before the latest one (values at the same time are one reading)."""
    if not n.latest:
        return None
    earlier = [v for v in n.values if v["_t"] < n.latest["_t"]]
    return max(earlier, key=lambda v: v["_t"]) if earlier else None


def change_text(n: Number, now: datetime) -> str:
    prev = previous_value(n)
    if n.latest is None:
        return ""
    if prev is None:
        return '<span class="dim">first reading</span>'
    d = float(n.latest["value"]) - float(prev["value"])
    if d == 0:
        return f'<span class="dim">no change since {e(stamp(prev["_t"], now))}</span>'
    better = (d < 0) == (n.better == "lower")
    arrow = "▲" if d > 0 else "▼"
    sign = "+" if d > 0 else "−"
    cls = "ok" if better else "bad"
    return (
        f'<span class="{cls}">{arrow} {sign}{e(fmt_value(abs(round(d, 4)), n.unit))} since '
        f'{e(stamp(prev["_t"], now))} ({"better" if better else "worse"})</span>'
    )


def progress_bar(n: Number) -> str:
    """From the number's first value to its target, with the current value marked; absolute values at both ends."""
    t = n.target or {}
    tv = t.get("value")
    if n.status == model.BAD_TARGET:
        return ""  # a bar toward a target the number already beat would run the wrong way
    if not n.latest or not isinstance(tv, int | float):
        return ""
    # The bar starts at the first reading; several values at that one time are one reading, shown as the latest.
    cur = float(n.latest["value"])
    start = cur if n.values[0]["_t"] == n.latest["_t"] else float(n.values[0]["value"])
    if model.meets(cur, tv, n.better):
        frac = 1.0
    elif tv == start:
        frac = 0.0
    else:
        frac = (cur - start) / (tv - start)
    worse = frac < 0
    pct = max(0.0, min(1.0, frac)) * 100
    by = n.target_at.strftime("%b %-d") if n.target_at else str(t.get("by"))
    label = f"{round(pct)}% of the way from {fmt_value(start, n.unit)} to {fmt_value(tv, n.unit)}"
    return (
        f'<span class="prog" title="{e(label)}">'
        f'<span class="prog-end">{e(fmt_value(start, n.unit))}</span>'
        f'<span class="bar{" worse" if worse else ""}"><span class="fill" style="width:{pct:.0f}%"></span>'
        f'<span class="mark" style="left:{pct:.0f}%"></span></span>'
        f'<span class="prog-end">{e(fmt_value(tv, n.unit))} by {e(by)}</span></span>'
    )


def headline_number(st: Stream, now: datetime) -> str:
    """The lead's first main number: name, value, target, change since the previous reading, progress bar."""
    if st.board is None:
        return '<span class="ov-num dim">Nothing measured: no scoreboard yet.</span>'
    n = st.headline_number
    if n is None:
        return '<span class="ov-num missing">No main number defined.</span>'
    if n.latest is None:
        why = clip(n.missing or "no value and no reason given", 120)
        return (
            f'<span class="ov-num"><span class="num-label">{e(n.short)}</span> '
            f'<span class="missing">not measured: {e(why)}</span></span>'
        )
    return (
        f'<span class="ov-num"><span class="num-label">{e(n.short)}</span> '
        f'<b class="big">{e(fmt_value(n.latest["value"], n.unit))}</b> {change_text(n, now)}'
        f"{progress_bar(n)}</span>"
    )


def overview(page: Page, web: str, notes: dict[str, list[dict]], can_send: bool) -> str:
    waiting: dict[str, int] = {}  # open Dispatch asks on each stream's issues that wait on Sami
    for a in page.asks:
        if a.stream:
            waiting[a.stream.slug] = waiting.get(a.stream.slug, 0) + 1
    rows = []
    for st in page.streams:
        star = '<span class="attn" title="one of your three attention items">★</span> ' if st.attention else ""
        active = f"active {ago(st.lead_active, page.now)}" if st.lead_active else "no Dispatch activity"
        n_dec = waiting.get(st.slug, 0)
        dec = (
            f'<a class="ov-act hot" href="{e(page.inbox)}" target="_blank" rel="noopener">'
            f'{n_dec} decision{"s wait" if n_dec != 1 else " waits"} on you</a>'
            if n_dec
            else ""
        )
        headline = ""
        if st.headline:
            more = ' <span class="more-link">more</span>' if len(st.headline) > model.HEADLINE_MAX else ""
            headline = f'<span class="ov-headline">{e(clip(st.headline, model.HEADLINE_MAX))}{more}</span>'
        b = (st.board or {}).get("bottleneck") if isinstance((st.board or {}).get("bottleneck"), dict) else {}
        bott = (
            f'<span class="ov-bott" title="{e(b["text"])}"><span class="loop-h">Bottleneck</span> {e(b["text"])}</span>'
            if b.get("text")
            else ""
        )
        rows.append(
            f'<details class="ov-row" id="s-{e(st.slug)}"><summary>'
            f'<span class="ov-top"><span class="ov-name">{star}{e(st.name)}</span>'
            f'<span class="ov-chips">{counts(st)}</span><span class="chev" aria-hidden="true"></span></span>'
            f"{headline}{headline_number(st, page.now)}{bott}"
            f'<span class="ov-loop">{loop_cell(st)}</span>'
            f'<span class="ov-meta">{e(st.lead_name)} · {e(active)}{" · " if dec else ""}{dec} · '
            f'<a class="ov-act" href="#tell-{e(st.slug)}">✉ note to lead</a></span>'
            "</summary>"
            f'<div class="ov-detail">{stream_card(st, page, web, notes.get(st.slug, []), can_send)}</div>'
            "</details>"
        )
    return f'<div class="overview">{"".join(rows)}</div>'


def lead_line(st: Stream, now: datetime, web: str) -> str:
    active = (
        f"last Dispatch activity {e(when(st.lead_active, now))}" if st.lead_active else "no Dispatch activity on record"
    )
    title = f" · {clip(st.issue_title, 70)}" if st.issue_title else ""
    issue = (
        f'<a href="{e(web)}/issues/{e(st.issue)}" target="_blank" rel="noopener">{e(st.issue)}{e(title)}</a>'
        if st.issue
        else "no stream issue named yet"
    )
    board = ""
    if st.board_version:
        board = f" · scoreboard updated {e(when(st.updated_at, now))}"
    return f'<div class="lead">Lead <b>{e(st.lead_name)}</b> · {active} · {issue}{board}</div>'


def waits_line(board: dict) -> str:
    rows = [w for w in board.get("waits") or [] if isinstance(w, dict)]
    if not rows:
        return ""
    names = {"person": "a person", "lead": "their lead", "merge": "a merge", "compute": "compute", "other": "something else"}
    parts = []
    for w in [w for w in rows if w.get("count")]:
        oldest = f", the oldest for {fmt_value(w.get('oldest_hours'))} h" if w.get("oldest_hours") is not None else ""
        parts.append(f"{e(w.get('count'))} on {e(names.get(w.get('on'), w.get('on')))}{e(oldest)}")
    return f'<div class="waits"><span class="loop-h">Work items waiting</span> {" · ".join(parts)}</div>'


def bottleneck_line(board: dict, web: str) -> str:
    b = board.get("bottleneck") if isinstance(board.get("bottleneck"), dict) else {}
    if not b.get("text"):
        return ""
    extra = []
    if isinstance(b.get("share"), int | float):
        extra.append(f"costs {round(100 * b['share'])}% of the main number")
    if b.get("ceiling") is not None:
        extra.append(f"main number without it: {fmt_value(b['ceiling'])}")
    tail = f' <span class="dim">({e("; ".join(extra))})</span>' if extra else ""
    return f'<div class="bott"><span class="loop-h">Bottleneck</span> {linkify(clip(str(b["text"]), 260), web)}{tail}</div>'


def experiments_table(st: Stream, now: datetime, web: str) -> str:
    exps = [x for x in (st.board or {}).get("experiments") or [] if isinstance(x, dict)]
    if not exps:
        return "<p class=dim>No experiments recorded.</p>"
    rows = []
    for x in reversed(exps):
        started, ended = model.parse_time(x.get("started")), model.parse_time(x.get("ended"))
        notes = []
        if x.get("predicted") is not None:
            notes.append(f"predicted {fmt_value(x.get('predicted'))}")
        if x.get("predicted_main") is not None:
            notes.append(f"predicted main-number change {x.get('predicted_main')}")
        if x.get("on_bottleneck") is False:
            notes.append(f"off the bottleneck: {x.get('why_off') or 'no reason given'}")
        if x.get("designs_tried") is not None:
            notes.append(f"designs tried: {x.get('designs_tried')}")
        if x.get("why_stopped"):
            notes.append(f"stopped because: {x.get('why_stopped')}")
        note = f'<div class="dim">{linkify("; ".join(map(str, notes)), web)}</div>' if notes else ""
        rows.append(
            f'<tr><td><span class="verdict v-{e(x.get("verdict"))}">{e(x.get("verdict"))}</span></td>'
            f"<td>{linkify(str(x.get('change') or ''), web)}"
            f'<div class="dim">Hypothesis: {linkify(str(x.get("hypothesis") or "none given"), web)}</div>{note}'
            f'<div class="dim">{linkify(str(x.get("link") or ""), web)}</div></td>'
            f"<td>{e(x.get('number'))}: {e(fmt_value(x.get('before')))} → {e(fmt_value(x.get('after')))}</td>"
            f"<td>started {e(when(started, now))}"
            f"{'<br>ended ' + e(when(ended, now)) if ended else ''}</td></tr>"
        )
    return (
        '<table class="exps"><tr><th>verdict</th><th>change</th><th>before → after</th><th>when</th></tr>'
        + "".join(rows)
        + "</table>"
    )


ASK_FOR_SCOREBOARD = (
    "Please publish your stream's scoreboard.json on your stream issue "
    "(format: ~/src/scrum/workstreams/scoreboard-schema.md) so the dashboard can show your numbers."
)


def note_box(st: Stream, notes: list[dict], now: datetime, can_send: bool, back: str = "/") -> str:
    if not st.lead_session:
        return '<p class="dim">No lead session named, so a note has nowhere to go.</p>'
    disabled = "" if can_send else " disabled"
    past = []
    for n in notes[-3:][::-1]:
        t = model.parse_time(n.get("at"))
        state = n.get("delivery", "pending")
        cls = "ok" if state == "delivered" else "bad"
        why = f": {n['error']}" if n.get("error") else ""
        again = ""
        if state != "delivered":
            again = (
                '<form method="post" action="/note" class="inline">'
                f'<input type="hidden" name="stream" value="{e(st.slug)}"><input type="hidden" name="back" value="{e(back)}">'
                f'<input type="hidden" name="text" value="{e(n.get("text", ""))}">'
                f'<button type="submit" class="small"{disabled}>Send again</button></form>'
            )
        past.append(
            f'<li><span class="dim">{e(when(t, now))}</span> {e(clip(n.get("text", ""), 200))} '
            f'<span class="{cls}">{e(state)}{e(why)}</span>{again}</li>'
        )
    hist = f'<ul class="notes">{"".join(past)}</ul>' if past else ""
    why = "" if can_send else '<span class="dim"> Sending needs you signed in to the tailnet as Sami.</span>'
    prefill = (
        f' <button type="button" class="small ghost" data-prefill="note-{e(st.slug)}" '
        f'data-text="{e(ASK_FOR_SCOREBOARD)}">Ask for a scoreboard</button>'
        if st.board is None
        else ""
    )
    form = (
        f'<form method="post" action="/note" class="note-form">'
        f'<input type="hidden" name="stream" value="{e(st.slug)}"><input type="hidden" name="back" value="{e(back)}">'
        f'<textarea name="text" id="note-{e(st.slug)}" rows="6" maxlength="{MAX_NOTE}" required '
        'placeholder="A short note; it reaches the lead as a message from you"></textarea>'
        f'<div><button type="submit"{disabled}>Send to the {e(st.name)} lead</button>{prefill}{why}</div>'
        "</form>"
    )
    sent = f" ({len(notes)} sent)" if notes else ""
    return (
        f'<details class="tell" id="tell-{e(st.slug)}"><summary>✉ Note to the {e(st.name)} lead{e(sent)}</summary>'
        f"{form}{hist}</details>"
    )


def stream_card(st: Stream, page: Page, web: str, notes: list[dict], can_send: bool) -> str:
    """The stream's detail, shown when its glance row is opened."""
    now = page.now
    full = f'<p class="full-headline">{linkify(st.headline, web)}</p>' if st.headline else ""
    head = f"{lead_line(st, now, web)}{full}{note_box(st, notes, now, can_send)}"
    if st.board is None:
        if st.board_error:
            why = f"The scoreboard file could not be read: {e(st.board_error)}"
        else:
            why = (
                f"{e(st.issue)} has the label <i>stream</i> but no scoreboard.json artifact; the lead publishes one "
                "with <code>dispatch artifact</code>."
            )
        return f'{head}<p class="noboard">No scoreboard yet. {why}</p>'

    guards: dict[str, list[Number]] = {}
    for n in st.others:
        if n.role == "guard" and n.raw.get("guards"):
            guards.setdefault(str(n.raw["guards"]), []).append(n)
    mains = "".join(main_row(n, guards.get(n.id, []), now, web) for n in st.mains)
    mains = mains or '<li class="missing">No main number defined.</li>'
    flags = "".join(f'<li class="{f.level}">{e(f.text)}</li>' for f in st.flags)
    facts = "; ".join(st.facts)
    loop = (
        '<div class="loop"><span class="loop-h">Loop</span> '
        + (f'<span class="ok">{e(facts)}</span>' if facts else "")
        + (f'<ul class="flags">{flags}</ul>' if flags else "")
        + "</div>"
    )
    b = st.board.get("bottleneck") if isinstance(st.board.get("bottleneck"), dict) else {}
    others = "".join(other_row(n, now, web) for n in st.others)
    hist = "".join(history_table(n, now, web) for n in st.mains + st.others)
    details = (
        f'<details class="more" id="more-{e(st.slug)}"><summary>Details: fast and guard numbers, experiments, '
        "every value</summary>"
        + (f'<h4>Fast and guard numbers</h4><ul class="others">{others}</ul>' if others else "")
        + (
            f"<h4>Bottleneck</h4><p>{linkify(str(b.get('text') or ''), web)}</p>"
            f"<p><b>Evidence:</b> {linkify(str(b.get('evidence') or 'none given'), web)} "
            f'<span class="dim">(since {e(b.get("since"))})</span></p>'
            if b
            else ""
        )
        + f"<h4>Experiments</h4>{experiments_table(st, now, web)}"
        + f"<h4>Every value</h4>{hist or '<p class=dim>No values recorded.</p>'}"
        + "</details>"
    )
    return (
        f'{head}<ul class="mains">{mains}</ul>{loop}'
        f"{bottleneck_line(st.board, web)}{waits_line(st.board)}{details}"
    )


def length_text(seconds: float | None) -> str:
    if seconds is None:
        return "length unknown"
    s = round(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"


def changes_section(page: Page, window: str) -> str:
    links = []
    for key, label in (("last", "since your last look"), ("24h", "24 h"), ("7d", "7 days")):
        cls = ' class="on"' if key == window else ""
        links.append(f'<a href="?window={key}#changes"{cls}>{label}</a>')

    slugs = {s.name: s.slug for s in page.streams}

    def row(c) -> str:
        t = stamp(c.at, page.now) if c.at else "–"
        name = f'<a href="#s-{e(slugs[c.stream])}"><b>{e(c.stream)}</b></a>' if c.stream in slugs else f"<b>{e(c.stream)}</b>"
        return f'<li><span class="dim">{e(t)}</span> {name} {e(c.text)}</li>'

    # New decisions are answered in the Dispatch Inbox: one line counts them, so the outcomes (numbers that moved,
    # experiments that ended) lead the list.
    new_decisions = [c for c in page.changes if c.kind == "decision"]
    outcomes = [c for c in page.changes if c.kind != "decision"]
    lead = ""
    if new_decisions:
        by: dict[str, int] = {}
        for c in new_decisions:
            by[c.stream] = by.get(c.stream, 0) + 1
        where = ", ".join(f"{k} {n}" for k, n in sorted(by.items(), key=lambda kv: -kv[1]))
        n = len(new_decisions)
        lead = (
            f'<li><a href="{e(page.inbox)}" target="_blank" rel="noopener"><b>{n} new decision{"s" if n != 1 else ""} '
            f'for you</b></a> <span class="dim">({e(where)}), in your Dispatch Inbox</span></li>'
        )
    shown = lead + "".join(row(c) for c in outcomes[:CHANGES_SHOWN])
    rest = outcomes[CHANGES_SHOWN:] + new_decisions
    more = (
        f'<details id="changes-more"><summary>{len(rest)} more changes</summary><ul class="changes">'
        + "".join(row(c) for c in rest)
        + "</ul></details>"
        if rest
        else ""
    )
    body = f'<ul class="changes">{shown}</ul>{more}' if shown else '<p class="dim">Nothing changed in this window.</p>'
    return (
        f'<section id="changes"><h2>What changed since {e(stamp(page.since, page.now))}</h2>'
        f'<div class="window"><span class="dim">{e(page.since_label)}.</span> Show: {" · ".join(links)}</div>'
        f"{body}</section>"
    )


def summary_line(page: Page) -> str:
    def first(pred) -> tuple[int, str]:
        hits = [s for s in page.streams if pred(s)]
        return len(hits), (f"#s-{hits[0].slug}" if hits else "#glance")

    behind, behind_at = first(lambda s: any(n.status == model.BEHIND for n in s.mains))
    unmeasured, unmeasured_at = first(lambda s: any(n.status == model.NOT_MEASURED for n in s.mains))
    noboard, noboard_at = first(lambda s: s.board is None)
    trouble, trouble_at = first(lambda s: any(f.level == "bad" for f in s.flags))

    def chip_link(href: str, count: int, text: str, hot: bool) -> str:
        cls = ' class="hot"' if hot and count else ""
        return f'<a href="{href}"{cls}><b>{count}</b> {e(text)}</a>'

    bits = [
        chip_link(
            "#changes",
            len(page.changes),
            f"change{'s' if len(page.changes) != 1 else ''} since {stamp(page.since, page.now)}",
            False,
        ),
        chip_link(behind_at, behind, f"stream{'s' if behind != 1 else ''} behind target", True),
        chip_link(unmeasured_at, unmeasured, "with a main number not measured", True),
        chip_link(noboard_at, noboard, "with no scoreboard", True),
        chip_link(trouble_at, trouble, f"loop{'s' if trouble != 1 else ''} with a problem", True),
    ]
    return f'<nav class="summary">{"".join(bits)}</nav>'


def body(
    page: Page,
    web: str,
    notes: dict[str, list[dict]],
    window: str,
    can_send: bool,
    notice: str | None,
    notice_error: bool,
) -> str:
    errs = "".join(f"<li>{e(x)}</li>" for x in page.errors)
    err_box = f'<div class="errors"><b>Some data could not be read:</b><ul>{errs}</ul></div>' if errs else ""
    note = f'<div class="notice{" bad-bg" if notice_error else ""}">{e(notice)}</div>' if notice else ""
    read = when(page.read_at, page.now) if page.read_at else "not yet"
    inbox = (
        f'<p class="inbox"><a href="{e(page.inbox)}" target="_blank" rel="noopener">Decisions waiting on you are in '
        "your Dispatch Inbox</a></p>"
    )
    glance = (
        '<section id="glance"><h2>Streams at a glance</h2>'
        '<p class="dim sub">Tap a stream for its numbers, loop, experiments and the note box.</p>'
        f"{overview(page, web, notes, can_send)}</section>"
    )
    return (
        f'<header><h1>Streams</h1><div class="read">Data read {e(read)} · refreshes every minute</div></header>'
        f"{note}{err_box}{inbox}{summary_line(page)}"
        f'<div class="top">{glance}{changes_section(page, window)}</div>'
    )


# The palette both pages share (/ and /v2).
THEME = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--dim:#5d6470;--line:#dde1e7;--bad:#b42318;--badbg:#fde8e6;
--warn:#9a5700;--warnbg:#fff3dc;--ok:#1e7b3c;--okbg:#e3f5e8;--blue:#1d4ed8;--grey:#5f6673;--greybg:#eceef2}
@media (prefers-color-scheme:dark){:root{--bg:#111317;--card:#1a1d23;--ink:#e8eaee;--dim:#9aa3af;--line:#2c313a;
--bad:#ff7b6b;--badbg:#3a1d1a;--warn:#ffbe5c;--warnbg:#3a2d14;--ok:#5fd38a;--okbg:#16301f;--blue:#8ab4ff;
--grey:#a0a7b4;--greybg:#2a2e36}}
"""

CSS = THEME + """
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",
Roboto,Helvetica,Arial,sans-serif}
main{max-width:1400px;margin:0 auto;padding:12px 20px 60px}
a{color:var(--blue)}
header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
h1{font-size:21px;margin:2px 0}
h2{font-size:17px;margin:14px 0 2px}
h3{font-size:17px;margin:0 0 4px;display:flex;flex-wrap:wrap;align-items:center;gap:6px}
h4{margin:14px 0 4px;font-size:14px}
.read,.dim{color:var(--dim)}
.sub{margin:0 0 8px;font-size:12.5px}
.summary{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px}
.summary a{display:inline-block;padding:3px 10px;border:1px solid var(--line);border-radius:999px;
background:var(--card);color:var(--ink);text-decoration:none;font-size:13.5px}
.summary a.hot{border-color:var(--bad);color:var(--bad)}
.errors{background:var(--badbg);color:var(--bad);padding:8px 12px;border-radius:8px;margin:8px 0}
.errors ul{margin:4px 0 0 18px;padding:0}
.notice{background:var(--okbg);color:var(--ok);padding:8px 12px;border-radius:8px;margin:8px 0}
.notice.bad-bg{background:var(--badbg);color:var(--bad)}
.inbox{margin:8px 0 4px;font-size:15px;font-weight:600}
.top{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);grid-template-areas:"ch gl";gap:4px 20px;
align-items:start}
#glance{grid-area:gl}#changes{grid-area:ch}
details>summary{cursor:pointer;color:var(--blue);font-size:13.5px}
.chip{display:inline-block;font-size:11.5px;font-weight:600;padding:1px 7px;border-radius:999px;white-space:nowrap;
background:var(--greybg);color:var(--grey)}
.chip.behind{background:var(--badbg);color:var(--bad)}
.chip.unmeasured{background:var(--card);color:var(--bad);border:1px dashed var(--bad)}
.chip.notarget{background:var(--warnbg);color:var(--warn)}
.chip.ontrack{background:var(--okbg);color:var(--ok)}
.chip.met{background:var(--ok);color:#fff}
.overview{background:var(--card);border:1px solid var(--line);border-radius:8px}
.ov-row{border-top:1px solid var(--line);scroll-margin-top:8px}
.ov-row:first-child{border-top:0}
.ov-row>summary{list-style:none;display:block;padding:9px 12px;cursor:pointer;color:var(--ink);font-size:13.5px}
.ov-row>summary::-webkit-details-marker{display:none}
.ov-row>summary:hover{background:var(--bg)}
.ov-row[open]>summary{background:var(--bg);border-bottom:1px solid var(--line)}
.ov-top{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px}
.ov-name{font-weight:700;font-size:15px}
.ov-chips .chip{margin-right:3px}
.chev{margin-left:auto;color:var(--blue);font-size:12.5px;white-space:nowrap}
.chev::after{content:"details ▾"}
.ov-row[open] .chev::after{content:"close ▴"}
.ov-headline{display:block;margin-top:4px;font-size:14px;line-height:1.4}
.more-link{color:var(--blue)}
.ov-num{display:block;margin-top:5px}
.num-label{color:var(--dim)}
.big{font-size:16px}
.prog{display:flex;align-items:center;gap:6px;margin-top:3px;font-size:11.5px;color:var(--dim)}
.prog-end{white-space:nowrap}
.bar{position:relative;flex:1;min-width:60px;max-width:260px;height:7px;background:var(--greybg);border-radius:4px}
.bar .fill{position:absolute;left:0;top:0;bottom:0;background:var(--blue);border-radius:4px;opacity:.55}
.bar .mark{position:absolute;top:-3px;width:3px;height:13px;margin-left:-1px;background:var(--blue);border-radius:2px}
.bar.worse .mark{background:var(--bad)}
.ov-bott{display:block;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ov-loop{display:block;margin-top:3px}
.ov-meta{display:block;margin-top:3px;font-size:12.5px;color:var(--dim)}
.ov-act{text-decoration:none}
.ov-act.hot{color:var(--bad);font-weight:600}
.ov-detail{padding:10px 12px 12px;overflow-wrap:anywhere}
.ov-detail table{display:block;overflow-x:auto}
.full-headline{font-size:14px;margin:6px 0}
.inline{display:inline;margin-left:6px}
button.small{padding:2px 8px;font-size:12.5px}
button.ghost{background:transparent;color:var(--blue);border:1px solid var(--blue)}
.attn,.attn-label{color:#b86e00}
.attn-label{font-size:12px;font-weight:600}
.lead{font-size:13px;color:var(--dim);margin-bottom:4px}
.noboard{background:var(--greybg);border:1px dashed var(--bad);color:var(--ink);padding:10px 12px;border-radius:8px;
margin:6px 0 4px;font-weight:600}
.mains{list-style:none;padding:0;margin:6px 0 6px}
.num{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr) 124px;gap:12px;padding:6px 0;
border-top:1px solid var(--line);align-items:center}
.num:first-child{border-top:0}
.num-name{font-weight:600}
.num-body{font-size:13.5px}
.guard{font-size:12.5px;margin-top:2px}
.missing{color:var(--bad);font-size:13px}
.spark .val{fill:none;stroke:var(--blue);stroke-width:1.6}
.spark .tgt{stroke:var(--ok);stroke-width:1.2;stroke-dasharray:4 3}
.spark .proj{stroke:var(--grey);stroke-width:1.2;stroke-dasharray:1 3}
.spark .dot{fill:var(--blue)}
.loop,.bott,.waits{font-size:13.5px;margin:4px 0}
.loop-h{font-weight:700;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--dim);margin-right:4px}
.flags{margin:2px 0 0 0;padding:0 0 0 18px}
.bad,.flags li.bad{color:var(--bad)}
.warn,.flags li.warn{color:var(--warn)}
.ok{color:var(--ok)}
.role{font-size:11.5px;font-weight:700;text-transform:uppercase;color:var(--dim)}
.others{padding-left:18px;margin:4px 0;font-size:13.5px}
.more{margin:6px 0 0}
.tell{margin:2px 0 6px}
.note-form{display:grid;gap:6px;margin-top:6px;max-width:640px}
textarea{width:100%;font:inherit;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);
color:var(--ink)}
button{font:inherit;font-weight:600;background:var(--blue);color:#fff;border:0;border-radius:6px;padding:6px 14px;
cursor:pointer}
button[disabled]{opacity:.5;cursor:not-allowed}
.notes{font-size:13px;padding-left:18px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;vertical-align:top;padding:4px 6px;border-top:1px solid var(--line)}
th{color:var(--dim);font-weight:600}
.hist{margin:8px 0}
.hist-name{font-weight:600;font-size:13px}
.verdict{font-weight:700}
.v-kept{color:var(--ok)}.v-reverted{color:var(--bad)}.v-running{color:var(--blue)}.v-inconclusive{color:var(--warn)}
.v-provisional{color:var(--warn)}
.window{font-size:13px;margin-bottom:4px}
.window a.on{font-weight:700;color:var(--ink);text-decoration:none}
.changes{list-style:none;padding:0;margin:0;background:var(--card);border:1px solid var(--line);border-radius:8px}
.changes li{padding:5px 10px;border-top:1px solid var(--line);font-size:13px}
.changes li:first-child{border-top:0}
#changes-more{margin-top:6px}
@media (max-width:1000px){.top{grid-template-columns:minmax(0,1fr);grid-template-areas:"gl" "ch";gap:4px}}
@media (max-width:720px){
main{padding:8px 12px 60px}
.summary{flex-wrap:nowrap;overflow-x:auto;gap:4px;padding-bottom:2px}
.summary a{font-size:12.5px;padding:2px 8px;white-space:nowrap}
.ov-row>summary{padding:9px 10px}
.prog-end{white-space:normal;max-width:38%}
.ov-detail{padding:8px 10px 10px}
.num{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"head spark" "body body";gap:2px 8px}
.num-head{grid-area:head}.num-body{grid-area:body}.num-spark{grid-area:spark}
.exps td:nth-child(4),.exps th:nth-child(4){display:none}
.hist td:nth-child(3),.hist th:nth-child(3){display:none}
}
"""

SCRIPT = """
(function(){
  // Live updates: the server pushes the page's version over a WebSocket (/ws). On a new version the page fetches its
  // own fragment; if the fragment is the one it already shows it does nothing at all, and otherwise it morphs the
  // live DOM into it node by node, so the scroll position, open details, what he types and a playing video all stay.
  // A block marked data-keep is replaced only when its data-key changed and no video inside it plays. A new build
  // never reloads the page by itself: it offers a reload and waits to be clicked. If the connection drops it
  // reconnects, waiting 1 s, then up to 30 s.
  const page = document.getElementById('page');
  let have = page.dataset.version || '', want = have, busy = false;
  function sameKey(a, b){ return a.getAttribute('data-key') === b.getAttribute('data-key'); }
  function playing(el){ return [...el.querySelectorAll('video')].some(v => !v.paused && !v.ended); }
  // A form field's own state is his, not the server's: its size (the inline height he dragged), its value, where
  // the caret is, what is selected and how far it is scrolled. The page never writes any of that back over him
  // (Sami, 2026-10-26: "When I resize the input text box, it goes back to being tiny").
  const FIELD = /^(TEXTAREA|INPUT|SELECT)$/;
  const HIS = /^(style|value|rows|cols|checked|selected)$/;
  function field(el){ return el.nodeType === 1 && FIELD.test(el.nodeName); }
  function holdsState(el){
    if (el.nodeType !== 1) return false;
    if (el.contains(document.activeElement)) return true;
    if (field(el)) return !!el.value || !!el.getAttribute('style');
    return [...el.querySelectorAll('textarea,input,select')].some(holdsState);
  }
  function morph(a, b){
    if (a.nodeType !== b.nodeType || a.nodeName !== b.nodeName) { if (!holdsState(a)) a.replaceWith(b); return; }
    if (a.nodeType === 3 || a.nodeType === 8) { if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue; return; }
    if (a.nodeType !== 1) return;
    if (a.hasAttribute('data-keep')) {
      if (sameKey(a, b) || playing(a) || holdsState(a)) return;  // never restart a playing video
      a.replaceWith(b); return;
    }
    const mine = field(a);
    for (const {name} of [...a.attributes]) if (name !== 'open' && !(mine && HIS.test(name)) && !b.hasAttribute(name)) a.removeAttribute(name);
    for (const {name, value} of [...b.attributes]) if (name !== 'open' && !(mine && HIS.test(name)) && a.getAttribute(name) !== value) a.setAttribute(name, value);
    if (mine) return;  // his text, his choice, his size and his caret stay exactly as they are
    children(a, b);
  }
  function children(a, b){
    // Children with an id are matched by id (a stream that moves up keeps its own player); the rest by position.
    const byId = new Map([...a.children].filter(c => c.id).map(c => [c.id, c]));
    const news = [...b.childNodes];
    let at = a.firstChild;
    for (const n of news) {
      let old = n.nodeType === 1 && n.id ? byId.get(n.id) : null;
      if (old) { byId.delete(old.id); if (old !== at) a.insertBefore(old, at); }
      else if (at && !(at.nodeType === 1 && at.id && byId.has(at.id))) old = at;
      if (old) { at = old.nextSibling; morph(old, n); }
      else a.insertBefore(n, at);
    }
    // A node he is typing in, or has typed in, stays until he is done with it, whatever the server now sends.
    while (at) { const next = at.nextSibling; if (!holdsState(at)) at.remove(); at = next; }
    byId.forEach(el => { if (!holdsState(el)) el.remove(); });
  }
  // Moving a node in the DOM blurs what is inside it, so a morph that reorders the page would take his caret out
  // of the box he is writing in. The field he is in, where the caret is, what is selected and how far the box is
  // scrolled are all put back exactly as they were.
  function keepingHisPlace(change){
    const a = document.activeElement;
    let was = null;
    if (a && FIELD.test(a.nodeName)) {
      was = {el: a, top: a.scrollTop, left: a.scrollLeft};
      try { was.start = a.selectionStart; was.end = a.selectionEnd; was.dir = a.selectionDirection; } catch (e) {}
    }
    change();
    if (!was || !was.el.isConnected) return;
    if (document.activeElement !== was.el) was.el.focus({preventScroll: true});
    if (was.start !== undefined) { try { was.el.setSelectionRange(was.start, was.end, was.dir); } catch (e) {} }
    was.el.scrollTop = was.top; was.el.scrollLeft = was.left;
  }
  let shown = null;  // the fragment the page is showing, so an update that changes nothing he can see never runs
  async function update(){
    if (busy || stale || want === have) return;
    busy = true;
    const target = want;
    try {
      const u = new URL(location.href); u.searchParams.set('fragment', '1'); u.searchParams.set('since', SINCE);
      u.searchParams.delete('sent'); u.searchParams.delete('error'); u.hash = '';
      const r = await fetch(u, {cache: 'no-store'}); if (!r.ok) throw new Error('HTTP ' + r.status);
      const text = await r.text();
      if (text === shown) {
        stamp('no change ' + pacific(new Date()));
      } else {
        const fresh = document.createElement('main'); fresh.innerHTML = text;
        const y = window.scrollY;
        keepingHisPlace(() => children(page, fresh));
        if (Math.abs(window.scrollY - y) > 1) window.scrollTo(0, y);
        stamp('updated ' + pacific(new Date()));
      }
      shown = text;
      have = r.headers.get('X-Page-Version') || target; page.dataset.version = have;
    } catch (err) {
      stamp('update failed: ' + err.message, true);
    }
    busy = false;
    if (want !== have) setTimeout(update, 500);
  }
  const pill = document.getElementById('live');
  const pillText = pill && pill.querySelector('.live-text');
  const pillReload = pill && pill.querySelector('.live-reload');
  function pacific(d){ return d.toLocaleTimeString('en-GB', {timeZone: 'America/Los_Angeles', hour: '2-digit', minute: '2-digit'}); }
  function stamp(text, bad){ if (!pill) return; pill.dataset.state = bad ? 'off' : 'on'; pillText.textContent = 'Live · ' + text; }
  function down(text){ if (!pill) return; pill.dataset.state = 'off'; pillText.textContent = text; }
  let ws = null, delay = 1000, heard = Date.now(), build = null, stale = false;
  // After a deploy the fragment may need CSS or JavaScript this page lacks, so the page stops morphing — and it
  // says so and waits. It never reloads itself: a reload while he reads or types is his to ask for, not the
  // page's to take (Sami, 2026-10-26: the page "is still doing some stupid, fucking annoying thing where it
  // reloads the page every few seconds").
  function offerReload(){
    stale = true;
    down('updates paused');
    if (pillReload) pillReload.hidden = false;
  }
  if (pillReload) pillReload.addEventListener('click', ev => { ev.preventDefault(); location.reload(); });
  function onMessage(ev){
    heard = Date.now();
    let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.type === 'build') { if (build === null) build = m.build; else if (build !== m.build) offerReload(); }
    else if (m.type === 'version') { want = m.version; update(); }
    else if (m.type === 'read') {
      const el = document.querySelector('[data-read-at]');
      if (el && m.read_at) { el.dataset.readAt = m.read_at; el.textContent = pacific(new Date(m.read_at)); }
    }
  }
  function connect(){
    if (ws) { ws.onclose = null; try { ws.close(); } catch (e) {} }
    const sock = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
    ws = sock;
    sock.onopen = () => { delay = 1000; heard = Date.now(); stamp('connected'); };
    sock.onmessage = onMessage;
    sock.onclose = () => {
      if (ws !== sock) return;
      ws = null; down('reconnecting…');
      setTimeout(connect, delay); delay = Math.min(delay * 2, 30000);
    };
  }
  // A connection the network silently dropped (a laptop asleep) sends nothing: 45 s without a ping means reconnect.
  setInterval(() => { if (Date.now() - heard > 45000) { heard = Date.now(); down('reconnecting…'); connect(); } }, 5000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() - heard > 20000) connect(); });
  if (window.WebSocket) connect(); else down('live updates need a newer browser');
  // A stream's detail and its note box sit inside its glance row: a link to either opens every enclosing row.
  function reveal(hash, focus){
    if (!['#s-', '#tell-', '#ask-'].some(p => hash.startsWith(p))) return false;
    const el = document.getElementById(hash.slice(1)); if (!el) return false;
    for (let d = el; d; d = d.parentElement) if (d.tagName === 'DETAILS') d.open = true;
    el.scrollIntoView({block:'start'});
    const t = hash.startsWith('#tell-') && el.querySelector('textarea');
    if (t && focus) t.focus({preventScroll:true});
    return true;
  }
  window.addEventListener('hashchange', () => reveal(location.hash, true)); reveal(location.hash, true);
  document.addEventListener('click', ev => {
    const b = ev.target.closest('[data-prefill]');
    if (b) { const t = document.getElementById(b.dataset.prefill); if (t) { t.value = b.dataset.text; t.focus(); } return; }
    const a = ev.target.closest('a[href^="#"]');
    if (a && reveal(a.getAttribute('href'), true)) { ev.preventDefault(); history.replaceState(null, '', a.getAttribute('href')); }
  });
})();
"""

# The connection state of the live updates, outside #page so a morph never resets it. The reload link appears only
# when the server reports a new build, and only a click on it reloads.
LIVE_PILL = ('<div id="live" class="live" data-state="off" role="status"><span class="dot"></span>'
             '<span class="live-text">connecting…</span>'
             '<a class="live-reload" href="" hidden>New version: reload</a></div>')
LIVE_CSS = """
.live{position:fixed;right:12px;bottom:12px;z-index:9;display:flex;align-items:center;gap:2px;font-size:12px;
padding:4px 10px;border-radius:999px;background:var(--card);border:1px solid var(--line);color:var(--dim);
box-shadow:0 1px 4px rgba(0,0,0,.08)}
.live .dot{width:8px;height:8px;border-radius:50%;background:var(--grey);margin-right:4px}
.live[data-state=on] .dot{background:var(--ok);margin-right:0}
/* While the updates run there is nothing to say, so the pill is a dot and covers no text it floats over; it opens
   on hover, and whenever it has something to report (a reconnect, a failure, a new version). */
.live[data-state=on]{padding:5px;gap:0}
.live[data-state=on] .live-text{display:none}
.live[data-state=on]:hover{padding:4px 10px}
.live[data-state=on]:hover .dot{margin-right:4px}
.live[data-state=on]:hover .live-text{display:inline}
.live-reload{margin-left:8px;font-weight:600;color:var(--blue)}
.live-reload[hidden]{display:none}
"""


def page_html(inner: str, since_iso: str, version: str = "") -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Streams</title><style>{CSS}{LIVE_CSS}</style></head><body>"
        f'<main id="page" data-version="{e(version)}">{inner}</main>{LIVE_PILL}'
        f"<script>const SINCE = {json.dumps(since_iso)};{SCRIPT}</script></body></html>"
    )
