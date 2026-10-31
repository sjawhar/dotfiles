"""This week's plan: the `week` list of the newest scoreboard version whose `week.start` is this Pacific week's Monday,
each item with its due day and Dispatch issue, and `chief-dashboard check` holding the field to its contract."""

from __future__ import annotations

from datetime import date

from test_dashboard import NOW, board, h, snapshot, well_formed

from chief_dashboard import check, v2_model, v2_render
from chief_dashboard.plan import week_plan, week_start
from chief_dashboard.plan_issues import plan_items

MONDAY = "2026-10-19"  # NOW is Saturday Oct 24, 05:00 Pacific: its week starts Monday Oct 19


def versions(*weeks) -> list[dict]:
    return [{"number": i + 1, "created_at": at, "data": {"week": w}} for i, (at, w) in enumerate(weeks)]


def test_the_newest_version_for_this_week_wins_and_other_weeks_are_ignored():
    assert week_start(date(2026, 10, 24)) == date(2026, 10, 19)
    vs = versions(
        (h(50), {"start": MONDAY, "items": [{"what": "old list"}]}),
        (h(20), {"start": MONDAY, "items": [{"what": "Land the gate", "issue": "BET-12", "due": "2026-10-23"}]}),
        (h(2), {"start": "2026-10-12", "items": [{"what": "last week's list, uploaded late"}]}),
        ("2026-10-24T13:00:00Z", {"start": MONDAY, "items": [{"what": "uploaded after NOW"}]}),
    )
    w = week_plan(vs, NOW)
    assert [(i.what, i.issue, i.due) for i in w.items] == [("Land the gate", "BET-12", date(2026, 10, 23))]
    assert w.start == date(2026, 10, 19) and w.set_at is not None
    assert week_plan(versions((h(2), {"start": "2026-10-12", "items": [{"what": "x"}]})), NOW).items == []


def test_week_items_are_read_for_their_issues_and_shown_with_due_day_and_issue():
    b = board(week={"start": MONDAY, "items": [
        {"what": "Ship the Lean grader", "issue": "BET-59", "due": "2026-10-25"},
        {"what": "Write the runbook"},
    ]})
    snap = snapshot([b])
    assert [i.what for i in plan_items(snap["scoreboards"], NOW)][-2:] == ["Ship the Lean grader", "Write the runbook"]
    snap["plan_issues"] = {"BET-59": {"key": "BET-59", "title": "t", "status": "todo", "priority": 0, "assignee": None,
                                      "claim": None, "route_holder": "s-9", "external_links": []}}
    page = v2_model.build(snap, NOW)
    v = next(x for x in page.streams if x.st.issue == "BET-83")
    html = v2_render.week_section(v, NOW)
    assert "This week's plan" in html and "0 of 2 merged" in html
    assert html.index("due Sun Oct 25") < html.index("Ship the Lean grader")  # due items first, due day on the line
    assert "/agents/s-9/live" in html and html.count("No Dispatch issue") == 1
    empty = next(x for x in page.streams if x.st.issue == "OSSV-142")
    assert "No plan" in v2_render.week_section(empty, NOW)


def test_check_holds_week_to_its_contract():
    ok = well_formed(board(week={"start": MONDAY, "items": [{"what": "a", "issue": "BET-1", "due": "2026-10-23"}]}))
    assert check.report(ok, NOW)[0] == []
    bad = well_formed(board(week={"start": "2026-10-20", "items": [{"what": "a", "due": "Friday"}, {"pr": "x"}]}))
    errors, lines = check.report(bad, NOW)
    assert errors == [
        "week.items[1]: must be an object with what",
        "week.start: '2026-10-20' must be the Monday of the Pacific week, as YYYY-MM-DD",
        "week.items[0].due: 'Friday' must be a Pacific day, as YYYY-MM-DD",
    ]
    assert any("week.items[0]" in ln and "names no Dispatch issue" in ln for ln in lines)
    assert any("no `week` list" in ln for ln in check.report(well_formed(board()), NOW)[1])
