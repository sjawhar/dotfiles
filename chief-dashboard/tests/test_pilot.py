"""The stream-pilot parser, scheduler and event-only measurement use recorded Dispatch shapes."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from chief_dashboard import pilot


SESSION = "01a1beef-0000-0000-0000-000000000000"
OTHER = "01a1cafe-0000-0000-0000-000000000000"
START = datetime(2026, 10, 27, 14, 30, tzinfo=UTC)
END = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)

RECORD = f"""# Pilot: stream record

## Order of work

| # | Issue | Outcome, and what makes it done | Owner session | State |
| --- | --- | --- | --- | --- |
| 1 | [INFRA-10](https://dispatch.test/issues/INFRA-10) | Deliver the thing. | {SESSION} | waits on INFRA-9 |
| 2 | <https://dispatch.test/issues/INFRA-11> | Measure it. | none | ready |

## Open commitments

- [ ] Finish the thing · owner {SESSION} · due 2026-11-03T06:30Z · https://dispatch.test/issues/INFRA-10
- [ ] Explain the risk · owner {OTHER} · due 2026-10-30T06:30Z · https://dispatch.test/issues/INFRA-11
"""


def actor(session: str = SESSION) -> dict:
    return {
        "kind": "session",
        "id": session,
        "origin": {"session_title": f"pilot {session[:8]}"},
        "owner": "sami@theorem.dev",
    }


def event(key: str, seq: int, type_: str, when: str, who: dict, payload: dict) -> dict:
    """An event in the shape GET /api/v1/issues/{key}/events returned on 2026-11-02 (INFRA-167, INFRA-243, INFRA-975)."""
    return {
        "id": seq,
        "issue_key": key,
        "project": key.split("-", 1)[0],
        "seq": seq,
        "type": type_,
        "actor": who,
        "created_at": when,
        "payload": payload,
    }


def created(key: str, seq: int, when: str, parent: str | None = None, refs=()) -> dict:
    return event(key, seq, "issue.created", when, actor(), {
        "key": key,
        "title": f"Title {key}",
        "parent": parent,
        "status": "triage",
        "references_changed": [{"kind": "issue", "id": ref} for ref in refs],
    })


def claimed(key: str, seq: int, when: str) -> dict:
    return event(key, seq, "issue.claimed", when, actor(), {
        "key": key,
        "claim": {"at": when, "actor": actor()},
        "reason": "claimed",
        "status": "in_progress",
    })


def released(key: str, seq: int, when: str, status: str = "in_progress") -> dict:
    """INFRA-243's release: `issue.released`, claim null, the holder in previous_claim."""
    return event(key, seq, "issue.released", when, actor(), {
        "key": key, "claim": None, "reason": "released", "status": status,
        "previous_claim": {"at": when, "actor": actor()},
    })


def closed(key: str, seq: int, when: str, links=()) -> dict:
    """INFRA-975's close: `issue.closed` with status done and `changed` naming status and closed_at; the payload is
    the issue at that moment, its `external_links` included (BET-352's close carried its commit link there)."""
    return event(key, seq, "issue.closed", when, actor(), {
        "key": key, "status": "done", "changed": ["status", "closed_at"], "closed_at": when,
        "external_links": [{"url": url, "kind": "url"} for url in links],
    })


def reopened(key: str, seq: int, when: str) -> dict:
    return event(key, seq, "issue.updated", when, actor(), {"key": key, "status": "in_progress", "changed": ["status"]})


def reason(key: str, seq: int, when: str, body: str) -> dict:
    """`dispatch issue-update --status done --reason` posts the reason as this message just before the close."""
    return event(key, seq, "message.created", when, actor(), {"id": f"m-{key}", "body": body, "target": None})


def test_record_parsing_keeps_template_rows_commitments_and_malformed_ledger():
    rows = pilot.parse_order_of_work(RECORD)
    assert rows == [
        {"issue": "INFRA-10", "owner": SESSION, "owner_session": SESSION, "waits_on": ["INFRA-9"],
         "state": "waits on INFRA-9"},
        {"issue": "INFRA-11", "owner": "none", "owner_session": None, "waits_on": [], "state": "ready"},
    ]

    commitments = pilot.parse_commitments(RECORD, datetime(2026, 11, 1, tzinfo=UTC))
    assert commitments == [
        {"text": "Finish the thing", "line": f"Finish the thing · owner {SESSION} · due 2026-11-03T06:30Z · "
         "https://dispatch.test/issues/INFRA-10", "link": "https://dispatch.test/issues/INFRA-10",
         "owner_session": SESSION, "issue": "INFRA-10", "due": "2026-11-03T06:30Z", "overdue": False},
        {"text": "Explain the risk", "line": f"Explain the risk · owner {OTHER} · due 2026-10-30T06:30Z · "
         "https://dispatch.test/issues/INFRA-11", "link": "https://dispatch.test/issues/INFRA-11",
         "owner_session": OTHER, "issue": "INFRA-11", "due": "2026-10-30T06:30Z", "overdue": True},
    ]

    good = pilot.parse_ledger_line("LEDGER handoff INFRA-10 outcome moved https://github.com/org/repo/pull/1")
    assert good == {
        "kind": "handoff",
        "issue": "INFRA-10",
        "text": "outcome moved",
        "link": "https://github.com/org/repo/pull/1",
    }
    assert pilot.parse_ledger_line("LEDGER impossible INFRA-10 no link") == {
        "raw": "LEDGER impossible INFRA-10 no link",
        "malformed": True,
    }


def test_record_parser_keeps_columns_after_an_escaped_pipe_and_iso_offsets_convert_to_utc():
    record = f"""## Order of work

| # | Issue | Outcome, and what makes it done | Owner session | State |
| --- | --- | --- | --- | --- |
| 1 | https://dispatch.test/issues/INFRA-10 | Deliver foo \\| bar. | {SESSION} | waits on INFRA-9 |
"""
    assert pilot.parse_order_of_work(record) == [{
        "issue": "INFRA-10", "owner": SESSION, "owner_session": SESSION, "waits_on": ["INFRA-9"],
        "state": "waits on INFRA-9",
    }]
    assert pilot._cells(record.splitlines()[-1]) == [
        "1", "https://dispatch.test/issues/INFRA-10", "Deliver foo | bar.", SESSION, "waits on INFRA-9"]
    # The summary skips a row whose owner column names a person: the column's text is kept for it.
    person = record.replace(f"| {SESSION} |", "| Aengus Lynch |")
    assert [(r["owner"], r["owner_session"]) for r in pilot.parse_order_of_work(person)] == [("Aengus Lynch", None)]
    assert pilot.parse_time("2026-11-02T06:30:00-08:00") == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
    assert pilot.parse_time("2026-11-02T06:30:00") == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)



def test_started_pane_counts_after_two_hours_only_while_omp_runs_in_it(tmp_path):
    starts = tmp_path / "started.tsv"
    starts.write_text("INFRA-10\t%17\t2026-11-01T15:00:00Z\n")
    now = datetime(2026, 11, 1, 18, tzinfo=UTC)

    assert pilot._recent_starts(starts, now, {"%17"}) == {"INFRA-10": {"pane": "%17", "at": "2026-11-01T15:00:00Z"}}
    # `agent-new` leaves `exec bash` in the pane after omp exits; that must not block a later summary.
    assert pilot._recent_starts(starts, now, set()) == {}
    # The allowance prevents a race while agent-new has not yet replaced `pending` with a pane id.
    starts.write_text("INFRA-10\tpending\t2026-11-01T17:30:00Z\n")
    assert "INFRA-10" in pilot._recent_starts(starts, now, set())


def test_omp_panes_finds_omp_behind_the_bash_shim_and_not_agent_news_trailing_bash():
    """Recorded 2026-11-02 in the rocq window: tmux reports `bash` for both panes, because `omp` here is the shim
    ~/.dotfiles/shims/omp, a bash script that starts the real omp as its child."""
    real = "/home/sami/.mise/installs/github-sjawhar-oh-my-pi/18.13.3-sami.20261029-115123/bin/omp"
    processes = [
        {"pid": 1595402, "ppid": 1, "uid": 1000, "argv": ["bash"], "cgroup": ""},
        {"pid": 1597368, "ppid": 1595402, "uid": 1000,
         "argv": ["bash", "/home/sami/.dotfiles/shims/omp", "--resume", SESSION, "."], "cgroup": ""},
        {"pid": 1598473, "ppid": 1597368, "uid": 1000, "argv": [real, "--resume", SESSION, "."], "cgroup": ""},
        {"pid": 2000000, "ppid": 1, "uid": 1000, "argv": ["bash"], "cgroup": ""},  # omp exited; `exec bash` remains
    ]
    panes = [
        {"pane": "%78", "pid": 1595402, "tmux": "agents", "window": "rocq", "title": "π > ROCQ"},
        {"pane": "%90", "pid": 2000000, "tmux": "agents", "window": "rocq", "title": "sami-rocqperf"},
    ]

    assert pilot.omp_panes(processes, panes) == {"%78"}


MOVED = "01a1d00d-0000-0000-0000-000000000000"
GONE = "01a1dead-0000-0000-0000-000000000000"


def moved_pane_command(session: str) -> str:
    """What `agentbox move --to` respawned SHORT's old pane in agents:tasks with (recorded 2026-11-03)."""
    return (f"bash -i -c \"echo 'agentbox: session {session} moved to sami-devbox at 2026-11-03T11:55:11Z; it runs "
            "there in tmux session moved, window 01a16551 (@4). This pane is a shell.'; exec bash -i\"")


def test_machine_census_reads_the_streams_windows_and_the_sessions_moved_from_them_and_nothing_in_a_box(monkeypatch):
    monkeypatch.delenv("AGENTBOX_BOX", raising=False)
    panes = [
        {"pane": "%35", "pid": 10, "tmux": "agents", "window": "rocq", "start": moved_pane_command(MOVED), "title": ""},
        {"pane": "%36", "pid": 11, "tmux": "agents", "window": "tasks", "start": moved_pane_command(GONE), "title": ""},
        {"pane": "%37", "pid": 12, "tmux": "agents", "window": "rocq", "start": "bash", "title": "π > ROCQ"},
    ]
    processes = [{"pid": 12, "ppid": 1, "uid": 1000, "argv": ["bash"], "cgroup": ""},
                 {"pid": 13, "ppid": 12, "uid": 1000, "argv": ["omp", "--resume", SESSION], "cgroup": ""}]
    monkeypatch.setattr(pilot.sessions, "read_panes", lambda: panes)
    monkeypatch.setattr(pilot.sessions, "read_processes", lambda: processes)
    monkeypatch.setattr(pilot.sessions, "read_containers", lambda: {})
    findings: list[str] = []

    assert pilot._machine_census("INFRA-167", findings) == ({SESSION}, {MOVED})
    assert findings == []

    monkeypatch.setenv("AGENTBOX_BOX", "agentbox-0123")
    assert pilot._machine_census("INFRA-167", findings) is None
    assert findings == ["in agent box agentbox-0123: this machine's windows not read, sessions counted from "
                        "members.txt and Envoy"]


def test_stream_sessions_counts_its_sessions_on_every_machine_while_envoy_lists_them(tmp_path):
    tmp_path.joinpath("members.txt").write_text(f"{GONE}\n")
    live = [
        {"session_id": SESSION, "host": "sami-rocqperf", "title": "in the window"},
        {"session_id": MOVED, "host": "agentbox-04921df9ec32", "title": "moved to sami-devbox"},
        {"session_id": OTHER, "host": "agentbox-161f233fba59", "title": "pilot session"},
        {"session_id": "01a1f00d-0000-0000-0000-000000000000", "host": "sami-devbox", "title": "another stream's"},
    ]

    found = pilot.stream_sessions(tmp_path, ({SESSION}, {MOVED}), live, {OTHER})

    assert [(item["session"], item["host"]) for item in found] == [
        (SESSION, "sami-rocqperf"), (OTHER, "agentbox-161f233fba59"), (MOVED, "agentbox-04921df9ec32")]
    # The record keeps what this machine saw, for a reader that cannot see the windows; GONE stays, uncounted.
    assert tmp_path.joinpath("members.txt").read_text() == f"{SESSION}\n{MOVED}\n{GONE}\n"
    # In an agent box on sami-devbox: no window reading, and the record and Envoy still count them.
    assert [item["session"] for item in pilot.stream_sessions(tmp_path, None, live, set())] == [SESSION, MOVED]
    # Envoy down: only what this machine sees in the windows, live by that reading.
    found = pilot.stream_sessions(tmp_path, ({SESSION}, set()), None, {OTHER})
    assert [(item["session"], item["host"]) for item in found] == [(SESSION, pilot.socket.gethostname())]


def test_live_omp_panes_reads_nothing_in_an_agent_box(monkeypatch):
    monkeypatch.setenv("AGENTBOX_BOX", "agentbox-0123")
    monkeypatch.setattr(pilot.sessions, "read_panes", lambda: pytest.fail("a box's pids name no host pane"))
    findings: list[str] = []

    assert pilot._live_omp_panes(findings) == set()
    assert findings == ["in agent box agentbox-0123: started panes not checked, a start counts for 2 hours"]


def test_scheduler_coalesces_daily_and_equal_dues_and_records_all_due_keys():
    at = datetime(2026, 11, 1, 14, 30, tzinfo=UTC)
    fires = [
        pilot.Fire("summary", at, "daily"),
        pilot.Fire("summary", at, "due", "INFRA-167", "summary@2026-11-01T14:30:00Z"),
        pilot.Fire("summary", at, "due", "INFRA-167", "summary@2026-11-01T14:30:00Z#second"),
    ]
    queued = pilot.coalesce_fires(fires)

    assert len(queued) == 1
    assert queued[0].name == "summary" and queued[0].at == at
    assert queued[0].due_keys == (
        ("INFRA-167", "summary@2026-11-01T14:30:00Z"),
        ("INFRA-167", "summary@2026-11-01T14:30:00Z#second"),
    )


def test_scheduler_keeps_fires_crossed_while_running_but_drops_before_start():
    jobs = [
        {"name": "A", "at": ["06:30"], "dues_from": None},
        {"name": "B", "at": ["06:31"], "dues_from": "INFRA-167"},
    ]
    started = datetime(2026, 11, 1, 14, 30, tzinfo=UTC)  # 06:30 PST
    while_a_runs = datetime(2026, 11, 1, 14, 32, tzinfo=UTC)
    due = datetime(2026, 11, 1, 14, 31, tzinfo=UTC)

    fires = pilot.fires_since(jobs, "America/Los_Angeles", started, while_a_runs, {"INFRA-167": [due]}, {}, set())
    assert [(fire.name, fire.at, fire.kind) for fire in fires if fire.at <= while_a_runs] == [
        ("A", started, "daily"), ("B", due, "daily"), ("B", due, "due")]

    restarted = pilot.fires_since(jobs, "America/Los_Angeles", while_a_runs, while_a_runs, {"INFRA-167": [due]}, {}, set())
    assert not [fire for fire in restarted if fire.at <= while_a_runs]


def test_scheduled_command_kills_and_reaps_its_process_group_on_timeout(monkeypatch):
    calls = []

    class Process:
        pid = 99
        returncode = None

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            if timeout is not None:
                raise subprocess.TimeoutExpired(["summary"], timeout)
            self.returncode = -9
            return self.returncode

    monkeypatch.setattr(pilot.subprocess, "Popen", lambda *args, **kwargs: calls.append(("popen", args, kwargs)) or Process())
    monkeypatch.setattr(pilot.os, "killpg", lambda pid, signal: calls.append(("killpg", pid, signal)))

    process = pilot.start_scheduled_command({"command": ["summary"], "cwd": ".", "timeout_s": 7})
    assert pilot.finish_scheduled_command(process, 7) == 124
    assert calls[0][2]["start_new_session"] is True
    assert calls[1] == ("wait", 7)
    assert calls[2][0] == "killpg" and calls[3] == ("wait", None)


def test_refresh_record_keeps_copy_on_failure_and_clears_it_on_404(tmp_path):
    stream_dir = tmp_path / "INFRA-167"
    stream_dir.mkdir()
    copy = stream_dir / "stream.md"
    copy.write_text("saved")

    class Api:
        def __init__(self, error):
            self.error = error

        def get(self, _):
            raise pilot.SourceError(self.error)

    assert pilot.refresh_stream_record(Api("HTTP 502"), tmp_path, "INFRA-167") == "saved"
    assert copy.read_text() == "saved"
    assert pilot.refresh_stream_record(Api("HTTP 404"), tmp_path, "INFRA-167") is None
    assert not copy.exists()


class Stop(Exception):
    pass


def drive_schedule(monkeypatch, tmp_path, start: datetime, durations: dict[str, float], runs: int, record,
                   b_at=("06:31",), a_at=("06:30",), max_jobs=pilot.MAX_CONCURRENT_JOBS, a_dues_from=None):
    """Run `schedule()` on a fake clock with its real worker threads: each started job blocks until the scheduler's
    wait reaches the job's end (start plus its duration), and a wait with no job ending inside it advances the clock
    by the whole wait. The loop stops after RUNS starts or an hour of fake time. RECORD is the stream.md Dispatch
    returns, or a function of the clock that returns it (an owner editing the record while the scheduler runs).
    Returns [(job, UTC start)] and the state."""
    import json
    import threading
    from datetime import timedelta

    clock = [start]
    ran: list[tuple[str, str]] = []
    pending: list[Handle] = []
    tmp_path.mkdir(parents=True, exist_ok=True)
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"timezone": "America/Los_Angeles", "jobs": [
        {"name": "A", "at": list(a_at), "dues_from": a_dues_from, "command": ["A"], "cwd": ".", "timeout_s": 9000},
        {"name": "B", "at": list(b_at), "dues_from": "INFRA-167", "command": ["B"], "cwd": ".", "timeout_s": 9000},
    ]}))

    class Handle:
        def __init__(self, end):
            self.end = end
            self.released = threading.Event()

    def start_command(job):
        ran.append((job["name"], pilot.utc_text(clock[0])))
        if len(ran) == runs:
            raise Stop
        handle = Handle(clock[0] + timedelta(seconds=durations[job["name"]]))
        pending.append(handle)
        return handle

    def finish_command(handle, timeout_s):
        assert handle.released.wait(timeout=30)
        return 0

    def wait(results, seconds):
        limit = clock[0] + timedelta(seconds=seconds)
        ending = min(pending, key=lambda handle: handle.end, default=None)
        if ending and ending.end <= limit:
            clock[0] = max(clock[0], ending.end)
            pending.remove(ending)
            ending.released.set()
            return [results.get(timeout=30)]
        clock[0] = limit
        if clock[0] > start + timedelta(hours=1):
            raise Stop
        return []

    class Alive:
        def is_alive(self):
            return True

    class Api:
        def get(self, path):
            assert path == "/api/v1/issues/INFRA-167/artifacts/stream.md/text"
            return {"markdown": record(clock[0]) if callable(record) else record}

    monkeypatch.setattr(pilot, "start_scheduled_command", start_command)
    monkeypatch.setattr(pilot, "finish_scheduled_command", finish_command)
    monkeypatch.setattr(pilot, "kill_scheduled_command", lambda handle: handle.released.set())
    monkeypatch.setattr(pilot, "start_heartbeat", lambda state, on_death: Alive())

    try:
        with pytest.raises(Stop):
            pilot.schedule(jobs, tmp_path / "state", api=Api(), clock=lambda: clock[0], wait=wait, max_jobs=max_jobs)
    finally:
        for handle in pending:
            handle.released.set()
    return ran, tmp_path / "state"


TWO_DUES = """## Open commitments

- [ ] First · owner 01a1beef-0000-0000-0000-000000000000 · due 2026-11-01T14:31Z · https://dispatch.test/issues/INFRA-1
- [ ] Second · owner 01a1beef-0000-0000-0000-000000000000 · due 2026-11-01T14:31Z · https://dispatch.test/issues/INFRA-2
"""


def due_record(*dues: str) -> str:
    return "## Open commitments\n\n" + "".join(
        f"- [ ] Item {n} · owner {SESSION} · due {due} · https://dispatch.test/issues/INFRA-{n}\n"
        for n, due in enumerate(dues, 1))


def test_schedule_starts_a_due_while_another_job_runs(monkeypatch, tmp_path):
    """A runs 14:30-14:50. B's 14:45 due starts at 14:45, before A ends."""
    ran, _ = drive_schedule(monkeypatch, tmp_path, datetime(2026, 11, 1, 14, 29, tzinfo=UTC), {"A": 1200, "B": 1},
                            runs=99, record=due_record("2026-11-01T14:45Z"), b_at=("12:00",))

    assert ran == [("A", "2026-11-01T14:30:00Z"), ("B", "2026-11-01T14:45:00Z")]


def test_schedule_runs_a_job_once_at_a_time_and_its_next_fire_when_it_ends(monkeypatch, tmp_path):
    """A fires at 14:30Z and 14:40Z and runs 20 minutes: its second fire waits for the first and starts at 14:50."""
    ran, _ = drive_schedule(monkeypatch, tmp_path, datetime(2026, 11, 1, 14, 29, tzinfo=UTC), {"A": 1200, "B": 1},
                            runs=99, record=due_record(), a_at=("06:30", "06:40"), b_at=("12:00",))

    assert ran == [("A", "2026-11-01T14:30:00Z"), ("A", "2026-11-01T14:50:00Z")]


def test_schedule_holds_a_due_fire_while_the_cap_is_full(monkeypatch, tmp_path):
    """With one job allowed at once, B's 14:45 due waits for A, which runs 14:30-14:50."""
    ran, _ = drive_schedule(monkeypatch, tmp_path, datetime(2026, 11, 1, 14, 29, tzinfo=UTC), {"A": 1200, "B": 1},
                            runs=99, record=due_record("2026-11-01T14:45Z"), b_at=("12:00",), max_jobs=1)

    assert ran == [("A", "2026-11-01T14:30:00Z"), ("B", "2026-11-01T14:50:00Z")]


def test_schedule_records_both_dues_when_two_jobs_of_one_stream_end_together(monkeypatch, tmp_path):
    ran, state = drive_schedule(monkeypatch, tmp_path, datetime(2026, 11, 1, 14, 29, tzinfo=UTC), {"A": 60, "B": 60},
                                runs=99, record=due_record("2026-11-01T14:31Z"), a_at=("12:00",), b_at=("12:00",),
                                a_dues_from="INFRA-167")

    assert ran == [("A", "2026-11-01T14:31:00Z"), ("B", "2026-11-01T14:31:00Z")]
    assert (state / "INFRA-167" / "fired-dues.json").read_text() == (
        '{\n  "fired": [\n    "A@2026-11-01T14:31:00Z",\n    "B@2026-11-01T14:31:00Z"\n  ]\n}\n')
    assert not (state / "INFRA-167" / "fired-dues.json.tmp").exists()


def test_schedule_drops_a_due_the_owner_removed_and_follows_one_they_moved(monkeypatch, tmp_path):
    """The 14:31 due is queued before A runs; once A has started the owner edits the record."""
    started = datetime(2026, 11, 1, 14, 29, tzinfo=UTC)
    edited = datetime(2026, 11, 1, 14, 30, 30, tzinfo=UTC)

    def removed(now):
        return due_record("2026-11-01T14:31Z") if now < edited else due_record()

    ran, _ = drive_schedule(monkeypatch, tmp_path, started, {"A": 120, "B": 1}, runs=99, record=removed,
                            b_at=("12:00",))
    assert ran == [("A", "2026-11-01T14:30:00Z")]

    def moved(now):
        return due_record("2026-11-01T14:31Z") if now < edited else due_record("2026-11-01T14:40Z")

    ran, _ = drive_schedule(monkeypatch, tmp_path / "moved", started, {"A": 120, "B": 1}, runs=99, record=moved,
                            b_at=("12:00",))
    assert ran == [("A", "2026-11-01T14:30:00Z"), ("B", "2026-11-01T14:40:00Z")]


def test_schedule_runs_one_fire_for_a_daily_time_and_both_dues_while_another_job_runs(monkeypatch, tmp_path):
    """A (06:30 PST, 14:30Z) runs for two minutes; B's daily time and two commitments fall at 14:31Z meanwhile, and
    B runs once then. The loop then runs for an hour of fake time with nothing more to do."""
    ran, state = drive_schedule(monkeypatch, tmp_path, datetime(2026, 11, 1, 14, 29, tzinfo=UTC),
                                {"A": 120, "B": 1}, runs=99, record=TWO_DUES)

    assert ran == [("A", "2026-11-01T14:30:00Z"), ("B", "2026-11-01T14:31:00Z")]
    # The scheduler refreshed the record from Dispatch before it read the dues, and remembers the due it ran.
    assert (state / "INFRA-167" / "stream.md").read_text() == TWO_DUES
    assert (state / "INFRA-167" / "fired-dues.json").read_text() == (
        '{\n  "fired": [\n    "B@2026-11-01T14:31:00Z"\n  ]\n}\n')


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0]
    except FileNotFoundError:  # reaped
        return False
    return state != "Z"


def _dies(pid: int) -> bool:
    """Whether PID stops running within five seconds (a zombie waiting for its reaper is dead)."""
    import time as clock

    for _ in range(50):
        if not _alive(pid):
            return True
        clock.sleep(0.1)
    return False


def _grandchild_command(pid_file: Path) -> list[str]:
    """A wrapper whose child would outlive it, as omp does under its shim."""
    return ["sh", "-c", f"sleep 60 & echo $! > {pid_file}.tmp && mv {pid_file}.tmp {pid_file}; wait"]


def _read_pid(pid_file: Path) -> int:
    import time as clock

    for _ in range(50):
        if pid_file.exists():
            return int(pid_file.read_text())
        clock.sleep(0.1)
    pytest.fail(f"{pid_file} never appeared")


def test_scheduled_command_timeout_kills_a_real_grandchild(tmp_path):
    """The reviewer's case without mocks: a wrapper whose child outlives it. After the timeout no process of the
    group may run on."""
    import time as clock

    pid_file = tmp_path / "grandchild.pid"
    started = clock.monotonic()

    process = pilot.start_scheduled_command({"command": _grandchild_command(pid_file), "cwd": str(tmp_path),
                                             "timeout_s": 1})
    assert pilot.finish_scheduled_command(process, 1) == 124
    assert clock.monotonic() - started < 10
    grandchild = _read_pid(pid_file)
    assert _dies(grandchild), f"grandchild {grandchild} still runs after the timeout"


def test_a_failed_wait_kills_the_group_before_it_reports(tmp_path):
    """A wait that raises (here a timeout the loader would now refuse) must not leave the command running unwatched."""
    import signal

    pid_file = tmp_path / "grandchild.pid"
    process = pilot.start_scheduled_command({"command": _grandchild_command(pid_file), "cwd": str(tmp_path),
                                             "timeout_s": 1})
    grandchild = _read_pid(pid_file)
    try:
        with pytest.raises(TypeError):
            pilot.finish_scheduled_command(process, "1")
        assert process.returncode is not None, "the wrapper was not reaped"
        assert _dies(grandchild), f"grandchild {grandchild} still runs after the failed wait"
    finally:
        if _alive(grandchild):
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def test_schedule_kills_running_jobs_when_a_launch_fails(monkeypatch, tmp_path):
    """A starts at 06:30 PST (14:30Z); B, due at the same time, names a missing program. The scheduler exits on B's
    error, and A's group must not outlive the scheduler that enforced its timeout."""
    import json
    import signal

    pid_file = tmp_path / "grandchild.pid"
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"timezone": "America/Los_Angeles", "jobs": [
        {"name": "A", "at": ["06:30"], "command": _grandchild_command(pid_file), "cwd": str(tmp_path),
         "timeout_s": 600},
        {"name": "B", "at": ["06:30"], "command": [str(tmp_path / "missing")], "cwd": str(tmp_path),
         "timeout_s": 600},
    ]}))

    class Alive:
        def is_alive(self):
            return True

    launched = []
    real_start = pilot.start_scheduled_command
    monkeypatch.setattr(pilot, "start_scheduled_command", lambda job: launched.append(real_start(job)) or launched[-1])
    monkeypatch.setattr(pilot, "start_heartbeat", lambda state, on_death=None: Alive())
    at = datetime(2026, 11, 1, 14, 30, tzinfo=UTC)

    with pytest.raises(FileNotFoundError):
        pilot.schedule(jobs, tmp_path / "state", api=object(), clock=lambda: at,
                       wait=lambda results, seconds: pytest.fail("the scheduler must exit before it waits"))

    [process] = launched
    assert process.returncode is not None, "A's wrapper was not reaped"
    # The scheduler can kill A's group before its shell forks the child or writes the pid file; the whole group
    # dies either way, so a missing pid file is a pass, not a hang.
    if not pid_file.exists():
        return
    grandchild = int(pid_file.read_text())
    try:
        assert _dies(grandchild), f"A's grandchild {grandchild} outlived the scheduler"
    finally:
        if _alive(grandchild):
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


@pytest.mark.parametrize("timeout", ["1", 0, -5, True, 1.5, None])
def test_jobs_file_refuses_a_timeout_that_is_not_a_positive_integer(tmp_path, timeout):
    import json

    jobs = tmp_path / "jobs.json"
    job = {"name": "A", "at": ["06:30"], "command": ["true"], "cwd": ".", "timeout_s": timeout}
    jobs.write_text(json.dumps({"timezone": "America/Los_Angeles", "jobs": [job]}))
    with pytest.raises(ValueError, match="timeout_s"):
        pilot._jobs(jobs)

    jobs.write_text(json.dumps({"timezone": "America/Los_Angeles", "jobs": [{**job, "timeout_s": 1}]}))
    assert pilot._jobs(jobs)[1][0]["timeout_s"] == 1


def test_schedule_drops_only_times_already_past_when_the_process_started(monkeypatch, tmp_path):
    ran, _ = drive_schedule(monkeypatch, tmp_path, datetime(2026, 11, 1, 14, 30, 30, tzinfo=UTC),
                            {"A": 1, "B": 1}, runs=1, record=TWO_DUES)

    assert ran == [("B", "2026-11-01T14:31:00Z")]


def fire_times(jobs, started, now, dues=None, fired=None, executed=frozenset()):
    """The scheduler's own fire list (`fires_since`, coalesced as the loop coalesces it) as (UTC, kind, due keys)."""
    fires = pilot.coalesce_fires(pilot.fires_since(jobs, "America/Los_Angeles", started, now, dues or {}, fired or {},
                                                   set(executed)))
    return [(pilot.utc_text(f.at), f.kind, f.due_keys) for f in fires]


def test_fire_times_follow_pacific_time_across_the_2026_11_01_change():
    """Pacific time fell back at 2026-11-01 09:00Z (02:00 PDT became 01:00 PST)."""
    jobs = [{"name": "daily", "at": ["06:30", "14:30"], "dues_from": None}]

    # Started 10-31 20:00Z; read the next morning: 14:30 PDT was 21:30Z, 06:30 PST is 14:30Z, never 13:30Z.
    across = fire_times(jobs, datetime(2026, 10, 31, 20, tzinfo=UTC), datetime(2026, 11, 1, 14, 25, tzinfo=UTC))
    assert [t for t, _, _ in across] == ["2026-10-31T21:30:00Z", "2026-11-01T14:30:00Z"]

    later = fire_times(jobs, datetime(2026, 11, 1, 13, tzinfo=UTC), datetime(2026, 11, 1, 22, 25, tzinfo=UTC))
    assert [t for t, _, _ in later] == ["2026-11-01T14:30:00Z", "2026-11-01T22:30:00Z"]


def test_fire_times_add_each_due_after_the_start_once_and_never_a_fired_or_run_one():
    jobs = [{"name": "summary", "at": ["06:00"], "dues_from": "INFRA-167"}]
    started, now = datetime(2026, 11, 1, 13, tzinfo=UTC), datetime(2026, 11, 1, 13, 55, tzinfo=UTC)
    past, due = datetime(2026, 11, 1, 12, tzinfo=UTC), datetime(2026, 11, 1, 14, tzinfo=UTC)

    # 06:00 PST is 14:00Z: the daily time and the due fall together and run once, carrying the due's key.
    assert fire_times(jobs, started, now, {"INFRA-167": [past, due]}) == [
        ("2026-11-01T14:00:00Z", "daily", (("INFRA-167", "summary@2026-11-01T14:00:00Z"),))]
    fired = {"INFRA-167": {"summary@2026-11-01T14:00:00Z"}}
    assert fire_times(jobs, started, now, {"INFRA-167": [due]}, fired) == [("2026-11-01T14:00:00Z", "daily", ())]
    assert fire_times(jobs, started, now, {"INFRA-167": [due]}, fired, {("summary", "2026-11-01T14:00:00Z")}) == []


def test_top_level_cli_delegates_the_pilot_subcommands(monkeypatch):
    from chief_dashboard import __main__ as entry

    called = []
    monkeypatch.setattr(pilot, "main", lambda argv: called.append(argv) or 0)
    monkeypatch.setattr(sys, "argv", ["chief-dashboard", "pilot", "facts", "--stream", "INFRA-167", "--state", "state"])

    with pytest.raises(SystemExit) as exited:
        entry.main()

    assert exited.value.code == 0
    assert called == [["facts", "--stream", "INFRA-167", "--state", "state"]]


DEAD = "01a1dead-0000-0000-0000-000000000000"

STARTED_RECORD = f"""# Pilot: stream record

## Order of work

| # | Issue | Outcome, and what makes it done | Owner session | State |
| --- | --- | --- | --- | --- |
| 1 | https://dispatch.test/issues/INFRA-10 | Started three hours ago, its pane still runs. | none | working |
| 2 | https://dispatch.test/issues/INFRA-11 | Claimed by an ended session, started an hour ago. | none | ready |
| 3 | https://dispatch.test/issues/INFRA-12 | Started three hours ago, its pane is gone. | none | ready |
| 4 | https://dispatch.test/issues/INFRA-13 | Claimed by a live session. | {SESSION} | working |

## Envoy roles and claims

| Envoy role | Holder now | Goes to when the holder ends |
| --- | --- | --- |
| lead-pilot | {SESSION} | record owner |
| missing-role | none | record owner |
"""


class FactsDispatch:
    """The read-only Dispatch routes facts uses, in the shapes Dispatch returned (claim.actor, agents session_id)."""

    def __init__(self, record: str, claims: dict[str, str] | None = None, live=(SESSION,),
                 issues: dict[str, dict] | None = None):
        self.record = record
        self.claims = claims or {}
        self.live = list(live)
        self.issues = issues or {}

    def get(self, path: str):
        if path == "/api/v1/issues/INFRA-167/artifacts/stream.md/text":
            return {"markdown": self.record}
        if path == "/api/v1/agents":
            return [{"session_id": s, "title": f"live {s[:8]}"} for s in self.live]
        if path in ("/api/v1/issues/INFRA-167/comments", "/api/v1/issues?project=INFRA&open=true"):
            return []
        if path == "/api/v1/issues/INFRA-167":
            return {"key": "INFRA-167", "artifacts": []}
        key = path.removeprefix("/api/v1/issues/")
        holder = self.claims.get(key)
        return {"key": key, "status": "in_progress",
                "claim": {"actor": actor(holder), "at": "2026-11-01T10:00:00Z"} if holder else None,
                **self.issues.get(key, {})}


def fake_commands(monkeypatch, panes: set[str], sessions: list[dict] | None,
                  machine: tuple[set[str], set[str]] | None = None) -> list[list[str]]:
    """Answer what facts reads from the machine: `envoy ps --json`, and the panes that run omp (PANES). Records each
    read. SESSIONS None makes `envoy ps` fail as it does when its hub is unreachable (exit 1)."""
    import json
    import subprocess

    ran: list[list[str]] = []

    def run(command, **kwargs):
        ran.append(command)
        if command[0] == "envoy" and sessions is None:
            raise subprocess.CalledProcessError(1, command, output="", stderr="hub unreachable")
        assert command[0] == "envoy", command
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(sessions), stderr="")

    def live_panes(findings):
        ran.append(["omp panes"])
        return set(panes)

    monkeypatch.setattr(pilot.subprocess, "run", run)
    monkeypatch.setattr(pilot, "_live_omp_panes", live_panes)
    monkeypatch.setattr(pilot, "_machine_census", lambda stream, findings: machine)
    return ran


def test_facts_reports_role_holders_unknown_when_envoy_cannot_be_read(monkeypatch, tmp_path):
    fake_commands(monkeypatch, set(), None)

    result, _ = pilot.facts(FactsDispatch(STARTED_RECORD), "INFRA-167", tmp_path, datetime(2026, 11, 1, 18, tzinfo=UTC))

    assert [(r["role"], r["holder_live"], r["live_holder"]) for r in result["roles"]] == [
        ("lead-pilot", None, None), ("missing-role", None, None)]
    assert any(f.startswith("Envoy unavailable, role holders and sessions off this machine not read: ")
               for f in result["findings"])
    assert not any(f.startswith("role has no live holder") for f in result["findings"])


def test_facts_counts_a_start_while_young_or_its_pane_lives_unless_a_live_session_claims_it(monkeypatch, tmp_path):
    state = tmp_path / "state"
    directory = state / "INFRA-167"
    directory.mkdir(parents=True)
    directory.joinpath("started.tsv").write_text(
        "INFRA-10\tpending\t2026-11-01T15:00:00Z\n"
        "INFRA-10\t%17\t2026-11-01T15:01:00Z\n"
        "INFRA-11\tpending\t2026-11-01T17:00:00Z\n"
        "INFRA-12\t%18\t2026-11-01T15:00:00Z\n"
        "INFRA-13\tpending\t2026-11-01T17:30:00Z\n"
    )
    directory.joinpath("offline-decisions.md").write_text("### D1: What to do\n\n- Answer: keep the evidence\n")
    ran = fake_commands(monkeypatch, {"%17"}, [{"session_id": SESSION, "roles": ["lead-pilot"]}])
    api = FactsDispatch(STARTED_RECORD, claims={"INFRA-11": DEAD, "INFRA-13": SESSION})

    result, _ = pilot.facts(api, "INFRA-167", state, datetime(2026, 11, 1, 18, tzinfo=UTC))

    assert ["omp panes"] in ran and ["envoy", "ps", "--json"] in ran
    rows = {item["issue"]: item for item in result["order_of_work"]}
    # INFRA-10: its latest line names pane %17, 2 h 59 min old, and omp still runs in that pane.
    assert rows["INFRA-10"]["started_recently"] == {"pane": "%17", "at": "2026-11-01T15:01:00Z"}
    # INFRA-11: an ended session's claim does not hide the start an hour ago.
    assert rows["INFRA-11"]["started_recently"] == {"pane": "pending", "at": "2026-11-01T17:00:00Z"}
    # INFRA-12: three hours old and tmux no longer lists %18, so it is ready again.
    assert "started_recently" not in rows["INFRA-12"] and rows["INFRA-12"]["ready_no_owner"] is True
    # INFRA-13: a live claim governs, whatever the start file says.
    assert "started_recently" not in rows["INFRA-13"] and rows["INFRA-13"]["ready_no_owner"] is False
    assert [rows[k]["ready_no_owner"] for k in ("INFRA-10", "INFRA-11")] == [False, False]
    assert result["roles"] == [
        {"role": "lead-pilot", "holder": SESSION, "next_holder": "record owner", "holder_live": True,
         "live_holder": SESSION},
        {"role": "missing-role", "holder": "none", "next_holder": "record owner", "holder_live": False,
         "live_holder": None},
    ]
    assert result["pending_offline_decisions"] == ["### D1: What to do\n\n- Answer: keep the evidence"]
    assert "role has no live holder: missing-role" in result["findings"]
    assert "ready issue with no owner: INFRA-12" in result["findings"]
    assert "ready issue with no owner: INFRA-10" not in result["findings"]


def test_facts_never_marks_a_legion_issue_or_one_under_a_legion_parent_ready(monkeypatch, tmp_path):
    record = STARTED_RECORD.replace(
        "| 4 |", "| 5 | https://dispatch.test/issues/INFRA-14 | Legion-labelled. | none | ready |\n"
        "| 6 | https://dispatch.test/issues/INFRA-15 | Child of a Legion issue. | none | ready |\n| 4 |")
    fake_commands(monkeypatch, set(), [])
    api = FactsDispatch(record, issues={"INFRA-14": {"labels": ["legion"]}, "INFRA-15": {"parent": "INFRA-16"},
                                        "INFRA-16": {"parent": "INFRA-17"}, "INFRA-17": {"labels": ["legion"]}})

    result, _ = pilot.facts(api, "INFRA-167", tmp_path, datetime(2026, 11, 1, 18, tzinfo=UTC))

    rows = {item["issue"]: item for item in result["order_of_work"]}
    # The Legion daemon routes these: an outside session's claim would take the tree out of its workflow.
    assert [(rows[k]["ready_no_owner"], rows[k]["legion"]) for k in ("INFRA-14", "INFRA-15")] == [
        (False, True), (False, True)]
    assert (rows["INFRA-12"]["ready_no_owner"], rows["INFRA-12"]["legion"]) == (True, False)
    assert "ready issue with no owner: INFRA-12" in result["findings"]
    assert not {"ready issue with no owner: INFRA-14", "ready issue with no owner: INFRA-15"} & set(result["findings"])


class FailingRecord(FactsDispatch):
    """FactsDispatch whose reads fail with ERROR, as `sources.Dispatch.get` raises it: only the stream.md read when
    the issue has no record (404), every read when Dispatch is down."""

    def __init__(self, error: str, every_read: bool = False):
        super().__init__("")
        self.error = error
        self.every_read = every_read

    def get(self, path: str):
        if self.every_read or path.endswith("/artifacts/stream.md/text"):
            raise pilot.SourceError(f"GET https://dispatch.test{path}: {self.error}")
        return super().get(path)


def test_facts_reads_the_saved_copy_and_nothing_live_while_dispatch_is_down(monkeypatch, tmp_path):
    directory = tmp_path / "INFRA-167"
    directory.mkdir()
    directory.joinpath("stream.md").write_text(RECORD)
    directory.joinpath("last-facts").write_text("2026-11-01T12:00:00Z\n")
    directory.joinpath("offline-ledger.md").write_text(
        "LEDGER blocked INFRA-10 Dispatch down https://github.com/org/repo/pull/1\n")
    fake_commands(monkeypatch, set(), [])

    result, _ = pilot.facts(FailingRecord("HTTP 502: bad gateway", every_read=True), "INFRA-167", tmp_path,
                            datetime(2026, 11, 1, 18, tzinfo=UTC))

    assert result["record"] == RECORD
    assert result["record_source"].startswith("local copy from 20")
    assert [(r["issue"], r["status"], r["claim"], r["ready_no_owner"]) for r in result["order_of_work"]] == [
        ("INFRA-10", None, None, False), ("INFRA-11", None, None, False)]
    assert [c["overdue"] for c in result["commitments"]] == [False, True]
    assert result["ledger"] == [{"kind": "blocked", "issue": "INFRA-10", "text": "Dispatch down",
                                 "link": "https://github.com/org/repo/pull/1", "pending_offline": True}]
    assert result["findings"][0].startswith("Dispatch unavailable, live state not read: GET ")
    # The comments since the last run were not read, so the next run must read them.
    assert directory.joinpath("last-facts").read_text() == "2026-11-01T12:00:00Z\n"


def test_facts_drops_the_saved_copy_when_the_issue_has_no_record(monkeypatch, tmp_path):
    """A 404 means the record is gone, not that Dispatch is down: the old copy must not keep feeding the scheduler."""
    directory = tmp_path / "INFRA-167"
    directory.mkdir()
    directory.joinpath("stream.md").write_text(RECORD)
    fake_commands(monkeypatch, set(), [])

    result, _ = pilot.facts(FailingRecord('HTTP 404: {"code":"ARTIFACT_NOT_FOUND"}'), "INFRA-167", tmp_path,
                            datetime(2026, 11, 1, 18, tzinfo=UTC))

    assert result["record"] is None and result["record_source"] is None
    assert result["findings"][0] == "no stream record"
    assert not directory.joinpath("stream.md").exists()


def test_facts_exposes_summary_inputs_titles_owner_liveness_and_persistent_pilot_sessions(monkeypatch, tmp_path):
    class SummaryFacts(FactsDispatch):
        def get(self, path: str):
            if path.endswith("/artifacts/stream.md/text"):
                return super().get(path)
            if path == "/api/v1/agents":
                return [{"session_id": SESSION, "title": "live pilot session"}]
            if path == "/api/v1/issues/INFRA-167/comments":
                return [{
                    "body": "LEDGER start INFRA-10 begun https://github.com/o/r/pull/10",
                    "created_at": "2026-11-03T15:00:00Z", "author": actor(),
                }]
            if path == "/api/v1/issues?project=INFRA&open=true":
                return [
                    {"key": "INFRA-10", "status": "in_progress", "claim": {"actor": actor()}},
                    {"key": "INFRA-20", "status": "in_progress", "claim": {"actor": actor()}},
                ]
            if path.startswith("/api/v1/issues/INFRA-"):
                key = path.rsplit("/", 1)[-1]
                return {"key": key, "title": f"Title {key}", "status": "in_progress", "claim": None}
            return super().get(path)

    state = tmp_path / "state"
    stream_dir = state / "INFRA-167"
    stream_dir.mkdir(parents=True)
    stream_dir.joinpath("offline-ledger.md").write_text(
        "LEDGER blocked INFRA-10 offline https://github.com/o/r/pull/10\n")
    fake_commands(monkeypatch, set(), [])

    result, _ = pilot.facts(
        SummaryFacts(RECORD), "INFRA-167", state, datetime(2026, 11, 3, 16, tzinfo=UTC),
        pilot_start=datetime(2026, 11, 3, 14, 30, tzinfo=UTC),
    )

    assert result["pending_offline"] == ["LEDGER blocked INFRA-10 offline https://github.com/o/r/pull/10"]
    assert result["pending_offline_decisions"] == []
    assert [(row["issue"], row["title"]) for row in result["order_of_work"]] == [
        ("INFRA-10", "Title INFRA-10"), ("INFRA-11", "Title INFRA-11")]
    assert [(item["issue"], item["title"], item["owner_live"]) for item in result["commitments"]] == [
        ("INFRA-10", "Title INFRA-10", True), ("INFRA-11", "Title INFRA-11", False)]
    assert result["multi_claim"] == [{
        "session": SESSION, "title": "live pilot session", "issues": ["INFRA-10", "INFRA-20"], "pilot_session": True,
    }]
    assert stream_dir.joinpath("pilot-sessions.txt").read_text() == SESSION + "\n"



def test_summary_skips_omp_when_facts_has_no_stream_record(monkeypatch, tmp_path):
    facts_file = tmp_path / "facts.json"
    monkeypatch.setattr(pilot, "facts", lambda *args: ({"record": None}, facts_file))
    monkeypatch.setattr(pilot.subprocess, "run", lambda *args, **kwargs: pytest.fail("omp must not run"))

    assert pilot.summary(object(), "INFRA-167", tmp_path, "rocq") == 0
    assert next((tmp_path / "INFRA-167").glob("summary-*.log")).read_text() == "no stream record, skipped\n"


def test_summary_dry_run_prints_the_bounded_omp_command_and_runs_nothing(monkeypatch, tmp_path, capsys):
    facts_file = tmp_path / "INFRA-167" / "facts-20261101T180000Z.json"
    monkeypatch.setattr(pilot, "facts", lambda *args: ({"record": RECORD}, facts_file))
    monkeypatch.setattr(pilot.subprocess, "run", lambda *args, **kwargs: pytest.fail("omp must not run"))

    assert pilot.summary(object(), "INFRA-167", tmp_path, "rocq", dry_run=True) == 0

    printed = capsys.readouterr().out
    assert printed.startswith("omp -p --no-title --max-time 30m --max-cost 15 ")
    for part in (str(facts_file), "INFRA-167", "`agent-new --box --window rocq`",
                 str(tmp_path / "INFRA-167" / "started.tsv"),
                 "ISSUE<TAB>pending<TAB>UTC_ISO", "skill://stream-lead/references/summary.md"):
        assert part in printed
    assert not list((tmp_path / "INFRA-167").glob("summary-*.log"))


def test_facts_and_the_dry_run_list_the_streams_live_sessions(monkeypatch, tmp_path, capsys):
    fake_commands(monkeypatch, set(), [{"session_id": MOVED, "host": "agentbox-04921df9ec32", "title": "SHORT",
                                        "roles": []}], machine=(set(), {MOVED}))

    result, _ = pilot.facts(FactsDispatch(STARTED_RECORD), "INFRA-167", tmp_path, datetime(2026, 11, 1, 18, tzinfo=UTC))

    assert result["sessions"] == [{"session": MOVED, "host": "agentbox-04921df9ec32", "title": "SHORT"}]
    monkeypatch.setattr(pilot, "facts", lambda *args: (result, tmp_path / "facts.json"))
    assert pilot.summary(object(), "INFRA-167", tmp_path, "rocq", dry_run=True) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == f"session {MOVED}\tagentbox-04921df9ec32\tSHORT"
    assert printed[1].startswith("omp -p ")


def test_summary_dry_run_without_a_record_says_it_skips(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pilot, "facts", lambda *args: ({"record": None}, tmp_path / "facts.json"))

    assert pilot.summary(object(), "INFRA-167", tmp_path, "rocq", dry_run=True) == 0
    assert capsys.readouterr().out == "no stream record, skipped\n"


def test_schedule_heartbeat_records_utc_time_and_pid(tmp_path):
    pilot.write_heartbeat(tmp_path, datetime(2026, 11, 1, 18, tzinfo=UTC), pid=4242)

    assert tmp_path.joinpath("heartbeat").read_text() == "2026-11-01T18:00:00Z\t4242\n"


def test_heartbeat_thread_keeps_writing_and_stops_the_process_when_it_dies(monkeypatch, tmp_path):
    writes: list[str] = []
    exits: list[int] = []

    def write(state, now, pid):
        if len(writes) == 2:
            raise OSError("disk full")
        writes.append(str(state))

    monkeypatch.setattr(pilot, "write_heartbeat", write)
    monkeypatch.setattr(pilot.os, "_exit", exits.append)

    died: list[bool] = []
    thread = pilot.start_heartbeat(tmp_path, interval_s=0.01, on_death=lambda: died.append(True))
    thread.join(timeout=5)

    assert thread.daemon is True and not thread.is_alive()
    assert writes == [str(tmp_path), str(tmp_path)] and died == [True] and exits == [1]


class FakeDispatch:
    """A pageable in-memory copy of the Dispatch responses pilot.measure reads: the issue list (its observed fields
    key, status, parent, claim, updated_at) and each issue's event pages. Records every path it was asked for."""

    def __init__(self, issue_events: dict[str, list[dict]]):
        self.issue_events = issue_events
        self.paths: list[str] = []

    def listed(self, key: str, history: list[dict]) -> dict:
        statuses = [e["payload"]["status"] for e in history if e["type"].startswith("issue.") and "status" in e["payload"]]
        return {"key": key, "status": statuses[-1], "parent": history[0]["payload"].get("parent"), "claim": None,
                "updated_at": history[-1]["created_at"]}

    def get(self, path: str):
        self.paths.append(path)
        parsed = urlparse(path)
        parts = parsed.path.removeprefix("/api/v1/").split("/")
        query = parse_qs(parsed.query)
        if parts == ["issues"]:
            project = query["project"][0]
            return [self.listed(key, history) for key, history in self.issue_events.items()
                    if key.startswith(project + "-")]
        assert len(parts) == 3 and parts[0] == "issues" and parts[2] == "events"
        after = int(query["after"][0])
        limit = int(query["limit"][0])
        return [item for item in self.issue_events[parts[1]] if item["seq"] > after][:limit]


def ledger(seq: int, when: str, body: str) -> dict:
    return event("INFRA-167", seq, "comment.created", when, actor(), {"id": f"c-{seq}", "body": body})


def test_measure_counts_session_spread_and_requires_evidence_from_recorded_event_shapes():
    issues = {
        "INFRA-167": [
            created("INFRA-167", 1, "2026-10-20T00:00:00Z"),
            ledger(2, "2026-10-27T15:00:00Z", "LEDGER start INFRA-10 begin https://dispatch.test/issues/INFRA-10"),
            ledger(3, "2026-10-29T12:00:00Z", "LEDGER handoff INFRA-10 work moved https://dispatch.test/issues/INFRA-10"),
            # one-issue-session: post `LEDGER done`, then set the issue to done, so each line precedes its close.
            ledger(4, "2026-10-30T09:59:00Z", "LEDGER done LINUX-4 closed it https://dispatch.test/issues/LINUX-4"),
            ledger(5, "2026-10-30T09:59:30Z",
                   "LEDGER done OSSV-7 measured https://dispatch.test/issues/OSSV-7/artifacts/result-md"),
        ],
        "INFRA-10": [
            created("INFRA-10", 1, "2026-10-27T15:01:00Z"),
            claimed("INFRA-10", 2, "2026-10-27T15:02:00Z"),
            reason("INFRA-10", 3, "2026-10-29T09:59:59Z", "Moved on to https://dispatch.test/issues/INFRA-11"),
            closed("INFRA-10", 4, "2026-10-29T10:00:00Z"),
        ],
        "INFRA-11": [created("INFRA-11", 1, "2026-10-27T16:00:00Z", parent="INFRA-10")],
        "INFRA-12": [created("INFRA-12", 1, "2026-10-27T17:00:00Z", refs=("INFRA-10",))],
        # Created under INFRA-10 before the window and untouched since: found through the issue list's parent.
        "INFRA-14": [created("INFRA-14", 1, "2026-10-26T09:00:00Z", parent="INFRA-10")],
        "BET-1": [
            created("BET-1", 1, "2026-10-27T16:00:00Z"),
            claimed("BET-1", 2, "2026-10-27T16:01:00Z"),
            released("BET-1", 3, "2026-10-28T11:00:00Z"),
        ],
        # Last updated before the window: its events are never read.
        "BET-99": [created("BET-99", 1, "2026-10-01T00:00:00Z")],
        "LINUX-4": [
            created("LINUX-4", 1, "2026-10-27T16:00:00Z"),
            claimed("LINUX-4", 2, "2026-10-27T16:01:00Z"),
            closed("LINUX-4", 3, "2026-10-30T10:00:00Z"),
            event("LINUX-4", 4, "ask.opened", "2026-10-31T10:00:00Z", actor(), {"id": "ask-1", "kind": "question"}),
        ],
        "OSSV-6": [
            created("OSSV-6", 1, "2026-10-27T16:00:00Z"),
            claimed("OSSV-6", 2, "2026-10-27T16:01:00Z"),
            reason("OSSV-6", 3, "2026-10-30T09:59:59Z", "Merged as https://github.com/org/repo/pull/6"),
            closed("OSSV-6", 4, "2026-10-30T10:00:00Z"),
        ],
        "OSSV-7": [
            created("OSSV-7", 1, "2026-10-27T16:00:00Z"),
            claimed("OSSV-7", 2, "2026-10-27T16:01:00Z"),
            closed("OSSV-7", 3, "2026-10-30T10:00:00Z"),
        ],
    }
    api = FakeDispatch(issues)
    report = pilot.measure(api, "INFRA-167", START, END, [SESSION])
    session = report["sessions"][0]

    assert session["issues_claimed"] == ["BET-1", "INFRA-10", "LINUX-4", "OSSV-6", "OSSV-7"]
    assert session["issues_created"] == ["INFRA-10", "BET-1", "INFRA-11", "LINUX-4", "OSSV-6", "OSSV-7", "INFRA-12"]
    assert session["sub_issues_of_own_work"] == [
        {"issue": "INFRA-11", "rule": "parent INFRA-10"},
        {"issue": "INFRA-12", "rule": "linked to INFRA-10 within 24h of claim"},
    ]
    assert session["work_moved"] == [
        {"issue": "BET-1", "kind": "released"},
        {"issue": "INFRA-10", "kind": "ledger handoff"},
    ]
    assert session["done_with_own_child_open"] == [{"issue": "INFRA-10", "open_children": ["INFRA-11", "INFRA-14"]}]
    # A close reason or LEDGER done line that links only a Dispatch issue is no evidence; a pull request or a
    # Dispatch document (a measured result) is.
    assert session["done_without_evidence"] == ["INFRA-10", "LINUX-4"]
    assert session["asks_opened"] == ["ask-1"]
    assert session["held_one_issue"] is False
    assert session["last_activity"] == "2026-10-31T10:00:00Z"
    assert session["exit_after_done"] is None
    assert report["totals"]["sessions"] == 1 and report["totals"]["done_without_evidence"] == 2
    assert "/api/v1/issues/BET-99/events?after=0&limit=200" not in api.paths


def test_measure_without_a_sessions_file_takes_the_sessions_that_posted_ledger_start():
    issues = {
        "INFRA-167": [
            created("INFRA-167", 1, "2026-10-20T00:00:00Z"),
            ledger(2, "2026-10-27T15:00:00Z", "LEDGER start INFRA-10 begin https://dispatch.test/issues/INFRA-10"),
            event("INFRA-167", 3, "comment.created", "2026-10-28T15:00:00Z", actor(OTHER),
                  {"id": "c-3", "body": "a comment that is not a ledger line"}),
        ],
    }
    report = pilot.measure(FakeDispatch(issues), "INFRA-167", START, END)

    assert [s["session"] for s in report["sessions"]] == [SESSION]
    assert "| " + SESSION + " |" in pilot.measure_markdown(report)


def test_measure_preserves_every_violating_close_and_uses_only_reason_or_ledger_evidence():
    """A later repair cannot erase an earlier premature close. An external link on the issue is never evidence, not
    even a pull request; a child created after a close is not retrospectively open at that close."""
    def claimed_then(key: str, *rest) -> list[dict]:
        return [created(key, 1, "2026-10-27T16:00:00Z"), claimed(key, 2, "2026-10-27T16:01:00Z"),
                *(dict(e, seq=n) for n, e in enumerate(rest, 3))]

    issues = {
        "INFRA-167": [created("INFRA-167", 1, "2026-10-20T00:00:00Z")],
        "LINUX-5": claimed_then("LINUX-5", closed("LINUX-5", 0, "2026-10-29T10:00:00Z"),
                                reopened("LINUX-5", 0, "2026-10-29T11:00:00Z"),
                                closed("LINUX-5", 0, "2026-10-29T12:00:00Z",
                                       links=["https://github.com/o/r/pull/5"])),
        "LINUX-6": [created("LINUX-6", 1, "2026-10-29T09:00:00Z", parent="LINUX-5"),
                    closed("LINUX-6", 2, "2026-10-29T10:30:00Z")],
        # This child does not exist at either LINUX-5 close and must not affect the window.
        "LINUX-7": [created("LINUX-7", 1, "2026-11-03T10:00:00Z", parent="LINUX-5")],
        "BET-2": claimed_then("BET-2", reason("BET-2", 0, "2026-10-29T09:59:59Z", "https://github.com/o/r/pull/2"),
                              closed("BET-2", 0, "2026-10-29T10:00:00Z"), reopened("BET-2", 0, "2026-10-29T11:00:00Z"),
                              closed("BET-2", 0, "2026-10-29T12:00:00Z",
                                     links=["https://github.com/o/r/issues/2"])),
        "OSSV-8": claimed_then("OSSV-8", reason("OSSV-8", 0, "2026-10-30T09:59:59Z",
                                                "Measured: dispatch://OSSV-8/artifact/result-md"),
                               closed("OSSV-8", 0, "2026-10-30T10:00:00Z")),
        "OSSV-9": claimed_then("OSSV-9", reason("OSSV-9", 0, "2026-10-30T09:59:59Z", "Superseded by dispatch://OSSV-10"),
                               closed("OSSV-9", 0, "2026-10-30T10:00:00Z")),
    }
    session = pilot.measure(FakeDispatch(issues), "INFRA-167", START, END, [SESSION])["sessions"][0]

    assert session["issues_claimed"] == ["BET-2", "LINUX-5", "OSSV-8", "OSSV-9"]
    assert session["done_without_evidence"] == ["BET-2", "LINUX-5", "OSSV-9"]
    assert session["done_with_own_child_open"] == [{"issue": "LINUX-5", "open_children": ["LINUX-6"]}]


def test_measure_ties_each_ledger_done_line_to_the_first_close_at_or_after_it():
    """A LEDGER done line is evidence for one close: the first at or after it, so it cannot vouch for an earlier
    close, and a line posted after an issue's last close vouches for none."""
    def claimed_then(key: str, *rest) -> list[dict]:
        return [created(key, 1, "2026-10-27T16:00:00Z"), claimed(key, 2, "2026-10-27T16:01:00Z"),
                *(dict(e, seq=n) for n, e in enumerate(rest, 3))]

    pull = "https://github.com/o/r/pull/9"
    issues = {
        "INFRA-167": [
            created("INFRA-167", 1, "2026-10-20T00:00:00Z"),
            # BET-3 closes at 10:00 and 12:00; this line precedes only the second close.
            ledger(2, "2026-10-29T11:30:00Z", f"LEDGER done BET-3 fixed {pull}"),
            # BET-4's line precedes its only close.
            ledger(3, "2026-10-29T09:30:00Z", f"LEDGER done BET-4 fixed {pull}"),
            # BET-5's line follows its only close.
            ledger(4, "2026-10-29T10:30:00Z", f"LEDGER done BET-5 fixed {pull}"),
        ],
        "BET-3": claimed_then("BET-3", closed("BET-3", 0, "2026-10-29T10:00:00Z"),
                              reopened("BET-3", 0, "2026-10-29T11:00:00Z"), closed("BET-3", 0, "2026-10-29T12:00:00Z")),
        "BET-4": claimed_then("BET-4", closed("BET-4", 0, "2026-10-29T10:00:00Z")),
        "BET-5": claimed_then("BET-5", closed("BET-5", 0, "2026-10-29T10:00:00Z")),
        # The only link is a pull request in the issue's external links.
        "BET-6": claimed_then("BET-6", closed("BET-6", 0, "2026-10-29T10:00:00Z", links=[pull])),
    }
    session = pilot.measure(FakeDispatch(issues), "INFRA-167", START, END, [SESSION])["sessions"][0]

    assert session["done_without_evidence"] == ["BET-3", "BET-5", "BET-6"]
