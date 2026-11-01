#!/usr/bin/env python3
"""Regression tests for envoy-role-holder-watch pane alerts."""

import importlib.machinery
import importlib.util
import json
import tempfile
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "envoy-role-holder-watch"
LOADER = importlib.machinery.SourceFileLoader("envoy_role_holder_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)


class PaneAlertTest(unittest.TestCase):
    def test_restart_command_says_when_automatic_resume_did_not_run(self) -> None:
        """A reader must not mistake a printed command for an automatic restart."""
        text = WATCH.pane_alert_text(
            {
                "pane_id": "%test",
                "where": "restart-test:0.0",
                "window_name": "restart-test",
                "shell": "bash",
                "shell_pid": 1,
                "title": "π Restart safety test",
                "cwd": "/tmp/restart-test",
                "opened_at": 0,
                "restart": "omp --resume 11111111-1111-1111-1111-111111111111",
                "session": "11111111-1111-1111-1111-111111111111",
                "session_source": "test fixture",
            },
            1,
        )

        self.assertIn("Automatic restart: NOT RUN", text)


class DirectoryInventoryTest(unittest.TestCase):
    """An unresolved pane alert must say what its session directory DOES hold.

    On 2026-11-01 pane %450 (/home/sami/src/eng-ops) was told no transcript carried its title
    while the directory held one, of a live session: the association was wrong, not missing.
    """

    def setUp(self) -> None:
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.home = Path(self.root.name)
        self.sessions = self.home / "sessions"
        self._root = WATCH.SESSION_ROOT
        WATCH.SESSION_ROOT = self.sessions
        self.addCleanup(setattr, WATCH, "SESSION_ROOT", self._root)

    def write(self, cwd: str, session_id: str, title: str) -> None:
        directory = WATCH.session_dir_of(cwd)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"2026-11-01T10-00-00_{session_id}.jsonl").write_text(
            json.dumps({"type": "title", "title": title}) + "\n"
        )

    def test_one_transcript_of_a_live_session_is_named_as_live(self) -> None:
        session_id = "01a18385-1111-7000-8000-000000000001"
        self.write("/var/lane-live", session_id, "eng-ops")

        line = WATCH.directory_inventory("/var/lane-live", {session_id: 4242})

        self.assertIn("holds 1 transcript:", line)
        self.assertIn(session_id, line)
        self.assertIn("eng-ops", line)
        self.assertIn("live in pid 4242", line)

    def test_an_empty_directory_says_it_holds_nothing(self) -> None:
        line = WATCH.directory_inventory("/var/lane-empty", {})

        self.assertIn("no transcript at all", line)

    def test_the_unresolved_alert_carries_the_line(self) -> None:
        session_id = "01a18385-1111-7000-8000-000000000002"
        self.write("/var/lane-alert", session_id, "eng-ops")
        incident = {
            "pane_id": "%450",
            "where": "lane:0.0",
            "window_name": "lane",
            "shell": "bash",
            "shell_pid": 7,
            "title": "π eng-ops",
            "cwd": "/var/lane-alert",
            "opened_at": 0,
            "directory_inventory": WATCH.directory_inventory(
                "/var/lane-alert", {session_id: 4242}
            ),
        }

        text = WATCH.pane_alert_text(incident, 1)

        self.assertIn("Last session: NOT RESOLVED", text)
        self.assertIn("holds 1 transcript:", text)
        self.assertIn("live in pid 4242", text)


if __name__ == "__main__":
    unittest.main()
