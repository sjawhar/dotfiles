#!/usr/bin/env python3
"""Replay a moved role holder and a holder absent from every checked host."""

import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

from test_envoy_role_holder_watch_tuning import Box, HOLDER, RunEnvoy, WATCH


class HostWatchTest(Box):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(WATCH.os.environ, {"AGENTBOX_HOST": ""}))
        self.enterContext(patch.object(WATCH.socket, "gethostname", return_value="sami-rocqperf"))
        self.calls = []
        self.remote_code = 1
        self.remote_timeout = False
        self.boxes = "agentbox-local\n"
        self.enterContext(patch.object(WATCH.subprocess, "run", side_effect=self.command))
        self.write_state(
            {"lead-lane": self.role(host="sami-rocqperf")},
            {f"lead-lane|{HOLDER}": {"opened_at": self.now - 700, "dir": str(self.dir)}},
        )

    def options(self, **overrides):
        return super().options(check_hosts=["sami-devbox", "sami-leanperf"], **overrides)

    def command(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[0] == "docker":
            return subprocess.CompletedProcess(argv, 0, self.boxes, "")
        if argv[0] != "ssh":
            raise AssertionError(argv)
        if self.remote_timeout:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, self.remote_code, "4242\tits command line names the session\n" if self.remote_code == 0 else "", "")

    def registration(self, host):
        return {"session_id": HOLDER, "host": host, "machine_id": "envoy-hub", "roles": ["lead-lane"],
                "dir": str(self.dir), "last_seen": (self.now - 500) * 1000}

    def test_a_remembered_holder_registered_on_devbox_is_not_judged_here(self):
        RunEnvoy.sessions_list = [self.registration("sami-devbox.us-west4-a.internal")]
        lines = self.period()
        self.assertEqual(RunEnvoy.sent, [])
        self.assertNotIn("lead-lane", self.data()["roles"])
        self.assertEqual(self.calls, [])
        self.assertTrue(any("sami-devbox" in line and "skip" in line for line in lines))

    def test_the_point_registry_host_overrides_the_remembered_host(self):
        with patch.object(RunEnvoy, "registry", return_value=(200, self.registration("sami-devbox"))):
            self.period()
        self.assertEqual(RunEnvoy.sent, [])
        self.assertNotIn("lead-lane", self.data()["roles"])

    def test_an_old_alert_is_cleared_when_the_holder_registers_elsewhere(self):
        self.write_state(
            {"lead-lane": self.role(host="sami-rocqperf")},
            {f"lead-lane|{HOLDER}": {"opened_at": self.now - 700, "alerted_at": self.now - 600,
                                    "told": ["the-original-recipient"]}},
        )
        RunEnvoy.sessions_list = [self.registration("sami-devbox")]
        self.period()
        self.assertEqual([target for target, _ in RunEnvoy.sent], ["the-original-recipient"])
        self.assertNotIn("ROLE HOLDER UNSEEN", RunEnvoy.sent[0][1])
        self.assertIn("sami-devbox", RunEnvoy.sent[0][1])
        self.assertNotIn("can see again", RunEnvoy.sent[0][1])
        self.assertEqual(self.data()["incidents"], {})

    def test_a_suppressed_role_does_not_pay_for_registry_or_ssh_reads(self):
        self.write_state({"node-envprobe-a1-1": self.role(host="sami-rocqperf")})
        with patch.object(RunEnvoy, "registry", side_effect=AssertionError("unneeded registry read")):
            self.period()
        self.assertEqual(RunEnvoy.sent, [])
        self.assertEqual(self.calls, [])

    def test_a_local_box_is_watched_but_a_remote_box_is_not(self):
        RunEnvoy.sessions_list = [self.registration("agentbox-elsewhere")]
        self.period()
        self.assertEqual(RunEnvoy.sent, [])
        RunEnvoy.sessions_list = [self.registration("agentbox-local")]
        self.period(grace=0)
        self.assertTrue(RunEnvoy.sent)

    def test_a_short_host_registration_is_watched_on_its_fqdn_host(self):
        RunEnvoy.sessions_list = [self.registration("sami-rocqperf")]
        with patch.object(WATCH.socket, "gethostname", return_value="sami-rocqperf.internal"):
            self.period()
        self.assertTrue(RunEnvoy.sent)

    def test_a_holder_missing_from_envoy_but_live_on_devbox_does_not_alert(self):
        self.remote_code = 0
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.period(dry_run=True, print_alert=True)
        self.assertNotIn("ROLE HOLDER UNSEEN", output.getvalue())
        self.assertEqual(self.data()["incidents"], {})
        self.assertTrue(any("sami-devbox" in argv for argv, _ in self.calls))

    def test_real_absence_still_alerts_and_names_each_checked_host(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.period(dry_run=True, print_alert=True)
        text = output.getvalue()
        self.assertIn("ROLE HOLDER UNSEEN", text)
        self.assertIn("Hosts checked: sami-rocqperf, sami-devbox, sami-leanperf", text)
        self.assertRegex(text, r"Next reading: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
        self.assertIn("wait one cycle", text)
        for argv, kwargs in self.calls:
            self.assertEqual(argv[-2:], ["omp-session-live", HOLDER])
            self.assertLessEqual(kwargs["timeout"], 5)
            self.assertIn("BatchMode=yes", argv)
        incident = self.data()["incidents"][f"lead-lane|{HOLDER}"]
        report = next((self.state.parent / "reports").glob("*alert.txt"))
        self.assertIn("sami-devbox: no live omp", report.read_text())
        self.assertIn("alerted_at", incident)

    def test_an_unreachable_host_is_reported_as_unchecked_not_absent(self):
        self.remote_timeout = True
        self.period()
        self.assertTrue(RunEnvoy.sent)
        report = next((self.state.parent / "reports").glob("*alert.txt"))
        self.assertIn("sami-devbox: timed out", report.read_text())
        self.assertNotIn("sami-devbox: no live omp", report.read_text())
        self.assertIn("timed out", RunEnvoy.sent[0][1])

    def test_every_short_message_keeps_the_next_reading_even_with_a_long_head(self):
        text, report = WATCH.compose(self.options(), "pane-alert", "full evidence", self.now, "x" * 1000)
        self.assertLessEqual(len(text), 600)
        self.assertRegex(text, r"\nNext reading: .*wait one cycle")
        self.assertTrue(text.endswith(f"Full report: {report}"))
        self.assertIn("Next reading:", report.read_text())


if __name__ == "__main__":
    unittest.main()
