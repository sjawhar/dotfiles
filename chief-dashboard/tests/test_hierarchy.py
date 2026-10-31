"""The front page's metrics at a glance: each main number's gap to the line Pace judges against, a chart whose scale
follows the readings rather than a far target, guards that fold away unless they got worse, and one list of every
main number behind its line across the streams."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from test_dashboard import NOW, board, h, snapshot

from chief_dashboard import pace as P
from chief_dashboard import v2_model, v2_render

T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
BY = "2026-10-30"


def test_gap_is_signed_so_that_behind_is_negative_in_both_directions():
    now = T0 + timedelta(days=5)
    rs = [(T0, 0.0), (now, 2.0)]
    p = P.pace(rs, 10, BY, "higher", 6, now)
    expected, gap = v2_model.line_gap(rs, p, "higher")
    assert p.label == P.BEHIND
    assert expected == P.line_at(rs[0], 10.0, p.end, now) and gap == 2.0 - expected < 0

    down = [(T0, 10.0), (now, 9.0)]  # lower is better, barely moved: above the line, so behind
    p = P.pace(down, 0, BY, "lower", 6, now)
    expected, gap = v2_model.line_gap(down, p, "lower")
    assert p.label == P.BEHIND and gap == expected - 9.0 < 0

    ahead = [(T0, 10.0), (now, 1.0)]
    p = P.pace(ahead, 0, BY, "lower", 6, now)
    assert v2_model.line_gap(ahead, p, "lower")[1] > 0


def test_no_gap_without_a_line_or_once_the_target_is_met():
    now = T0 + timedelta(days=1)
    met = [(T0, 0.0), (now, 12.0)]
    assert v2_model.line_gap(met, P.pace(met, 10, BY, "higher", 6, now), "higher") == (None, None)
    assert v2_model.line_gap(met, P.pace(met, None, None, "higher", 6, now), "higher") == (None, None)


def test_a_far_target_does_not_flatten_the_readings():
    lo, hi = v2_render.chart_domain([1.0, 3.0, 5.0], 1000.0)
    assert hi < 6 and lo > 0  # the readings fill the height; the target is named, not drawn
    lo, hi = v2_render.chart_domain([1.0, 3.0, 5.0], 8.0)
    assert hi >= 8  # a target within one span of the readings is drawn
    assert v2_render.chart_domain([0.0, 5.0], None)[0] == 0.0  # a count's axis never goes below zero
    assert v2_render.chart_domain([-2.0, 5.0], None)[0] < -2.0


def test_chart_names_a_target_out_of_range_and_marks_where_the_line_says_the_number_should_be():
    n_raw = board()["numbers"][0]
    n_raw["target"] = {"value": 400, "by": "2026-10-31"}
    n_raw["values"] = [{"at": h(48), "value": 0}, {"at": h(2), "value": 2}]
    page = v2_model.build(snapshot([board(numbers=[n_raw])]), NOW)
    k = next(v for v in page.streams if v.st.issue == "BET-83").kpis[0]
    svg = v2_render.chart(k, NOW, "bad", "t")
    assert "target 400 by Oct 31 ↑" in svg
    assert 'class="exp"' in svg and 'class="gap bad"' in svg


def behind_board() -> dict:
    b = board()
    b["numbers"][0]["values"] = [{"at": h(48), "value": 0}, {"at": h(2), "value": 0.5}]  # under the line
    return b


def test_behind_strip_lists_every_behind_number_across_streams_and_links_its_stream():
    page = v2_model.build(snapshot([behind_board()]), NOW)
    assert [(v.st.slug, k.num.id) for v, k in page.behind] == [("task-making", "usable")]
    strip = v2_render.behind_strip(page)
    assert re.findall(r'<a href="#s-([^"]+)">', strip) == ["task-making"]
    assert "behind" in strip and "Target ≥ 4 by Oct 31" in strip

    on_pace = v2_model.build(snapshot(), NOW)
    assert on_pace.behind == [] and "No main number is behind its line." in v2_render.behind_strip(on_pace)


def test_row_shows_the_verdict_the_gap_and_folds_holding_guards():
    b = behind_board()
    b["numbers"].append({"id": "q", "name": "Escapes per task", "role": "guard", "guards": "usable", "unit": "",
                         "better": "lower", "values": [{"at": h(30), "value": 1}, {"at": h(3), "value": 4}]})
    page = v2_model.build(snapshot([b]), NOW)
    k = next(v for v in page.streams if v.st.issue == "BET-83").kpis[0]
    card = v2_render.kpi_row(k, NOW)
    assert card.startswith('<div class="kr bad">') and ">Behind<" in card and '<svg class="kchart"' in card
    # The name is a line of its own, and the figures that are compared stand in one column with their own words.
    assert ('<h3 class="kn" title="Usable task types (passed QA)">Usable task types '
            '<span class="lbl bad">Behind</span></h3>' in card)
    assert re.search(r'class="fig-k">Now</span><span class="fig-v"><span class="n">0\.5</span>', card)
    assert re.search(r'class="fig-k">Pace line</span><span class="fig-v"><span class="n">[\d.]+</span>'
                     r'</span><span class="fig-q"><b class="gapw">0\.\d+ types behind</b>', card)
    assert re.search(r'class="fig-k">Target</span><span class="fig-v"><span class="pre">≥</span><span class="n">4</span>'
                     r'</span><span class="fig-q">by Oct 31 · \+[\d.]+ per day · \d+ d left<', card)
    outside, _, folded = card.partition("<details")
    assert "Escapes per task" in outside  # the guard that got worse is shown in full, outside the fold
    assert "Tokens per usable task" in folded and "Tokens per usable task" not in outside
    assert re.search(r"<summary>1 guard: 1 can.{1,6}t tell</summary>", folded)
