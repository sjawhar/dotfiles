#!/usr/bin/env python3
"""scripts/sami-said --dispatch runs scripts/agent-private-guard before every Dispatch request and before
it opens the live socket: a bearer that lists a private project stops the run with exit 2 before its first
request, and with a public one the requests are those of a run with no private project (the flag false:
no whoami at all).

The fake Dispatch, its stand-ins and the store are test_sami_said.py's; each run uses its own copy of
sami-said beside a copy of the guard that reads the stand-in flag (private_project_stubs.guarded_copy).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from private_project_stubs import FLAG_OFF, FLAG_ON, PRIV_TOKEN, PUB_TOKEN, WHOAMI
from test_sami_said import FakeDispatch, Store, run, stand_ins, start_dispatch


class PrivateProjects(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.root = base / "agent"
        Store(cls.root)
        cls.flag, metadata = stand_ins(cls)
        cls.server, cls.env, cls.script = start_dispatch(cls, base, cls.flag, metadata)
        cls.cache = base / "cache"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_as(self, token: str, flag: dict[str, object]) -> subprocess.CompletedProcess[str]:
        self.flag.body = flag
        self.flag.auth.clear()
        FakeDispatch.paths, FakeDispatch.subscribes = [], []
        shutil.rmtree(self.cache / "agent-private", ignore_errors=True)
        return run(self.root, "--json", "--dispatch", env={**self.env, "DISPATCH_TOKEN": token}, script=self.script)

    def test_a_priv_bearer_exits_2_before_its_first_request(self):
        done = self.run_as(PRIV_TOKEN, FLAG_ON)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("PRIV is private", done.stderr)
        self.assertTrue(self.flag.auth, "the guard read some flag other than the stand-in")
        self.assertEqual(FakeDispatch.requests(), [])
        self.assertEqual(FakeDispatch.subscribes, [])
        self.assertEqual(done.stdout, "")

    def test_a_pub_bearer_makes_the_requests_of_a_run_with_no_private_project(self):
        base = self.run_as(PUB_TOKEN, FLAG_OFF)
        self.assertEqual(base.returncode, 0, base.stderr)
        before = FakeDispatch.requests()
        self.assertNotIn(WHOAMI, FakeDispatch.paths)
        done = self.run_as(PUB_TOKEN, FLAG_ON)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(FakeDispatch.requests(), before)
        self.assertIn(WHOAMI, FakeDispatch.paths)
        self.assertEqual(done.stdout, base.stdout)

    def test_a_bearer_that_turns_private_before_the_live_socket_never_opens_it(self):
        # whoami answers PUB for the two reads that find the head, then PRIV: the socket's own guard refuses.
        FakeDispatch.private_from = 3
        try:
            done = self.run_as(PUB_TOKEN, FLAG_ON)
        finally:
            FakeDispatch.private_from = None
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("PRIV is private", done.stderr)
        self.assertEqual(FakeDispatch.requests(), ["/api/v1/issues", "/api/v1/issues/LINUX-1157/events"])
        self.assertEqual(FakeDispatch.subscribes, [])


if __name__ == "__main__":
    unittest.main()
