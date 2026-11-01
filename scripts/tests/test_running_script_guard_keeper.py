#!/usr/bin/env python3
"""scripts/running-script-guard-keeper: hold the older guards' pass.lock while one of them lives.

Technique: XDG_STATE_HOME is a scratch directory, so the keeper reads and writes only a scratch
copy of the guard's state directory. An "older guard" is a `sleep` process with a
`sessions/<pid>-<uuid>` registration written after it started, which is all the keeper reads of
one; killing the sleep ends that session.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "running-script-guard-keeper"
NAMESPACE = "pid-" + "".join(ch for ch in os.readlink("/proc/self/ns/pid") if ch.isdigit())


def wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class Keeper(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "omp-running-script-guard" / NAMESPACE
        (self.state / "sessions").mkdir(parents=True)
        self.lock = self.state / "pass.lock"
        self.sessions: list[subprocess.Popen[bytes]] = []
        self.keeper: subprocess.Popen[str] | None = None

    def tearDown(self) -> None:
        if self.keeper and self.keeper.poll() is None:
            self.keeper.kill()
            self.keeper.wait()
        for proc in self.sessions:
            proc.kill()
            proc.wait()
        self.tmp.cleanup()

    def older_guard(self) -> subprocess.Popen[bytes]:
        """A live process registered as an older guard's session."""
        proc = subprocess.Popen(["sleep", "300"])
        self.sessions.append(proc)
        time.sleep(0.05)
        (self.state / "sessions" / f"{proc.pid}-{uuid.uuid4()}").write_text("")
        return proc

    def start(self) -> subprocess.Popen[str]:
        env = {**os.environ, "XDG_STATE_HOME": str(self.root)}
        self.keeper = subprocess.Popen([str(SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return self.keeper

    def ours(self) -> bool:
        try:
            return self.lock.read_text() == f"running-script-guard-keeper {self.keeper.pid}\n"
        except FileNotFoundError:
            return False

    def test_holds_and_refreshes_the_lock_while_an_older_guard_lives(self) -> None:
        self.older_guard()
        self.start()
        self.assertTrue(wait_for(self.ours), "the keeper never created pass.lock")
        first = self.lock.stat().st_mtime_ns
        self.assertTrue(wait_for(lambda: self.lock.stat().st_mtime_ns > first, 5), "pass.lock was not refreshed")

    def test_leaves_a_lock_another_process_holds_and_takes_it_once_free(self) -> None:
        self.older_guard()
        self.lock.write_text("")  # a pass holds it
        before = self.lock.stat()
        self.start()
        time.sleep(2.5)
        after = self.lock.stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(self.lock.read_text(), "")
        self.lock.unlink()  # the pass ends
        self.assertTrue(wait_for(self.ours), "the keeper did not take the freed lock")

    def test_releases_the_lock_and_exits_when_the_last_older_guard_ends(self) -> None:
        guard = self.older_guard()
        keeper = self.start()
        self.assertTrue(wait_for(self.ours))
        guard.kill()
        guard.wait()
        self.assertEqual(keeper.wait(timeout=10), 0)
        self.assertFalse(self.lock.exists())
        self.assertIn("no live process loaded an older guard", keeper.stdout.read())

    def test_a_registration_of_a_dead_or_reused_pid_holds_nothing(self) -> None:
        dead = subprocess.Popen(["true"])
        dead.wait()
        (self.state / "sessions" / f"{dead.pid}-{uuid.uuid4()}").write_text("")
        # pid 1 lives, but it started after this registration was written (1970): a reused pid.
        reused = self.state / "sessions" / f"1-{uuid.uuid4()}"
        reused.write_text("")
        os.utime(reused, (0, 0))
        keeper = self.start()
        self.assertEqual(keeper.wait(timeout=10), 0)
        self.assertFalse(self.lock.exists())

    def test_gives_back_recorded_write_bits_when_it_exits(self) -> None:
        guard = self.older_guard()
        script = self.root / "driver.sh"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o555)
        st = script.stat()
        (self.state / "held").mkdir()
        record = {"path": str(script), "dev": st.st_dev, "ino": st.st_ino, "mode": 0o755, "reduced": 0o555, "at": 0, "pid": 1, "cmdline": "sh driver.sh"}
        (self.state / "held" / "r.json").write_text(json.dumps(record))
        keeper = self.start()
        self.assertTrue(wait_for(self.ours))
        guard.kill()
        guard.wait()
        self.assertEqual(keeper.wait(timeout=10), 0)
        self.assertEqual(script.stat().st_mode & 0o7777, 0o755)
        self.assertEqual(list((self.state / "held").glob("*.json")), [])

    def test_sigterm_releases_the_lock(self) -> None:
        self.older_guard()
        keeper = self.start()
        self.assertTrue(wait_for(self.ours))
        keeper.send_signal(signal.SIGTERM)
        self.assertEqual(keeper.wait(timeout=10), 0)
        self.assertFalse(self.lock.exists())


if __name__ == "__main__":
    unittest.main()
