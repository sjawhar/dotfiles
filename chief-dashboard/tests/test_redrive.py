"""dispatch-check's re-drive: an executor, a message and a time to acknowledge by, for each row that waits on us.

The measurement these tests hold in place (2026-10-29): the 13:10Z board check printed 15 rows Sami had answered that
no session had executed, the oldest 8 d 17 h; a person cleared every one of them by hand in 13 minutes at 13:23Z; and
the 17:10Z check printed the same 12 INFRA rows. Printing does not move the count, so each of these tests asserts
that something left the tool: a message, a recorded time, or a named row nobody can take.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from chief_dashboard import dispatch_check as dc
from chief_dashboard import redrive
from test_dispatch_check import DEAD, LIVE, NOW, SAMI, FakeDispatch, ev, found, issue, no_prs, session

ASKER = "01a1asker-000-0000-0000-00000000"[:8] + "-0000-0000-0000-000000000000"
LEAD = "01a1lead-0000-0000-0000-000000000000"
OFF_ROSTER = "01a15237-75c3-7321-befb-e6e1cc57a948"  # taken off the lead roster on 2026-10-29


class Sent(list):
    """A stand-in for the Envoy hub: records each send and reports it delivered."""

    def __call__(self, sid, message, idempotency_key, source="human"):
        self.append({"to": sid, "message": message, "key": idempotency_key, "source": source})
        return {"recipient": sid}


def driver(tmp_path, sent=None, *, dry_run=False, ack_minutes=60, panes=None, screen="") -> redrive.Driver:
    return redrive.Driver(redrive.Ledger.load(tmp_path / "redrive.json"), send=sent if sent is not None else Sent(),
                          ack_minutes=ack_minutes, dry_run=dry_run,
                          locate=(panes or {}).get, tmux=lambda *a, **k: screen)


def answered(key: str = "BET-1", ask_id: str = "q1", hours: float = 60, author=None, live=(LIVE,),
             **kw) -> FakeDispatch:
    """An issue carrying one ask of Sami's that no session followed, with the ask node AUTHOR opened."""
    events = [ev("ask.answered", SAMI, hours, id=ask_id, question="Do we ship the gate?",
                 author=author, answer={"selected": ["Approve"], "text": "Ship it"})]
    nodes = {ask_id: {"ask": {"id": ask_id, "author": author}, "replies": []}}
    return FakeDispatch([issue(key, status="backlog", events=events, **kw)], nodes=nodes, live=live)


def run(api, drive, scope=None, streams=None, now=NOW, owner=None) -> list[str]:
    return dc.run(api, scope or dc.Scope("BET"), no_prs, [p["key"] for p in api.projects()], streams or {}, now,
                  owner, drive)


def drive_of(lines: list[str]) -> list[str]:
    """What the re-drive did with each row, without the owner the line also carries."""
    return [ln.split(" | drive")[1].split(" | owner: ")[0] for ln in lines if " | drive" in ln]


def test_an_answered_ask_goes_to_the_session_that_asked_with_a_time_to_answer_by(tmp_path):
    sent = Sent()
    api = answered(author=session(LIVE, "PIPE: task pipeline"))
    lines = run(api, driver(tmp_path, sent))

    assert len(sent) == 1 and sent[0]["to"] == LIVE
    assert sent[0]["source"] == "agent"  # the chief's tool carries Sami's answer; it is not Sami
    assert sent[0]["key"] == "redrive:BET-1|ask|q1:1"
    body = sent[0]["message"]
    assert 'His answer: "Approve Ship it"' in body
    assert "2d 12h" in body  # how long it has been unexecuted
    assert "https://dispatch.test/issues/BET-1/asks/q1" in body
    assert "Acknowledge by 2026-10-25T13:00:00Z, in 1h 0m" in body
    assert "1. that you have it," in body and "3. when it is done." in body
    assert drive_of(lines) == [f" 1: PIPE: task pipeline ({LIVE[:8]}); sent; acknowledge by 2026-10-25T13:00:00Z"]
    rows = redrive.Ledger.load(tmp_path / "redrive.json").rows
    assert rows["BET-1|ask|q1"]["due"] == "2026-10-25T13:00:00Z"
    assert rows["BET-1|ask|q1"]["attempts"] == [
        {"at": "2026-10-25T12:00:00Z", "executor": LIVE, "name": "PIPE: task pipeline",
         "due": "2026-10-25T13:00:00Z", "delivered": True}]


def test_a_row_is_driven_once_inside_its_time_and_again_past_it_and_the_count_is_reported(tmp_path):
    """The number to drive to zero is PAST-ACK, not the raw waiting count."""
    sent = Sent()
    api = answered(author=session(LIVE, "PIPE"))
    first = run(api, driver(tmp_path, sent))
    assert "0 PAST-ACK" in first[-1] and len(sent) == 1

    inside = run(api, driver(tmp_path, sent), now=NOW + timedelta(minutes=30))
    assert len(sent) == 1, "a second run inside the time must not send again"
    assert drive_of(inside) == [f": PIPE ({LIVE[:8]}) was sent this 12:00Z, acknowledgement due 13:00Z, in 30m"]
    assert "0 PAST-ACK" in inside[-1]

    past = run(api, driver(tmp_path, sent), now=NOW + timedelta(minutes=95))
    assert len(sent) == 2, "past its time the row is driven again"
    assert sent[1]["key"] == "redrive:BET-1|ask|q1:2"
    assert "This is drive 2 of this row. Drive 1 got no reply in its thread." in sent[1]["message"]
    assert found(past, "PAST-ACK") == [
        "BET-1 ask q1: no reply in its thread 35m after the time set (2026-10-25T13:00:00Z); 1 drive(s) since "
        "2026-10-25T12:00:00Z; BET-1 answer \"Approve Ship it\" to: Do we ship the gate? (sami, 2d 13h ago; "
        "no event from t"]
    assert "1 PAST-ACK" in past[-1]


def test_a_row_nothing_can_take_is_named_not_dropped(tmp_path):
    """A check that cannot report its own non-match is the defect this whole day was about: when the re-drive
    matches nothing, every row it could not place is printed and counted."""
    sent = Sent()
    api = answered(author=None)  # no asking session, no claim on the issue, no stream
    lines = run(api, driver(tmp_path, sent))

    assert sent == [], "nothing is invented to send to"
    assert found(lines, "NO-EXECUTOR") == [
        "BET-1 ask q1 has waited 2d 12h and nothing can take it: no session opened it, no session claims the issue, "
        "and no stream of BET names a lead. Place it by hand (https://dispatch.test/issues/BET-1/asks/q1); "
        "BET-1 answer \"Approve Ship it\" to: Do we ship the gate? (sami, 2d 12h ago; no event from t"]
    assert "1 NO-EXECUTOR" in lines[-1]
    assert drive_of(lines) == [": NO EXECUTOR (no session opened it, no session claims the issue, and no stream of "
                               "BET names a lead)"]
    # It is remembered, so the next run does not start its clock over.
    assert redrive.Ledger.load(tmp_path / "redrive.json").rows["BET-1|ask|q1"]["due"] is None


def test_a_person_is_never_an_executor(tmp_path):
    """Executors are agent sessions. A claim a person holds is passed over for the stream's lead."""
    sent = Sent()
    api = answered(claim={"kind": "user", "id": "sami@theorem.dev"}, parent="BET-59", live=(LIVE, LEAD))
    api.issues["BET-59"] = issue("BET-59", status="backlog", labels=["stream"])
    streams = {"BET-59": {"project": "BET", "lead": {"title": "FRAC", "session": LEAD}}}
    lines = run(api, driver(tmp_path, sent), streams=streams)

    assert [s["to"] for s in sent] == [LEAD]
    assert "you lead stream BET-59, which BET-1 belongs to" in sent[0]["message"]
    assert "sami@theorem.dev" not in sent[0]["message"]
    assert drive_of(lines) == [f" 1: FRAC ({LEAD[:8]}); sent; acknowledge by 2026-10-25T13:00:00Z"]


def test_the_claim_holder_takes_it_when_no_session_opened_the_ask(tmp_path):
    sent = Sent()
    api = answered(author=None, claim=session(LIVE, "OWNER: the claim"))
    run(api, driver(tmp_path, sent))
    assert [s["to"] for s in sent] == [LIVE]
    assert "you claim BET-1" in sent[0]["message"]


def test_an_issue_under_no_stream_falls_back_to_the_project_s_lead(tmp_path):
    """The 2026-10-29 INFRA run: 7 of the 14 waiting rows hung off issues with no stream among their parents, so a
    stream-only rule placed none of them."""
    sent = Sent()
    api = answered(author=None, live=(LIVE, LEAD))
    streams = {"BET-59": {"project": "BET", "lead": {"title": "FRAC", "session": LEAD}}}
    run(api, driver(tmp_path, sent), streams=streams)
    assert [s["to"] for s in sent] == [LEAD]
    assert "you lead BET's stream BET-59 and nothing nearer the issue can take this" in sent[0]["message"]


def test_a_parked_legion_row_goes_to_its_controller_and_to_no_other_lead(tmp_path):
    """A Legion architect's claim outlives its process, so Envoy not listing it means parked, not gone. Its tree is
    woken by that project's Legion controller, which also triages the board; another stream's lead cannot take it."""
    sent = Sent()
    controller = "01a1ctrl-0000-0000-0000-000000000000"
    api = answered(author=session(DEAD, "Legion architect · BET-1"), live=(LIVE, LEAD, controller))
    coach = {"BET-59": {"project": "BET", "lead": {"title": "SPEEDCOACH", "session": LEAD}}}
    only_coach = run(api, driver(tmp_path, sent, panes={}), streams=coach)
    assert sent == [], "a stream coach is not asked to run somebody's Legion tree"
    assert "Legion architect · BET-1 (01a1dead) is not listening on Envoy" in found(only_coach, "NO-EXECUTOR")[0]

    both = dict(coach, **{"BET-83": {"project": "BET", "lead": {"title": "Legion controller · BET",
                                                                "session": controller}}})
    lines = run(api, driver(tmp_path / "b", sent, panes={}), streams=both)
    assert [s["to"] for s in sent] == [controller]
    assert found(lines, "NO-EXECUTOR") == []


def test_a_legion_labelled_issue_takes_the_controller_even_when_a_session_opened_the_ask(tmp_path):
    sent = Sent()
    controller = "01a1ctrl-0000-0000-0000-000000000000"
    api = answered(author=None, labels=["legion"], live=(LIVE, LEAD, controller))
    streams = {"BET-59": {"project": "BET", "lead": {"title": "FRAC", "session": LEAD}},
               "BET-83": {"project": "BET", "lead": {"title": "Legion controller · BET", "session": controller}}}
    run(api, driver(tmp_path, sent), streams=streams)
    assert [s["to"] for s in sent] == [controller]


def test_a_paused_session_keeps_its_row_and_the_registry_alone_never_decides(tmp_path):
    """2026-10-29: a paused lane was restarted because a check could not tell 'paused' from 'wedged'. A session the
    agent registry does not list is looked up in this box's panes and its pane is read for the pause screen."""
    sent = Sent()
    api = answered(author=session(DEAD, "PAUSED: a lane"))  # DEAD is simply absent from /api/v1/agents
    d = driver(tmp_path, sent, panes={DEAD: "%20"}, screen="\n  P A U S E D  \n  press enter to resume\n")
    lines = run(api, d)

    assert [s["to"] for s in sent] == [DEAD], "a paused session is alive and keeps the row"
    assert drive_of(lines) == [f" 1: PAUSED: a lane ({DEAD[:8]}), paused: the message waits for it; sent; "
                               "acknowledge by 2026-10-25T13:00:00Z"]


def test_a_session_with_no_pane_is_passed_over_and_is_never_called_dead(tmp_path):
    sent = Sent()
    api = answered(author=session(DEAD, "LANE: an unlisted lane"), parent="BET-59", live=(LIVE, LEAD))
    api.issues["BET-59"] = issue("BET-59", status="backlog", labels=["stream"])
    streams = {"BET-59": {"project": "BET", "lead": {"title": "FRAC", "session": LEAD}}}
    lines = run(api, driver(tmp_path, sent, panes={}), streams=streams)

    assert [s["to"] for s in sent] == [LEAD]
    assert "LANE" not in " ".join(drive_of(lines))  # it was passed over for the lead, not reported
    api2 = answered(author=session(DEAD, "LANE: an unlisted lane"))
    alone = run(api2, driver(tmp_path / "b", Sent(), panes={}))
    why = found(alone, "NO-EXECUTOR")[0]
    assert "LANE: an unlisted lane (01a1dead) is not listening on Envoy and has no pane on this box" in why
    # It says what was checked, never that the session is gone for good, and never that it should be restarted.
    assert not re.search(r"\b(dead|gone|wedged|restart\w*)\b", why, re.I)


def test_a_session_taken_off_the_lead_roster_is_not_a_lead(tmp_path):
    """The four sessions removed from the roster on 2026-10-29 are not leads, whatever a scoreboard still says."""
    sent = Sent()
    api = answered(author=None, parent="BET-59")
    api.issues["BET-59"] = issue("BET-59", status="backlog", labels=["stream"])
    streams = {"BET-59": {"project": "BET", "lead": {"title": "OMP PO", "session": OFF_ROSTER}}}
    lines = run(api, driver(tmp_path, sent, panes={OFF_ROSTER: "%4"}), streams=streams)

    assert sent == []
    assert found(lines, "NO-EXECUTOR"), "it is reported, not quietly handed to a session off the roster"
    assert OFF_ROSTER not in " ".join(lines)
    assert "stream BET-59 (its scoreboard names no lead)" in lines[0]


def test_a_dry_run_names_the_executor_and_sends_and_records_nothing(tmp_path):
    sent = Sent()
    api = answered(author=session(LIVE, "PIPE"))
    lines = run(api, driver(tmp_path, sent, dry_run=True))

    assert sent == [], "a dry run sends nothing"
    assert not (tmp_path / "redrive.json").exists(), "a dry run records nothing"
    assert drive_of(lines) == [f" 1: PIPE ({LIVE[:8]}); would send; acknowledge by 2026-10-25T13:00:00Z"]


def test_a_row_that_stopped_waiting_closes_its_record(tmp_path):
    """A session reply in the thread takes the row off the list; the record closes, so a later recurrence is drive 1
    again rather than an endless escalation."""
    sent = Sent()
    api = answered(author=session(LIVE, "PIPE"))
    run(api, driver(tmp_path, sent))
    api.issues["BET-1"]["events"].append(ev("comment.created", session(LIVE), 1, id="c9", body="On it."))
    api.issues["BET-1"]["last_seq"] = len(api.issues["BET-1"]["events"])
    lines = run(api, driver(tmp_path, sent))

    assert found(lines, "WAITING-ON-US") == [] and len(sent) == 1
    assert redrive.Ledger.load(tmp_path / "redrive.json").rows["BET-1|ask|q1"]["cleared_at"] == "2026-10-25T12:00:00Z"


def test_a_hub_failure_is_reported_on_the_row_and_the_run_goes_on(tmp_path):
    def refuse(sid, message, idempotency_key, source="human"):
        raise OSError("hub down")

    api = answered(author=session(LIVE, "PIPE"))
    lines = run(api, driver(tmp_path, refuse))
    assert drive_of(lines) == [f" 1: PIPE ({LIVE[:8]}); NOT DELIVERED (hub down); acknowledge by "
                               "2026-10-25T13:00:00Z"]


def test_a_leads_own_run_drives_only_its_own_rows(tmp_path):
    sent = Sent()
    api = answered(author=session(LIVE, "PIPE"))
    lines = run(api, driver(tmp_path, sent), owner=LEAD)
    assert sent == [] and found(lines, "WAITING-ON-US") == []


def test_an_open_ask_waiting_on_an_agent_is_driven_too(tmp_path):
    sent = Sent()
    ask = {"id": "q5", "waiting_on": "agent", "age_seconds": 7200, "question": "Which gate?",
           "ref": "/issues/BET-1/asks/q5", "owner": {"issue": {"key": "BET-1"}}}
    api = FakeDispatch([issue("BET-1", status="backlog")], asks=[ask],
                       nodes={"q5": {"ask": {"id": "q5", "author": session(LIVE, "PIPE")}, "replies": []}})
    lines = run(api, driver(tmp_path, sent))
    assert [s["to"] for s in sent] == [LIVE]
    assert "This has waited 2h 0m for an agent" in sent[0]["message"]
    assert drive_of(lines) == [f" 1: PIPE ({LIVE[:8]}); sent; acknowledge by 2026-10-25T13:00:00Z"]


@pytest.mark.parametrize(("screen", "state"), [("the agent is working", "running"), ("  P A U S E D  ", "paused")])
def test_liveness_reads_the_pane_before_it_passes_a_session_over(screen, state):
    seen = []
    live = redrive.Liveness({}, locate=lambda sid: "%7", tmux=lambda *a, **k: seen.append(a) or screen)
    assert live.state(DEAD) == state
    assert ("capture-pane", "-p", "-t", "%7") in seen, "the pane is read, not only the registry"
    assert redrive.Liveness({DEAD: "t"}, locate=lambda sid: None).state(DEAD) == "running"
    assert redrive.Liveness({}, locate=lambda sid: None).state(DEAD) == "elsewhere"


def test_panes_asks_tmux_nothing_when_nothing_is_wanted():
    """Reading one pane's session id walks its process tree. An empty lookup must cost no walk: the first version of
    this walked every process on the box and did not return."""
    calls = []
    assert redrive.panes(set(), tmux=lambda *a, **k: calls.append(a) or "%1\t1\n") == {}
    assert calls == []


def test_the_ledger_survives_a_broken_file(tmp_path):
    bad = tmp_path / "redrive.json"
    bad.write_text("{not json")
    led = redrive.Ledger.load(bad)
    assert led.rows == {}
    led.rows["BET-1|ask|q1"] = {"issue": "BET-1", "attempts": []}
    led.save()
    assert redrive.Ledger.load(bad).rows["BET-1|ask|q1"]["issue"] == "BET-1"


def test_utc_times_are_written_without_microseconds():
    assert redrive.iso(datetime(2026, 10, 29, 17, 10, 5, 123456, tzinfo=UTC)) == "2026-10-29T17:10:05Z"
