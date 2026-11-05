"""An experiment with no owner gets no prediction and no before-value, so the check names it."""

from datetime import UTC, datetime

from test_dashboard import board, h, well_formed

from chief_dashboard import check

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _exp(**over) -> dict:
    x = {
        "id": "e1",
        "number": "usable",
        "change": "Dose every package from the shared pool",
        "hypothesis": "More packages land in band",
        "verdict": "running",
        "started": h(5),
    }
    x.update(over)
    return x


def test_an_experiment_without_an_owner_warns_and_one_with_an_owner_does_not():
    b = well_formed(board(experiments=[_exp()]))
    errors, lines = check.report(b, NOW)
    assert errors == []
    assert [ln for ln in lines if "names no `owner`" in ln] == [
        "Warning: experiments[0] ('e1') names no `owner`; an experiment with no owner gets no prediction and no "
        "before-value. Give a session id or a role."
    ]

    for owner in ("lead-oss-verify", "01a14b36-227b-7141-acdb-b5397251a0b1"):
        owned = well_formed(board(experiments=[_exp(owner=owner)]))
        assert not [ln for ln in check.report(owned, NOW)[1] if "names no `owner`" in ln]


def test_a_blank_owner_counts_as_none_and_each_unowned_experiment_is_named():
    b = well_formed(board(experiments=[_exp(owner="   "), _exp(id="e2", owner="lead-oss-verify"), _exp(id="e3")]))
    lines = check.report(b, NOW)[1]
    named = [ln for ln in lines if "names no `owner`" in ln]
    assert len(named) == 2
    assert "experiments[0] ('e1')" in named[0] and "experiments[2] ('e3')" in named[1]
