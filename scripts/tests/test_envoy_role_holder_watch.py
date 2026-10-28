#!/usr/bin/env python3
"""Regression tests for envoy-role-holder-watch pane alerts."""

import importlib.machinery
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
