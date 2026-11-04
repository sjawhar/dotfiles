#!/usr/bin/env python3
"""Regression tests for the bet-owner-nickmine alerts of 2026-11-02.

Session 01a19366-958c-7295-8c2b-171fa219c033 held role bet-owner-nickmine and ended on purpose:
its transcript's last entry is a normal session_exit recorded at 05:41:32.174Z. The watch read
that at 05:47:45Z and wrote `exited_reported_at` on the incident. Then:

- 07:50:22Z omp-transcript-offload replaced the transcript with a symlink into /mnt/durable;
- 13:20:57Z the first run to finish in hours walked the session root, ran out of its 3 s budget
  before it reached the transcript, found no exit, saw no `alerted_at` and sent a FIRST ALERT to
  the chief of staff, the INFRA lead and TASKCOACH;
- 14:08:13Z the session sweep (dispatch://OSSV-240) imported the transcript into Postgres and
  deleted it at 14:10:26Z; at 14:10:46Z the watch escalated ("STILL UNSEEN after 10 minutes",
  49 minutes after the alert).

Pinned here: a recorded exit holds whether or not the transcript can be read again; the holder's
own session directory is listed before the budgeted walk; a swept transcript is read back from
the table; a resume after the exit undoes it; and the one repeat says that it is one.

Nothing here touches the real tree, the real Envoy, the real sweep record or the real database.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "envoy-role-holder-watch"
LOADER = importlib.machinery.SourceFileLoader("envoy_role_holder_watch_exits", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

ROLE = "bet-owner-nickmine"
HOLDER = "01a19366-958c-7295-8c2b-171fa219c033"
NAME = f"2026-10-31T19-18-56-140Z_{HOLDER}.jsonl"
EXITED_AT = "2026-11-02T05:41:32.174Z"
CHIEF, INFRA, TASKCOACH = (
    "01a13dca-e979-7264-af32-f654ef57a6f5",
    "01a13c57-d826-7242-80fe-5f0670188b11",
    "01a16a9e-a90d-7148-95ed-0e9e7839dbd2",
)
# The last two entries of the holder's transcript, as Postgres holds them.
LAST = (
    '{"type":"message","id":"024bb533","parentId":"2a3db6e6","timestamp":"2026-11-02T05:40:46.297Z",'
    '"message":{"role":"assistant","content":[{"type":"text","text":"My session is done."}]}}\n'
    '{"type":"custom","customType":"session_exit","data":{"reason":"dispose","kind":"normal",'
    '"recordedAt":"2026-11-02T05:41:32.174Z"},"id":"da4a6644","parentId":"024bb533",'
    '"timestamp":"2026-11-02T05:41:32.174Z","runtime":{"version":"18.13.3"}}\n'
)
OTHER = (
    '{"type":"custom","customType":"envoy-replay-state","data":{},"id":"6feb73ae",'
    '"timestamp":"2026-11-02T13:00:00.000Z"}\n'
)


class RunEnvoy:
    """Envoy with no live session and no registry entry for anyone; sends are recorded."""

    url = "http://127.0.0.1:9020"
    sent: list = []

    def __init__(self, timeout: float = 8.0) -> None:
        pass

    def sessions(self) -> list:
        return []

    def registry(self, session_id: str):
        return 404, {"error": "not found"}

    def send(self, session_id: str, message: str) -> bool:
        RunEnvoy.sent.append((session_id, message))
        return True

    def publish(self, topic: str, message: str) -> bool:
        return True


class Incident(unittest.TestCase):
    """An empty session root, an empty sweep record and a fake psql, all in a temp directory."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dir = self.root / "src/bet-design-lane"
        self.dir.mkdir(parents=True)
        (self.root / "agent/sessions").mkdir(parents=True)
        self.sweep = self.root / "omp-session-sql"
        self.sweep.mkdir()
        (self.sweep / "dsn").write_text("postgresql://fake\n")
        self.calls = self.root / "psql-calls"
        self.tail = self.root / "psql-tail"
        self.logs: list[str] = []
        RunEnvoy.sent = []
        # getattr with a default, so the same tests run against the script before this change
        # and fail on what it decides rather than on a name it did not have. A period's budget
        # counts from its process's start, which in a test is pytest's: it starts now instead.
        for name, value in (
            ("SESSION_ROOT", self.root / "agent/sessions"), ("SWEEP_STATE", self.sweep),
            ("_TRANSCRIPT_INDEX", None), ("_SWEEP_INDEX", None),
            ("PSQL", None), ("Envoy", RunEnvoy), ("log", self.logs.append),
            ("omp_sessions_by_pid", lambda: {}), ("transcript_holders", lambda path: []),
            ("handover_files", lambda session, directory: []),
            ("check_panes", lambda *args: (0, 0, 0, 0)), ("process_started", time.time),
        ):
            original = getattr(WATCH, name, None)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)
        for memo in (WATCH._KNOWN_TRANSCRIPTS, getattr(WATCH, "_SWEPT", {})):
            memo.clear()
            self.addCleanup(memo.clear)
        self.now = time.time()
        self.state = self.root / "state.json"
        self.fake_psql(0)

    def fake_psql(self, status: int) -> None:
        script = self.root / "psql"
        script.write_text(
            "#!/bin/sh\n"
            f"cat > '{self.root}/psql-query'\n"
            f"echo call >> '{self.calls}'\n"
            + (f"printf '1793598092173\\n'; cat '{self.tail}'\n" if status == 0 else
               "echo 'psql: error: connection refused' >&2\n")
            + f"exit {status}\n"
        )
        script.chmod(0o755)
        self.tail.write_text(LAST)
        WATCH.PSQL = (str(script),)

    def psql_calls(self) -> int:
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def session_dir(self) -> Path:
        path = WATCH.session_dir_of(str(self.dir))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def swept(self) -> str:
        """The sweep's record of deleting the transcript, as deleted.tsv holds it."""
        path = str(self.session_dir() / NAME)
        (self.sweep / "deleted.tsv").write_text(
            f"{path}/GroundData.jsonl\t683373\t17bb\t/mnt/durable/x/GroundData.jsonl\t2026-11-02T14:10:26Z\n"
            f"{path}\t8797027\t0dd1ced2\t/mnt/durable/sami/omp-sessions-moved/{NAME}\t2026-11-02T14:10:26Z\n"
        )
        return path

    def write_state(self, incident: dict | None) -> None:
        self.state.write_text(json.dumps({
            "roles": {ROLE: {"holder": HOLDER, "host": socket.gethostname(), "dir": str(self.dir),
                             "title": "DESIGN: Plan task design analysis across candidates"}},
            "incidents": {f"{ROLE}|{HOLDER}": incident} if incident else {},
        }))

    def period(self, dry_run: bool = False) -> list[str]:
        self.logs.clear()
        WATCH._TRANSCRIPT_INDEX = None
        options = argparse.Namespace(
            state=self.state, dry_run=dry_run, write_state=True, print_alert=False,
            http_timeout=1.0, grace=60.0, write_grace=120.0, dir_grace=300.0, escalate=600.0,
            check_hosts=(),
        )
        WATCH.run_once(options)
        return [line for line in self.logs if line.startswith(ROLE)]

    def incident(self) -> dict:
        return json.loads(self.state.read_text())["incidents"][f"{ROLE}|{HOLDER}"]

    def no_walk_budget(self) -> None:
        original = WATCH.TRANSCRIPT_WALK_SECONDS
        WATCH.TRANSCRIPT_WALK_SECONDS = -1.0
        self.addCleanup(setattr, WATCH, "TRANSCRIPT_WALK_SECONDS", original)


class RecordedExitTest(Incident):
    # The incident as the state file held it before 13:20:57Z: the exit was recorded at
    # 05:47:45Z, and nothing had been sent. Times are shifted to now, gaps kept.
    def before_1320(self) -> dict:
        reported = self.now - (13 * 3600 + 20 * 60 + 57 - (5 * 3600 + 47 * 60 + 45))
        return {
            "opened_at": reported, "exited_reported_at": reported, "exited_at": EXITED_AT,
            "last_seen_alive": reported - 373, "last_proc_pid": 894738,
            "last_pid_seen_at": reported - 954, "verdict": "I cannot see this session",
            "dir": str(self.dir), "host": socket.gethostname(),
        }

    def test_a_recorded_exit_holds_when_the_transcript_cannot_be_read(self) -> None:
        """13:20:57Z: no transcript reachable, so the run sent a first alert. It must not."""
        self.write_state(self.before_1320())
        self.no_walk_budget()

        lines = self.period() + self.period()

        self.assertFalse([line for line in lines if "ALERT" in line or "ESCALATED" in line], lines)
        self.assertEqual(RunEnvoy.sent, [])
        self.assertEqual(self.incident()["exited_at"], EXITED_AT)

    def test_the_wrong_alert_of_1320_is_closed_once_with_its_reason(self) -> None:
        """The state file at 14:15Z: alerted 13:20:57Z, escalated 14:10:34Z, exit recorded 05:47:45Z."""
        incident = self.before_1320()
        incident.update({
            "alerted_at": self.now - 54 * 60, "escalated_at": self.now - 4 * 60,
            "told": [CHIEF, INFRA, TASKCOACH], "transcript": "",
        })
        self.write_state(incident)

        first = self.period()
        second = self.period()

        self.assertEqual(sorted(target for target, _ in RunEnvoy.sent), sorted([CHIEF, INFRA, TASKCOACH]))
        text = RunEnvoy.sent[0][1]
        self.assertIn(f"holder exited normally at {EXITED_AT}", text)
        self.assertIn("was wrong", text)
        self.assertIn("ENVOY ROLE HOLDER EXITED NORMALLY", text)
        self.assertTrue(any("CLOSED, HOLDER EXITED NORMALLY" in line for line in first), first)
        self.assertEqual(second, [])
        self.assertEqual(len(RunEnvoy.sent), 3)

    def test_a_session_that_wrote_after_its_exit_was_resumed_and_alerts_again(self) -> None:
        self.write_state(self.before_1320())
        transcript = self.session_dir() / NAME
        transcript.write_text('{"type":"session","id":"' + HOLDER + '"}\n' + LAST + OTHER)
        os.utime(transcript, (self.now - 900, self.now - 900))

        lines = self.period()

        self.assertTrue(any("it was resumed" in line for line in lines), lines)
        self.assertTrue(any("ALERT sent" in line for line in lines), lines)


class HolderDirectoryTest(Incident):
    def test_the_holder_directory_is_listed_before_the_budgeted_walk(self) -> None:
        """13:20:57Z: the transcript, a symlink into /mnt/durable, sat in the holder's own
        session directory while the walk of the whole root ran out of budget."""
        moved = self.root / "durable" / NAME
        moved.parent.mkdir()
        moved.write_text('{"type":"session","id":"' + HOLDER + '"}\n' + LAST)
        os.utime(moved, (self.now - 30000, self.now - 30000))
        (self.session_dir() / NAME).symlink_to(moved)
        self.no_walk_budget()

        evidence = WATCH.holder_evidence(
            RunEnvoy(), HOLDER, {}, {}, 120.0, self.now, str(self.dir), 300.0,
        )

        self.assertEqual(evidence["exited_at"], EXITED_AT)
        self.assertEqual(evidence["transcript"], str(self.session_dir() / NAME))
        self.assertFalse(evidence["walk_missed"])

    def test_a_holder_gone_before_the_memo_exits_quietly_on_its_first_period(self) -> None:
        transcript = self.session_dir() / NAME
        transcript.write_text('{"type":"session","id":"' + HOLDER + '"}\n' + LAST)
        os.utime(transcript, (self.now - 30000, self.now - 30000))
        self.write_state(None)
        self.no_walk_budget()

        lines = self.period()

        self.assertTrue(any(f"holder exited normally at {EXITED_AT}" in line for line in lines), lines)
        self.assertFalse(any("ALERT" in line for line in lines), lines)


class SweptTranscriptTest(Incident):
    def test_a_swept_transcript_is_read_back_from_the_table_once(self) -> None:
        path = self.swept()

        first = WATCH.holder_evidence(RunEnvoy(), HOLDER, {}, {}, 120.0, self.now, str(self.dir), 300.0)
        second = WATCH.holder_evidence(RunEnvoy(), HOLDER, {}, {}, 120.0, self.now, str(self.dir), 300.0)

        self.assertEqual(first["exited_at"], EXITED_AT)
        self.assertEqual(second["exited_at"], EXITED_AT)
        self.assertEqual(self.psql_calls(), 1)
        query = (self.root / "psql-query").read_text()
        self.assertIn(f"'{path}'", query)
        self.assertIn(f"'sessions/{Path(path).relative_to(WATCH.SESSION_ROOT)}'", query)
        row = next(sentence for name, _, sentence in first["sources"] if name == "transcript")
        self.assertIn("Postgres omp_sessions", row)
        self.assertIn("2026-11-02T14:10:26Z", row)
        self.assertFalse(first["walk_missed"])

    def test_a_failed_read_says_so_and_is_tried_again_next_run(self) -> None:
        self.swept()
        self.fake_psql(2)

        evidence = WATCH.holder_evidence(RunEnvoy(), HOLDER, {}, {}, 120.0, self.now, str(self.dir), 300.0)
        WATCH.holder_evidence(RunEnvoy(), HOLDER, {}, {}, 120.0, self.now, str(self.dir), 300.0)

        self.assertIsNone(evidence["exited_at"])
        row = next(sentence for name, _, sentence in evidence["sources"] if name == "transcript")
        self.assertIn("connection refused", row)
        self.assertEqual(self.psql_calls(), 2)

    def test_the_holder_of_this_incident_reads_as_exited_from_its_swept_transcript(self) -> None:
        """The state after 14:10:26Z with no exit recorded: only the table still holds it."""
        self.swept()
        self.write_state(None)

        lines = self.period()
        lines += self.period()

        self.assertTrue(any(f"holder exited normally at {EXITED_AT}" in line and "Postgres" in line
                            for line in lines), lines)
        self.assertFalse(any("ALERT" in line or "ESCALATED" in line for line in lines), lines)
        self.assertEqual(self.psql_calls(), 1)
        self.assertIn(HOLDER, json.loads(self.state.read_text())["swept"])

    def test_the_alert_tells_how_to_resume_a_swept_session(self) -> None:
        path = self.swept()
        self.tail.write_text(LAST + OTHER)  # not an exit: a crashed lane whose file was swept
        incident = {"opened_at": self.now - 700, "dir": str(self.dir)}
        self.write_state(incident)

        self.period()
        getattr(WATCH, "_SWEPT", {}).clear()
        text = WATCH.alert_text(ROLE, self.incident(), HOLDER, self.now, "first alert")

        self.assertIn(f"omp --resume {path}", text)
        self.assertIn("INFRA-754", text)
        self.assertNotIn("THIS STEP MAY NOT APPLY: no transcript", text)


class RepeatTest(Incident):
    def test_the_one_repeat_names_the_first_sighting_and_is_not_repeated(self) -> None:
        opened, alerted = self.now - 3000, self.now - 2940
        self.write_state({
            "opened_at": opened, "alerted_at": alerted, "told": [CHIEF, INFRA, TASKCOACH],
            "dir": str(self.dir), "transcript": "",
        })
        self.no_walk_budget()

        first = self.period()
        later = self.period() + self.period()

        self.assertEqual([target for target, _ in RunEnvoy.sent], [CHIEF])
        text = RunEnvoy.sent[0][1]
        # The message itself is now the decision only (at most 600 characters) and names the
        # report file that holds the whole alert; both carry the two times and the one repeat.
        report = Path(text.rsplit("Full report: ", 1)[1].strip())
        self.assertLessEqual(len(text), 600, text)
        for carries in (text, report.read_text()):
            self.assertIn(f"REPEAT of the incident first seen at {WATCH.iso(opened)}", carries)
            self.assertIn(f"alerted at {WATCH.iso(alerted)}", carries)
            self.assertIn("49 min 0 s after its first alert", carries)
            self.assertNotIn("after 10 minutes", carries)
        self.assertIn("THIS IS A REPEAT, AND THE ONLY ONE", report.read_text())
        self.assertTrue(any("a repeat of the incident first seen at" in line for line in first), first)
        self.assertFalse(any("ESCALATED" in line for line in later), later)


if __name__ == "__main__":
    unittest.main()
