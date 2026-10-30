#!/usr/bin/env python3
"""Regression tests for envoy-role-holder-watch's fifth source and its named suppression.

On 2026-10-30 the watch alerted on roles node-envprobe-d10302216-2 (22:21:26Z),
node-envprobe-d10302249-2 (22:52:08Z) and node-envprobe-d10302253-1 (22:54:39Z). All four
session-level sources were CORRECTLY silent for every one of them: the Verified Linux
scheduler's envprobe claims a role from a process that is not an omp session, writes the lane's
transcripts into the run directory instead of ~/.omp/agent/sessions, and releases the role at
the end of each run. Nothing the watch read could ever see those holders, so the alerts could
never clear, they escalated, and each repeat taught a reader to skip the next one.

Two things are pinned here.

The fifth source: the newest regular file under the holder's own working directory, NAMED and
dated absolutely. The chief of staff's qualifier is a test, not a comment -- an mtime says that
one file was written, never that the lane is working, and he proved it on the first incident
where ps.log grew about a kilobyte a second while the probe's own outputs had stopped three
minutes earlier. So every sentence this source produces names its file, and the quieting
message says in its own words what it does not establish.

The suppression: narrow to the scheduler's role shape, dated, and visible in every alert of
every other class while it holds.

Nothing here touches the real tree, the real Envoy or the real state file.
"""

import importlib.machinery
import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "envoy-role-holder-watch"
LOADER = importlib.machinery.SourceFileLoader("envoy_role_holder_watch_directory", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

HOLDER = "01a18f00-e348-7617-8e7a-a1d0b774b455"
ROLE = "node-envprobe-d10302249-2"


class FakeEnvoy:
    """Envoy answering what it answered for every one of these holders: 404."""

    url = "http://127.0.0.1:9020"

    def registry(self, session_id: str):
        return 404, {"error": "not found"}


class Lane(unittest.TestCase):
    """A run directory on disk, with an empty session root so nothing real is read."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dir = self.root / "runs/d10302249"
        self.dir.mkdir(parents=True)
        sessions = self.root / "sessions"
        sessions.mkdir()
        original_root, original_index = WATCH.SESSION_ROOT, WATCH._TRANSCRIPT_INDEX
        WATCH.SESSION_ROOT, WATCH._TRANSCRIPT_INDEX = sessions, None
        self.addCleanup(
            lambda: setattr(WATCH, "_TRANSCRIPT_INDEX", original_index)
        )
        self.addCleanup(lambda: setattr(WATCH, "SESSION_ROOT", original_root))
        self.now = time.time()

    def write(self, name: str, age: float, text: str = "x\n") -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        stamp = self.now - age
        os.utime(path, (stamp, stamp))
        return path

    def evidence(self, directory: str | None = None, dir_grace: float = 300.0) -> dict:
        return WATCH.holder_evidence(
            FakeEnvoy(), HOLDER, {}, {}, 120.0, self.now,
            str(self.dir) if directory is None else directory, dir_grace,
        )

    def row(self, evidence: dict, name: str) -> str:
        return next(sentence for source, _, sentence in evidence["sources"] if source == name)


class DirectorySourceTest(Lane):
    def test_a_directory_written_seconds_ago_quiets_and_names_the_file(self) -> None:
        """The 2026-10-30 shape: nothing can see the holder, the lane's directory is live."""
        self.write("ps.log", age=3)
        evidence = self.evidence()

        self.assertFalse(evidence["alive"])
        self.assertTrue(evidence["dir_fresh"])
        self.assertEqual(evidence["dir_file"], str(self.dir / "ps.log"))
        self.assertIn("ps.log", self.row(evidence, "directory"))
        self.assertIn("3 s ago", self.row(evidence, "directory"))
        # It never upgrades to a sighting, however recent the write is.
        self.assertFalse(any(seen for source, seen, _ in evidence["sources"] if source == "directory"))

    def test_a_directory_untouched_for_hours_does_not_quiet(self) -> None:
        self.write("events.log", age=5 * 3600)
        evidence = self.evidence()

        self.assertFalse(evidence["dir_fresh"])
        self.assertIn("events.log", self.row(evidence, "directory"))
        self.assertIn("5 h 0 min ago", self.row(evidence, "directory"))

    def test_a_directory_that_does_not_exist_does_not_quiet_and_says_so(self) -> None:
        evidence = self.evidence(directory=str(self.root / "gone"))

        self.assertFalse(evidence["dir_fresh"])
        self.assertEqual(evidence["dir_file"], "")
        self.assertIn("No such file or directory", self.row(evidence, "directory"))
        self.assertIn(str(self.root / "gone"), self.row(evidence, "directory"))

    def test_a_fresh_log_beside_old_outputs_quiets_and_names_the_log(self) -> None:
        """The chief of staff's own counter-example, pinned as the literal it is.

        On incident d10302216 the probe's outputs stopped at 22:18:55Z while ps.log kept
        growing. The decision this test fixes: that DOES quiet the alert -- a reader cannot
        answer an alert about a holder no source will ever see -- and the sentence must name
        ps.log, not "the directory", so a reader can see for themselves that the fresh file is
        a log beside the work rather than the work.
        """
        self.write("R-output.txt", age=4000)
        self.write("facts.json", age=3800)
        self.write("ps.log", age=1)
        evidence = self.evidence()

        self.assertTrue(evidence["dir_fresh"])
        self.assertEqual(
            self.row(evidence, "directory"),
            f"the newest file under {self.dir} is {self.dir}/ps.log, written"
            f" {WATCH.iso(self.now - 1)} (1 s ago; 3 entries walked). This says that file was"
            " written. It does NOT say the lane is working: on 2026-10-30 ps.log in such a"
            " directory grew about a kilobyte a second while the probe's own outputs had"
            " stopped 3 minutes earlier",
        )

    def test_a_truncated_walk_never_quiets(self) -> None:
        """"I did not finish looking" is not "something was written here"."""
        self.write("ps.log", age=1)
        original = WATCH.DIR_SCAN_ENTRIES
        WATCH.DIR_SCAN_ENTRIES = 0
        self.addCleanup(lambda: setattr(WATCH, "DIR_SCAN_ENTRIES", original))

        evidence = self.evidence()

        self.assertFalse(evidence["dir_fresh"])
        self.assertTrue(evidence["dir_truncated"])

    def test_the_holders_own_transcript_is_found_in_its_working_directory(self) -> None:
        """envprobe writes <run>/R/<stamp>_<holder>.jsonl and nothing under the session root."""
        WATCH.transcript_holders = lambda path: []
        transcript = self.write(f"R/2026-10-30T22-49-22-504Z_{HOLDER}.jsonl", age=30)

        evidence = self.evidence()

        self.assertEqual(evidence["transcript"], str(transcript))
        self.assertTrue(evidence["alive"])
        self.assertIn("in the holder's own working directory", self.row(evidence, "transcript"))


class QuietMessageTest(Lane):
    def test_the_quieting_message_never_claims_the_lane_is_working(self) -> None:
        self.write("ps.log", age=2)
        evidence = self.evidence()
        text = WATCH.clear_text(
            ROLE, {"opened_at": self.now - 120}, HOLDER, evidence["dir_sentence"], self.now,
            "quiet",
        )

        self.assertIn("ps.log", text)
        self.assertIn("it does not say the lane is working", text)
        self.assertIn("does not say the holder is alive", text)
        self.assertNotIn("No action is needed", text)
        self.assertIn("NOT ALERTING AGAIN", text)

    def test_an_ordinary_clear_still_says_the_holder_was_seen(self) -> None:
        text = WATCH.clear_text(ROLE, {"opened_at": self.now - 60}, HOLDER, "pid 1 holds it", self.now)

        self.assertIn("has a holder this watch can see again", text)
        self.assertIn("No action is needed", text)


class AlertTextTest(unittest.TestCase):
    def test_the_alert_says_the_holder_may_not_be_an_omp_session(self) -> None:
        text = WATCH.alert_text(
            ROLE,
            {
                "opened_at": 1793400728.0, "dir": "/mnt/nvme/sami/tmp/x", "title": "",
                "host": "sami-rocqperf", "sources": [], "transcript": "", "pane": "",
            },
            HOLDER, 1793400789.0, "first alert",
        )

        self.assertIn("THE HOLDER MAY NOT BE AN omp SESSION AT ALL", text)
        # The two steps that send a reader to something that may never have existed.
        self.assertEqual(text.count("THIS STEP MAY NOT APPLY"), 2)
        self.assertIn("may never have had one", text)
        self.assertIn("nothing to resume in place", text)


class SuppressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = WATCH.SUPPRESSIONS[0]
        self.until = WATCH.epoch_of(self.rule["until"])

    def test_it_matches_the_scheduler_roles_and_nothing_else(self) -> None:
        for role in ("node-envprobe-d10302249-2", "node-envprobe-d10302216-2", "node-envprobe-d10302253-1"):
            self.assertIsNotNone(WATCH.suppression_for(role, self.until - 60), role)
        for role in (
            "bet-owner-machcsl", "lead-verified-linux", "stream-lead-verified-linux",
            "node-envprobe-d10302249", "node-verified-linux-2", "linux-baseline",
        ):
            self.assertIsNone(WATCH.suppression_for(role, self.until - 60), role)

    def test_it_expires_by_itself(self) -> None:
        self.assertIsNone(WATCH.suppression_for(ROLE, self.until + 1))

    def test_a_reader_sees_it_in_another_classs_alert_while_it_holds(self) -> None:
        lines = WATCH.suppression_lines(
            self.until - 60, {self.rule["shown"]: {"count": 7, "role": ROLE, "at": self.until - 300}}
        )

        self.assertEqual(len(lines), 1)
        self.assertIn("ACTIVE SUPPRESSION", lines[0])
        self.assertIn(self.rule["shown"], lines[0])
        self.assertIn(self.rule["until"], lines[0])
        self.assertIn("It has suppressed 7 role(s) so far", lines[0])
        self.assertIn("Every other role alerts normally", lines[0])

    def test_the_journal_form_keeps_the_scope_the_date_and_the_tally(self) -> None:
        """Every run writes this; the long form would be 700 characters every 30 s."""
        counts = {self.rule["shown"]: {"count": 4, "role": ROLE, "at": self.until - 300}}

        active = WATCH.suppression_lines(self.until - 60, counts, short=True)[0]
        expired = WATCH.suppression_lines(self.until + 60, counts, short=True)[0]

        self.assertEqual(
            active,
            f"SUPPRESSION ACTIVE: {self.rule['shown']} until {self.rule['until']}. It has"
            f" suppressed 4 role(s) so far, last {ROLE} at {WATCH.iso(self.until - 300)}.",
        )
        self.assertIn("SUPPRESSION EXPIRED, DELETE THE ENTRY", expired)

    def test_after_the_date_the_same_place_says_it_expired(self) -> None:
        lines = WATCH.suppression_lines(self.until + 86400, {})

        self.assertIn("EXPIRED SUPPRESSION", lines[0])
        self.assertIn("alerts again", lines[0])
        self.assertIn("until the entry is deleted", lines[0])


if __name__ == "__main__":
    unittest.main()
