"""Unit tests for the Pace label, the guard label and the day's plan (dispatch://INFRA-224, "Testing")."""

from datetime import UTC, date, datetime, timedelta

from chief_dashboard import pace as P
from chief_dashboard import plan as L

PT = P.PACIFIC
T0 = datetime(2026, 10, 20, 12, tzinfo=UTC)
BY = "2026-10-30"  # line runs from T0 to 2026-10-31 00:00 Pacific
END = P.target_end(BY)


def at(frac: float) -> datetime:
    return T0 + (END - T0) * frac


def rs(*pairs):
    return list(pairs)


# --- Pace -------------------------------------------------------------------------------------------------------


def test_on_the_line_is_on_pace_both_directions():
    now = at(0.5)
    assert P.pace(rs((T0, 0.0), (now, 5.0)), 10, BY, "higher", 6, now).label == P.ON_PACE
    assert P.pace(rs((T0, 10.0), (now, 5.0)), 0, BY, "lower", 6, now).label == P.ON_PACE


def test_better_and_worse_side_higher():
    now = at(0.5)
    assert P.pace(rs((T0, 0.0), (now, 6.0)), 10, BY, "higher", 6, now).label == P.ON_PACE
    assert P.pace(rs((T0, 0.0), (now, 4.0)), 10, BY, "higher", 6, now).label == P.BEHIND


def test_better_and_worse_side_lower():
    now = at(0.5)
    assert P.pace(rs((T0, 10.0), (now, 4.0)), 0, BY, "lower", 6, now).label == P.ON_PACE
    assert P.pace(rs((T0, 10.0), (now, 6.0)), 0, BY, "lower", 6, now).label == P.BEHIND


def test_target_met_is_on_pace_even_with_one_reading():
    now = at(0.5)
    p = P.pace(rs((now, 12.0)), 10, BY, "higher", 6, now)
    assert (p.label, p.met) == (P.ON_PACE, True)
    assert P.pace(rs((now, 0.0)), 1, BY, "lower", 6, now).label == P.ON_PACE


def test_no_reading_and_one_reading_cant_tell():
    now = at(0.5)
    assert P.pace([], 10, BY, "higher", 6, now).reason == "no reading"
    p = P.pace(rs((now, 3.0)), 10, BY, "higher", 6, now)
    assert (p.label, p.reason) == (P.CANT_TELL, "one reading")


def test_stale_reading_default_interval():
    t_new = at(0.5)
    now = t_new + timedelta(hours=13)  # older than 2 x 6 h
    p = P.pace(rs((T0, 0.0), (t_new, 9.0)), 10, BY, "higher", P.every_hours({}), now)
    assert (p.label, p.reason) == (P.CANT_TELL, "no reading for 13 h")
    assert P.pace(rs((T0, 0.0), (t_new, 9.0)), 10, BY, "higher", 6, t_new + timedelta(hours=11)).label == P.ON_PACE


def test_stale_reading_longer_interval():
    t_new = at(0.3)
    now = t_new + timedelta(hours=100)
    every = P.every_hours({"every": 168})
    assert every == 168
    assert P.pace(rs((T0, 0.0), (t_new, 9.0)), 10, BY, "higher", every, now).label == P.ON_PACE
    assert P.pace(rs((T0, 0.0), (t_new, 9.0)), 10, BY, "higher", every, t_new + timedelta(hours=337)).label == (
        P.CANT_TELL
    )
    assert P.every_hours({"every": 2}) == 6  # 6 unless the lead sets more


def test_needed_per_day():
    now = END - timedelta(days=2)
    p = P.pace(rs((T0, 0.0), (now, 6.0)), 10, BY, "higher", 6, now)
    assert abs(p.needed_per_day - 2.0) < 1e-9


def test_guard_holding_and_worse_both_directions():
    a, b = T0, T0 + timedelta(hours=6)
    assert P.guard(rs((a, 5.0), (b, 5.0)), "higher").label == P.HOLDING
    assert P.guard(rs((a, 5.0), (b, 4.0)), "higher").label == P.WORSE
    assert P.guard(rs((a, 5.0), (b, 4.0)), "lower").label == P.HOLDING
    assert P.guard(rs((a, 5.0), (b, 6.0)), "lower").label == P.WORSE


def test_guard_fewer_than_two_readings():
    assert P.guard([], "higher").label == P.CANT_TELL
    assert P.guard(rs((T0, 1.0)), "lower").label == P.CANT_TELL

# --- A reading that is only a lower bound -----------------------------------------------------------------------


def test_lower_is_better_bound_worse_than_the_line_is_behind():
    now = at(0.5)  # the line is at 5 here
    p = P.pace(rs((T0, 10.0), (now, P.Bound(6.0))), 0, BY, "lower", 6, now)
    assert p.label == P.BEHIND


def test_lower_is_better_bound_on_the_better_side_cant_tell():
    now = at(0.5)
    for bound in (4.0, 5.0):
        p = P.pace(rs((T0, 10.0), (now, P.Bound(bound))), 0, BY, "lower", 6, now)
        assert (p.label, p.reason) == (P.CANT_TELL, "only a lower bound")
    # A bound at or under the target does not meet it: the true value may be above.
    p = P.pace(rs((T0, 10.0), (now, P.Bound(0.5))), 1, BY, "lower", 6, now)
    assert (p.label, p.reason, p.met) == (P.CANT_TELL, "only a lower bound", False)


def test_higher_is_better_bound_at_or_above_the_line_is_on_pace():
    now = at(0.5)  # the line is at 5 here
    for bound in (5.0, 6.0):
        assert P.pace(rs((T0, 0.0), (now, P.Bound(bound))), 10, BY, "higher", 6, now).label == P.ON_PACE
    assert P.pace(rs((now, P.Bound(12.0))), 10, BY, "higher", 6, now).met


def test_higher_is_better_bound_under_the_line_cant_tell():
    now = at(0.5)
    p = P.pace(rs((T0, 0.0), (now, P.Bound(4.0))), 10, BY, "higher", 6, now)
    assert (p.label, p.reason) == (P.CANT_TELL, "only a lower bound")


def test_bound_counts_as_a_reading_for_freshness():
    t_new = at(0.5)
    p = P.pace(rs((T0, 10.0), (t_new, P.Bound(6.0))), 0, BY, "lower", 6, t_new + timedelta(hours=11))
    assert p.label == P.BEHIND
    p = P.pace(rs((T0, 10.0), (t_new, P.Bound(6.0))), 0, BY, "lower", 6, t_new + timedelta(hours=13))
    assert (p.label, p.reason) == (P.CANT_TELL, "no reading for 13 h")


def test_bound_reading_from_the_scoreboard_shows_more_than_and_an_open_dot():
    from chief_dashboard import model, v2_render

    now = at(0.5)
    iso = lambda t: t.isoformat().replace("+00:00", "Z")  # noqa: E731
    raw = {"id": "x", "name": "Turnaround", "role": "main", "unit": "h", "better": "lower",
           "target": {"value": 0.5, "by": BY},
           "values": [{"at": iso(T0), "value": 2.0}, {"at": iso(now), "value": None, "lower_bound": 0.8}]}
    n = model.number_view(raw, now)
    rd = P.readings(n.values)
    assert isinstance(rd[-1][1], P.Bound) and rd[-1][1] == 0.8
    assert n.status != model.MET
    assert ('<span class="pre">more than</span><span class="n">0.8</span>'
            in v2_render.now_value(rd, "h", now))
    g = v2_render.GuardRow(n, P.guard(rd, "lower"), rd)
    assert 'class="dot grey open"' in v2_render.chart(g, now, "grey", "t")
    assert 'class="rl b"' in v2_render.chart(g, now, "grey", "t")  # the segment into a bound is dashed
    assert model.fmt_value(n.latest["value"], "h") == "more than 0.8 h"


def test_check_accepts_null_value_only_with_a_numeric_lower_bound():
    from chief_dashboard import check

    def errs(v):
        b = {"numbers": [{"id": "x", "name": "X", "role": "main", "better": "lower", "unit": "h",
                          "values": [{"at": "2026-10-24T00:00:00Z", "source": "s", **v}]}]}
        return [x for x in check.format_errors(b) if "values[0]" in x]

    assert errs({"value": None, "lower_bound": 0.8}) == []
    assert errs({"value": 3}) == []
    assert errs({"value": None}) != []
    assert errs({"value": None, "lower_bound": None}) != []
    assert errs({"value": None, "lower_bound": "0.8"}) != []



# --- The day's plan ---------------------------------------------------------------------------------------------

D = date(2026, 10, 25)


def pt(h: int, m: int = 0, d: date = D) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=PT)


def version(t: datetime, *items, day: date = D) -> dict:
    return {"created_at": t.astimezone(UTC).isoformat(), "data": {"today": {"date": day.isoformat(), "items": [
        {"what": w, "pr": pr} for w, pr in items
    ]}}}


def merged(t: datetime) -> dict:
    return {"state": "MERGED", "merged_at": t.astimezone(UTC).isoformat()}


def test_changes_before_nine_show_current_list():
    vs = [version(pt(7), ("a", None), ("b", None)), version(pt(8), ("a", None), ("c", None))]
    p = L.day_plan(vs, D, pt(8, 30))
    assert p.before_fix and [i.what for i in p.items] == ["a", "c"]
    p = L.day_plan(vs, D, pt(10))
    assert not p.before_fix and [i.what for i in p.planned] == ["a", "c"]


def test_item_added_after_nine():
    vs = [version(pt(8), ("a", None)), version(pt(11), ("a", None), ("b", None))]
    p = L.day_plan(vs, D, pt(12))
    assert [i.what for i in p.planned] == ["a"]
    (added,) = p.added
    assert added.what == "b" and added.added_at == pt(11)


def test_item_removed_after_nine_still_planned():
    vs = [version(pt(8), ("a", None), ("b", None)), version(pt(11), ("a", None))]
    p = L.day_plan(vs, D, pt(12))
    assert [(i.what, i.removed) for i in p.planned] == [("a", False), ("b", True)]


def test_pr_added_to_planned_item_is_not_an_addition():
    vs = [version(pt(8), ("a", None)), version(pt(11), ("a", "o/r#1"))]
    p = L.day_plan(vs, D, pt(12))
    assert p.added == [] and [(i.what, i.pr) for i in p.planned] == [("a", "o/r#1")]


def test_first_list_after_nine_is_a_late_plan():
    vs = [version(pt(12, 14), ("a", "o/r#1"), ("b", None)), version(pt(15), ("a", "o/r#1"), ("b", None), ("c", None))]
    p = L.day_plan(vs, D, pt(16))
    assert p.fixed and p.late_at == pt(12, 14)
    assert [i.what for i in p.planned] == ["a", "b"]  # the first list of the day is the plan
    (added,) = p.added
    assert added.what == "c" and added.added_at == pt(15)
    assert L.day_record(vs, D, {"o/r#1": merged(pt(17))}) == (1, 2)


def test_plan_fixed_before_nine_is_not_late():
    p = L.day_plan([version(pt(8), ("a", None))], D, pt(12))
    assert p.fixed and p.late_at is None


def test_day_with_no_list():
    p = L.day_plan([], D, pt(12))
    assert not p.fixed and p.late_at is None and p.items == []
    assert L.day_record([], D, {}) is None


def test_merge_outside_the_pacific_day_does_not_count():
    vs = [version(pt(8), ("a", "o/r#1"), ("b", "o/r#2"))]
    states = {"o/r#1": merged(pt(23, 59)), "o/r#2": merged(pt(0, 1, D + timedelta(days=1)))}
    assert L.tally(L.day_plan(vs, D, pt(23, 59)), states) == (1, 2)


def test_count_at_midnight():
    vs = [
        version(pt(8), ("a", "o/r#1"), ("b", "o/r#2")),
        version(pt(14), ("b", "o/r#2"), ("c", "o/r#3")),  # a removed, c added
        version(pt(1, 0, D + timedelta(days=1)), ("next", None), day=D + timedelta(days=1)),
    ]
    states = {"o/r#1": merged(pt(9, 30)), "o/r#2": {"state": "OPEN"}, "o/r#3": merged(pt(15))}
    assert L.day_record(vs, D, states) == (1, 2)
