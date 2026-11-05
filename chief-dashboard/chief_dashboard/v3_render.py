"""The first screen: one question, answered at a glance.

Sami, 2026-11-05: "Stop making the mistake of thinking that jamming a lot of things onto a web page is the same
thing as making a good dashboard." The /v2 page carried 1,022 numbers; a reader has to find the answer in it, so
it goes unread.

The question this page answers: WHICH STREAMS ARE BEHIND, AND WHAT IS THE ONE THING TO DO ABOUT EACH?
Everything here answers that. Anything that does not belongs behind a link.

It holds, in order: the answer in three numbers; one line per stream, worst first, each with its main number and
its one next thing; what is wrong in one sentence; when the page last read its sources; links to the rest. The
caps it holds itself to are `building-a-dashboard`'s: at most 40 numbers in the visible text, at most 7 outside
the per-stream lines, no identifier of 7 characters or more, no seconds, no raw timestamp, and one screen at
1280x800."""

from __future__ import annotations

import re
from html import escape as e

from .pace import BEHIND, CANT_TELL, ON_PACE
from .v2_model import KpiRow, PageV2, StreamView, clock

QUESTION = "Which streams are behind, and what is the one thing to do about each?"


# The verdict is the front page's own (pace.pace), so the two pages never disagree. It reads a number with no
# reading newer than twice its cadence as Can't tell. The older model.status projected a line through stale
# readings and called check-wall "on track" at 4,202 s against a 753 s target (SPEEDCOACH, 2026-11-05 05:39Z).
RANK = {BEHIND: 0, CANT_TELL: 1, ON_PACE: 2}


def _verdict(k: KpiRow | None) -> str:
    return k.label.label if k else CANT_TELL


def _worst(v: StreamView) -> KpiRow | None:
    """The stream's own worst main number, which decides its line and its place on the page."""
    if not v.kpis:
        return None
    return sorted(v.kpis, key=lambda k: RANK.get(k.label.label, 1))[0]


def _value(k: KpiRow) -> str:
    """The number as a reader needs it: no second decimal, no raw figure longer than it has to be."""
    if not k.readings:
        return "no reading"
    v = k.readings[-1][1]
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)[:12]
    if abs(f) >= 1000:
        return f"{f:,.0f}"
    return f"{f:.0f}" if f == int(f) else f"{f:.1f}"


# A lead writes its plan for other leads, so an item carries its evidence: identifiers, times to the second, run
# and commit names. On this page that evidence is noise, and it breaks the page's own caps on numbers and on
# machinery words. So the line shows the ACTION and drops the rest; the stream's own section has the whole item.
_CLIP = 58


def _action(text: str) -> str:
    """The first clause of a plan item, without its evidence: up to the first sentence end, bracket or dash."""
    head = re.split(r"[.;(]| - |: ", text.strip(), maxsplit=1)[0].strip()
    head = re.sub(r"\s+", " ", head)
    if len(head) > _CLIP:
        head = head[:_CLIP].rsplit(" ", 1)[0] + "…"
    return head or text.strip()[:_CLIP]


def _next_thing(v: StreamView) -> str:
    """The stream's one next thing: the first item of today's plan that has not merged, clipped to its action.
    The page says what the stream itself says; it invents nothing."""
    for line in v.lines:
        if line.kind != "merged":
            return _action(line.item.what)
    return "no item left in today's plan"


def _rows(page: PageV2) -> list[tuple[StreamView, KpiRow | None]]:
    pairs = [(v, _worst(v)) for v in page.streams]
    return sorted(pairs, key=lambda p: (RANK.get(_verdict(p[1]), 1), p[0].st.name))


def first_screen(page: PageV2) -> str:
    rows = _rows(page)
    # The three counts must add up to the streams on the page: a reader who sums them and comes up short stops
    # trusting the page. So every stream falls in exactly one: "needs a look" is a number the page cannot judge
    # (no target, one reading, or no reading newer than twice its cadence), which is neither behind nor on track.
    behind = [p for p in rows if _verdict(p[1]) == BEHIND]
    fine = [p for p in rows if _verdict(p[1]) == ON_PACE]
    dark = [p for p in rows if p not in behind and p not in fine]
    assert len(behind) + len(dark) + len(fine) == len(rows)

    head = (
        f'<header><h1>{e(QUESTION)}</h1>'
        f'<div class="ans">'
        f'<span class="big bad">{len(behind)}</span> behind'
        f'<span class="big dim">{len(dark)}</span> need a look'
        f'<span class="big ok">{len(fine)}</span> on track'
        f"</div></header>"
    )

    items = []
    for v, k in rows:
        st = _verdict(k)
        cls = "bad" if st == BEHIND else ("ok" if st == ON_PACE else "dim")
        num = f'{e(k.num.short or k.num.name)} <b>{_value(k)}</b>' if k else "no number"
        items.append(
            f'<li class="{cls}"><a href="/#s-{e(v.st.slug)}"><span class="nm">{e(v.st.name)}</span></a>'
            f'<span class="kv">{num}</span>'
            f'<span class="nx">{e(_next_thing(v))}</span></li>'
        )
    body = f'<ul class="streams">{"".join(items)}</ul>'

    if behind:
        worst = behind[0]
        wrong = (f'<p class="wrong">The one to look at is <b>{e(worst[0].st.name)}</b>: '
                 f"{e(_next_thing(worst[0]))}.</p>")
    elif dark:
        wrong = (f'<p class="wrong">Nothing is behind, but {len(dark)} '
                 f'{"stream needs" if len(dark) == 1 else "streams need"} a look.</p>')
    else:
        wrong = '<p class="wrong ok">Every stream is on track.</p>'

    fresh = f'<p class="fresh">Read {e(clock(page.read_at, page.now))} Pacific.</p>'
    links = ('<p class="links"><a href="/">Everything, stream by stream</a> · '
             f'<a href="{e(page.inbox)}">Decisions waiting for you</a> · '
             '<a href="/standup">The standup room</a></p>')
    return f"{head}{wrong}{body}{fresh}{links}"


CSS = """
:root{color-scheme:dark light}
body{margin:0;padding:2.2rem 2.6rem;background:#0e1013;color:#e8e8ea;
 font:16px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
h1{font-size:1.15rem;font-weight:600;margin:0 0 1rem;color:#aeb4bd}
.ans{display:flex;gap:2.4rem;align-items:baseline;margin:0 0 1.4rem}
.big{font-size:3rem;font-weight:700;margin-right:.5rem;line-height:1}
.bad{color:#ff6b6b}.ok{color:#5ac67a}.dim{color:#8b93a0}
.wrong{margin:0 0 1.4rem;font-size:1.05rem}
.wrong.ok{color:#5ac67a}
ul.streams{list-style:none;margin:0;padding:0;border-top:1px solid #272b31}
ul.streams li{display:grid;grid-template-columns:13rem 11rem 1fr;gap:1rem;align-items:baseline;
 padding:.62rem .2rem;border-bottom:1px solid #1c2026}
ul.streams li.bad .nm{color:#ff6b6b}
ul.streams li.dim{color:#8b93a0}
.nm{font-weight:600}
a{color:inherit;text-decoration:none}
a:hover .nm{text-decoration:underline}
.kv{color:#aeb4bd;font-variant-numeric:tabular-nums}
.nx{color:#c7ccd3}
.fresh{color:#6f7681;font-size:.85rem;margin:1.1rem 0 .3rem}
.links{font-size:.9rem;color:#7d8794}
.links a{text-decoration:underline}
"""


def page_html(inner: str) -> str:
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>Streams</title><style>{CSS}</style></head><body>{inner}</body></html>")
