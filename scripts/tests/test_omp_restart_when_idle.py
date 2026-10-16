"""omp-restart-when-idle types /restart into a pane only at the session's next idle turn end."""

import itertools
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "omp-restart-when-idle"
ATTENTION = Path(__file__).resolve().parent.parent / "tmux-attention"


class RestartWhenIdle(unittest.TestCase):
    sockets = itertools.count()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "attention"
        self.dir.mkdir()
        self.socket = f"restart-idle-test-{os.getpid()}-{next(self.sockets)}"
        self.tmux = ["tmux", "-L", self.socket]
        # `cat` echoes what is typed into the pane, so capture-pane shows the keys that arrived.
        subprocess.run([*self.tmux, "-f", "/dev/null", "new-session", "-d", "-s", "w", "-x", "120", "-y", "20", "cat"], check=True)
        subprocess.run([*self.tmux, "split-window", "-d", "-t", "w", "cat"], check=True)
        panes = subprocess.run([*self.tmux, "list-panes", "-t", "w", "-F", "#{pane_id}"], capture_output=True, text=True, check=True).stdout.split()
        self.pane, self.other = panes[0], panes[1]
        self.server = subprocess.run([*self.tmux, "display", "-p", "#{pid}"], capture_output=True, text=True, check=True).stdout.strip()
        self.env = {**os.environ, "OMP_ATTENTION_DIR": str(self.dir), "TMUX_ATTENTION_TMUX": " ".join(self.tmux)}

    def tearDown(self) -> None:
        subprocess.run([*self.tmux, "kill-server"], check=False)
        self.tmp.cleanup()

    def start(self, *args: str) -> subprocess.Popen[str]:
        return subprocess.Popen([str(SCRIPT), *args], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def push(self, pane: str, stamp_ms: int) -> None:
        """What attention-queue.ts does when a turn ends idle."""
        line = {"pane": pane, "server": self.server, "session": "s", "title": "t", "cwd": "/tmp", "at": "2026-01-01T00:00:00Z"}
        subprocess.run([str(ATTENTION), "push", json.dumps(line), str(stamp_ms)], env=self.env, check=True, capture_output=True)

    def typed(self, pane: str) -> str:
        return subprocess.run([*self.tmux, "capture-pane", "-p", "-t", pane], capture_output=True, text=True, check=True).stdout

    def test_sends_restart_at_the_next_idle_turn_end(self) -> None:
        proc = self.start(self.pane, "20")
        time.sleep(1.5)
        self.assertNotIn("/restart", self.typed(self.pane), "nothing is typed before the turn ends idle")
        self.push(self.pane, int(time.time() * 1000))
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertEqual(proc.stdout.read(), "", "a sent restart prints nothing over the composer")
        time.sleep(0.3)
        self.assertIn("/restart", self.typed(self.pane))
        self.assertNotIn("/restart", self.typed(self.other))

    def test_an_idle_line_from_before_the_call_does_not_count(self) -> None:
        self.push(self.pane, int(time.time() * 1000) - 60_000)
        proc = self.start(self.pane, "2")
        self.assertEqual(proc.wait(timeout=10), 1)
        self.assertNotIn("/restart", self.typed(self.pane))

    def test_another_panes_idle_turn_end_does_not_count(self) -> None:
        proc = self.start(self.pane, "3")
        time.sleep(0.5)
        self.push(self.other, int(time.time() * 1000))
        self.assertEqual(proc.wait(timeout=10), 1)
        self.assertNotIn("/restart", self.typed(self.pane))
        self.assertNotIn("/restart", self.typed(self.other))

    def test_no_idle_turn_end_gives_up_loudly_and_sends_nothing(self) -> None:
        proc = self.start(self.pane, "2")
        self.assertEqual(proc.wait(timeout=10), 1)
        out = proc.stdout.read()
        self.assertIn(f"pane {self.pane} had no idle turn end within 2s", out)
        self.assertIn("/restart was not sent", out)
        self.assertNotIn("/restart", self.typed(self.pane))

    def test_refuses_a_pane_that_is_not_a_pane_id(self) -> None:
        result = subprocess.run([str(SCRIPT), "w:0"], env=self.env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("PANE is a tmux pane id", result.stderr)


if __name__ == "__main__":
    unittest.main()
