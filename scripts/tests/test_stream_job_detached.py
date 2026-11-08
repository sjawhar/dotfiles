#!/usr/bin/env python3
"""stream-job: a job that outlives one tick must not stop every other job.

The runner runs due jobs one at a time. On 2026-11-08 the gate lane's daily publication scan
(90-minute timeout, started 05:04:00Z) held the single runner, and `infra-oomnotify` — a
four-minute OOM notifier — together with four other jobs did not start again until it
finished. `detached=yes` starts such a job in a session of its own and returns.

The two properties worth testing are the ones that are easy to lose:

  - the LOCK still forbids a second copy, because flock lives on the open file description
    and the child inherits it. A detached job that gave that up would overlap with itself.
  - the FINISH is still recorded by the child, because a detached job whose finish nobody
    writes is indistinguishable from a job that never finishes — which is exactly what
    unit-health-watch alerts on, and the alert that started this.
"""

from __future__ import annotations

import subprocess
import time
import unittest
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "stream-job"


class Detached(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.jobs = self.root / "jobs"
        self.state = self.root / "state"
        self.jobs.mkdir()
        self.state.mkdir()

    def write_job(self, name: str, command: str, **extra: str) -> None:
        lines = [
            "owner=test",
            f"command={command}",
            f"workdir={self.root}",
            "every=1s",
            "timeout=60s",
        ]
        lines += [f"{k}={v}" for k, v in extra.items()]
        (self.jobs / f"{name}.job").write_text("\n".join(lines) + "\n")

    def run_once(self) -> str:
        done = subprocess.run(
            ["python3", str(RUNNER), "--jobs", str(self.jobs), "--state", str(self.state)],
            capture_output=True, text=True, timeout=120,
        )
        return done.stdout + done.stderr

    def read(self, job: str, name: str) -> str:
        path = self.state / job / name
        return path.read_text().strip() if path.exists() else ""

    def test_a_detached_job_returns_at_once_and_does_not_hold_the_runner(self) -> None:
        self.write_job("slow", "sleep 30", detached="yes")
        self.write_job("quick", "echo quick-ran", **{})
        started = time.monotonic()
        out = self.run_once()
        took = time.monotonic() - started
        self.assertLess(took, 15, f"the runner waited for the detached job: {took:.1f}s\n{out}")
        self.assertIn("started detached", out)
        # The short job ran in the same pass, which is the whole point.
        self.assertEqual(self.read("quick", "last-line"), "quick-ran", out)

    def test_the_child_records_its_own_exit_and_finish(self) -> None:
        # Long enough that the parent's own record is observable before the child's.
        self.write_job("brief", "sleep 3; exit 3", detached="yes")
        self.run_once()
        self.assertEqual(self.read("brief", "last-exit"), "detached",
                         "the parent should record that it detached, not a status it cannot know")
        # Poll for the LAST thing the child writes, not the first: exit lands before finish,
        # so waiting on exit can observe the pair half-written.
        for _ in range(150):
            if self.read("brief", "last-finish"):
                break
            time.sleep(0.2)
        self.assertTrue(self.read("brief", "last-finish"), "no finish was recorded")
        self.assertEqual(self.read("brief", "last-exit"), "3",
                         "a detached job that never records its status looks hung for ever")

    def test_a_second_copy_cannot_start_while_the_detached_one_runs(self) -> None:
        self.write_job("slow", "sleep 30", detached="yes")
        self.run_once()
        # The job is due again only after `every`; without this the second pass runs nothing
        # and the lock is never tested.
        time.sleep(2)
        out = self.run_once()
        self.assertIn("still holds", out,
                      f"the lock did not survive into the detached child:\n{out}")

    def test_an_ordinary_job_is_unchanged(self) -> None:
        self.write_job("plain", "echo plain-ran")
        self.run_once()
        self.assertEqual(self.read("plain", "last-exit"), "0")
        self.assertEqual(self.read("plain", "last-line"), "plain-ran")
        self.assertTrue(self.read("plain", "last-finish"))


if __name__ == "__main__":
    unittest.main()
