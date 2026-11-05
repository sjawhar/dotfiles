"""The front page's experiments: each one's verdict, what changed, the number it moves, the value it started from
with the lead's own note for it, and whether a prediction was registered before the run — said in those words when
none was. Until 2026-11-05 none of this reached the front page: only /v1 drew experiments, so a lead's predictions
and before-values were invisible on the page Sami reads.

Also the `today` plan's `due`: the checker's contract is the reader's (plan.py), which takes a missing one."""

from __future__ import annotations

from test_dashboard import NOW, board, h, snapshot, well_formed

from chief_dashboard import check, v2_model, v2_render

WEB = "https://dispatch.example"


def exp(**over) -> dict:
    x = {
        "id": "e3",
        "change": "Hand the held pieces to GPT workers",
        "hypothesis": "The refusals come from the safeguards, not from the work",
        "number": "fast",
        "started": h(6),
        "ended": h(3),
        "before": 31,
        "after": 31,
        "verdict": "inconclusive",
    }
    x.update(over)
    return x


def view(*exps):
    page = v2_model.build(snapshot([board(experiments=list(exps))]), NOW)
    return page, next(v for v in page.streams if v.st.issue == "BET-83")


def test_the_front_page_shows_an_experiment_with_its_before_value_and_says_no_prediction_was_registered():
    page, v = view(exp(before_note="held pieces"))
    html = v2_render.stream_section(v, page, [])
    # The section is on the front page's stream view, listed in what the folded block says it holds.
    assert "experiments, bottleneck" in html and '<span class="sec-t">Experiments</span>' in html
    # The conclusion leads the row, then what changed, then the number with the value it started from.
    assert html.index(">inconclusive<") < html.index("Hand the held pieces to GPT workers")
    assert "fast: 31 held pieces → 31" in html  # `before_note` is read: the lead's prose beside its value
    # The honest state the lead wrote: no prediction, said in words, on the page Sami reads.
    assert v2_model.NO_PREDICTION == "no prediction registered before the run"
    assert f'class="xp nopred">{v2_model.NO_PREDICTION}<' in html
    assert "1 of 1 with no prediction" in html


def test_a_registered_prediction_is_shown_instead_of_the_warning():
    for field, text in (("predicted", "predicted 1.5"), ("predicted_main", "refusal stops fall to 0 a day")):
        page, v = view(exp(**{field: 1.5 if field == "predicted" else text}))
        html = v2_render.exps_section(v, NOW, WEB)
        assert text in html and v2_model.NO_PREDICTION not in html
        assert "1 all predicted" in html


def test_every_experiment_reaches_the_page_and_a_stream_with_none_says_so():
    page, v = view(exp(id="e1", verdict="kept", predicted=1.0), exp(id="e2", verdict=None, started=None, before=None))
    html = v2_render.exps_section(v, NOW, WEB)
    assert html.count("<li class=") == 2 and "1 of 2 with no prediction" in html
    # Newest first, and an experiment the lead has not started or judged still shows, with what it lacks.
    assert html.index("no verdict yet") < html.index(">kept<")
    assert "fast: no before-value" in html and "not started" in html
    assert "None recorded" in v2_render.exps_section(view()[1], NOW, WEB)


def test_the_checker_takes_a_today_item_with_no_due_and_names_a_malformed_one():
    """plan.py reads `due` as optional, so the checker must not refuse a file for a missing one: requiring it
    refused a whole plan file on 2026-11-05 and the board then showed the previous day's work."""
    ok = well_formed(board(today={"date": "2026-10-24", "items": [
        {"what": "Land the gate", "issue": "BET-12"},
        {"what": "Ship the runbook", "due": "2026-10-25"},
    ]}))
    assert check.report(ok, NOW)[0] == []

    bad = well_formed(board(today={"date": "2026-10-24", "items": [{"what": "Land the gate", "due": "Friday"}]}))
    assert check.report(bad, NOW)[0] == [
        "today.items[0].due ('Land the gate'): 'Friday' must be a Pacific day, as YYYY-MM-DD"
    ]
