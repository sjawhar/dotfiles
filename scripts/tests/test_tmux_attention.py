#!/usr/bin/env python3
"""Behaviour of scripts/tmux-attention: queue file, lock, and pane swapping."""

from __future__ import annotations

import itertools
import json
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "tmux-attention"


def line(pane: str, title: str = "t", server: str = "1") -> str:
    return json.dumps({"pane": pane, "server": server, "session": "s-" + pane, "title": title, "cwd": "/tmp", "at": "2026-01-01T00:00:00Z"})


class QueueFile(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "attention"
        # A drop names no server; the script takes it from TMUX, as inside a session's pane.
        self.env = {**os.environ, "OMP_ATTENTION_DIR": str(self.dir), "TMUX": "/tmp/tmux-test/default,1,0"}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cmd(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(SCRIPT), *args], env=self.env, capture_output=True, text=True, check=True)

    def queue(self) -> list[dict]:
        path = self.dir / "queue.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines() if l]

    def test_push_appends_in_order_and_creates_dir(self) -> None:
        self.run_cmd("push", line("%1"))
        self.run_cmd("push", line("%2"))
        self.assertEqual([q["pane"] for q in self.queue()], ["%1", "%2"])

    def test_push_same_pane_twice_keeps_one_line_at_the_back(self) -> None:
        self.run_cmd("push", line("%1", "old"))
        self.run_cmd("push", line("%2"))
        self.run_cmd("push", line("%1", "new"))
        self.assertEqual([(q["pane"], q["title"]) for q in self.queue()], [("%2", "t"), ("%1", "new")])

    def test_drop_removes_only_that_pane_and_is_idempotent(self) -> None:
        self.run_cmd("push", line("%1"))
        self.run_cmd("push", line("%2"))
        self.run_cmd("drop", "%1")
        self.run_cmd("drop", "%1")
        self.assertEqual([q["pane"] for q in self.queue()], ["%2"])

    def test_list_prints_lines_in_order(self) -> None:
        self.run_cmd("push", line("%1"))
        self.run_cmd("push", line("%2"))
        out = self.run_cmd("list").stdout.splitlines()
        self.assertEqual([json.loads(l)["pane"] for l in out], ["%1", "%2"])

    def test_concurrent_pushes_lose_nothing(self) -> None:
        procs = [subprocess.Popen([str(SCRIPT), "push", line(f"%{i}")], env=self.env) for i in range(12)]
        for p in procs:
            self.assertEqual(p.wait(), 0)
        self.assertEqual(sorted(q["pane"] for q in self.queue()), sorted(f"%{i}" for i in range(12)))

    def test_a_call_without_a_stamp_is_stamped_when_it_starts(self) -> None:
        # omp sessions still on an extension that sends no stamp depend on this.
        before = int(time.time() * 1000)
        self.run_cmd("drop", "%1")
        after = int(time.time() * 1000)
        self.run_cmd("push", line("%1", "earlier"), str(before - 1))
        self.assertEqual(self.queue(), [])
        self.run_cmd("push", line("%1", "later"), str(after + 1))
        self.assertEqual([q["title"] for q in self.queue()], ["later"])

    # omp stamps each push and drop with its event's time; the script orders them by
    # the stamp, whichever process takes the lock first.
    def test_a_push_stamped_at_or_before_its_panes_latest_stamp_is_ignored(self) -> None:
        self.run_cmd("drop", "%1", "2000")
        self.run_cmd("push", line("%1"), "1999")
        self.run_cmd("push", line("%1"), "2000")
        self.assertEqual(self.queue(), [])
        self.run_cmd("push", line("%1"), "2001")
        self.assertEqual([q["pane"] for q in self.queue()], ["%1"])

    def test_a_drop_not_newer_than_the_queued_line_leaves_it(self) -> None:
        # That drop answered an earlier push; the line came from a later one.
        self.run_cmd("push", line("%1"), "2000")
        self.run_cmd("drop", "%1", "1999")
        self.run_cmd("drop", "%1", "2000")
        self.assertEqual([q["pane"] for q in self.queue()], ["%1"])
        self.run_cmd("drop", "%1", "2001")
        self.assertEqual(self.queue(), [])

    def test_every_arrival_order_ends_with_the_latest_event(self) -> None:
        # An ask opens (push@100), closes (drop@150), and the turn ends (push@200).
        events = {
            "push@100": ("push", line("%1", "ask"), "100"),
            "drop@150": ("drop", "%1", "150"),
            "push@200": ("push", line("%1", "turn"), "200"),
        }
        for order in itertools.permutations(events):
            with self.subTest(order=order):
                shutil.rmtree(self.dir, ignore_errors=True)
                for name in order:
                    self.run_cmd(*events[name])
                self.assertEqual([(q["pane"], q["title"]) for q in self.queue()], [("%1", "turn")])

    def test_an_earlier_drop_does_not_lower_the_panes_latest_stamp(self) -> None:
        # A keystroke drop and the shutdown drop can reach the lock in either order.
        self.run_cmd("drop", "%1", "3000")
        self.run_cmd("drop", "%1", "1000")
        self.run_cmd("push", line("%1"), "2000")
        self.assertEqual(self.queue(), [])

    def test_stamps_are_kept_per_server(self) -> None:
        # The same pane id on two tmux servers is two sessions.
        self.run_cmd("push", line("%1", server="1"), "1000")
        self.run_cmd("push", line("%1", server="2"), "1000")
        self.run_cmd("drop", "%1", "2000", "2")
        self.run_cmd("push", line("%1", "later", server="1"), "1500")
        self.assertEqual([(q["server"], q["title"]) for q in self.queue()], [("1", "later")])

    def test_stamps_older_than_ten_minutes_are_forgotten(self) -> None:
        now = int(time.time() * 1000)
        old, recent = now - 11 * 60_000, now - 60_000
        self.run_cmd("drop", "%8", str(old))
        self.run_cmd("drop", "%9", str(recent))
        self.run_cmd("drop", "%1", str(now))
        self.run_cmd("push", line("%8"), str(old - 1))
        self.run_cmd("push", line("%9"), str(recent - 1))
        self.assertEqual([q["pane"] for q in self.queue()], ["%8"])

    def test_a_stamp_that_is_not_a_number_is_refused(self) -> None:
        # A leading zero would make bash read the stamp as octal.
        for args in (("push", line("%1"), "soon"), ("drop", "%1", "-5"), ("push", line("%1"), "08"), ("drop", "%1", "08")):
            with self.subTest(args=args):
                result = subprocess.run([str(SCRIPT), *args], env=self.env, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.queue(), [])

    def test_push_and_drop_refuse_what_is_not_a_pane_id_or_server_pid(self) -> None:
        # tmux issues %<number> and a server pid; anything else would split a stamps record.
        self.run_cmd("drop", "%1", "2000")
        stamps = (self.dir / "stamps").read_text()
        refused = [
            ("push", line("%9 x"), "3000"),
            ("push", line("9"), "3000"),
            ("push", line("%9", server="1 2"), "3000"),
            ("drop", "%9 x", "3000"),
            ("drop", "9", "3000"),
            ("drop", "%9", "3000", "x"),
        ]
        for args in refused:
            with self.subTest(args=args):
                result = subprocess.run([str(SCRIPT), *args], env=self.env, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual((self.dir / "stamps").read_text(), stamps)
        self.assertEqual(self.queue(), [])

    def test_invalid_line_is_removed_and_reported(self) -> None:
        self.dir.mkdir(parents=True)
        (self.dir / "queue.jsonl").write_text(line("%1") + "\nnot json\n" + line("%2") + "\n")
        result = self.run_cmd("list")
        self.assertIn("not json", result.stderr)
        self.assertEqual([q["pane"] for q in self.queue()], ["%1", "%2"])

    def test_push_and_drop_over_a_dirty_queue_succeed(self) -> None:
        self.dir.mkdir(parents=True)
        (self.dir / "queue.jsonl").write_text(line("%1") + "\nnot json\n" + line("%2") + "\n")
        self.run_cmd("push", line("%3"))
        self.run_cmd("drop", "%1")
        self.assertEqual([q["pane"] for q in self.queue()], ["%2", "%3"])

    def test_a_drop_whose_stderr_is_gone_leaves_the_other_lines(self) -> None:
        # A shutdown drop outlives omp, so nothing reads its stderr when it reports an invalid line.
        for sigpipe in (signal.SIG_DFL, signal.SIG_IGN):
            with self.subTest(sigpipe=sigpipe):
                shutil.rmtree(self.dir, ignore_errors=True)
                self.dir.mkdir(parents=True)
                (self.dir / "queue.jsonl").write_text("\n".join([line("%1"), "not json", line("%2"), line("%3")]) + "\n")
                read_end, write_end = os.pipe()
                os.close(read_end)
                subprocess.run(
                    [str(SCRIPT), "drop", "%1", "2000"],
                    env=self.env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=write_end,
                    preexec_fn=lambda sigpipe=sigpipe: signal.signal(signal.SIGPIPE, sigpipe),
                    check=False,
                )
                os.close(write_end)
                self.assertEqual([q["pane"] for q in self.queue()], ["%2", "%3"])

    def test_a_drop_whose_read_fails_leaves_the_queue_as_it_was(self) -> None:
        self.run_cmd("push", line("%1"), "1000")
        self.run_cmd("push", line("%2"), "1000")
        before = (self.dir / "queue.jsonl").read_text()
        broken = Path(self.tmp.name) / "broken-jq"
        broken.mkdir()
        (broken / "jq").write_text("#!/bin/sh\nexit 3\n")
        (broken / "jq").chmod(0o755)
        result = subprocess.run(
            [str(SCRIPT), "drop", "%1", "2000"],
            env={**self.env, "PATH": f"{broken}:{self.env['PATH']}"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.dir / "queue.jsonl").read_text(), before)

    def test_list_on_empty_queue_prints_nothing(self) -> None:
        self.assertEqual(self.run_cmd("list").stdout, "")

    def test_push_rejects_line_without_title(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "push", json.dumps({"pane": "%9", "session": "s"})],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.queue(), [])

    def test_push_rejects_line_without_at(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "push", json.dumps({"pane": "%9", "server": "1", "session": "s", "title": "t", "cwd": "/tmp"})],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.queue(), [])

    def test_push_rejects_line_without_server(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "push", json.dumps({"pane": "%9", "session": "s", "title": "t", "cwd": "/tmp", "at": "2026-01-01T00:00:00Z"})],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.queue(), [])


class PaneSwapping(unittest.TestCase):
    """Runs against a scratch tmux server: window one has three panes, window two has two."""

    # One socket per test: tearDown's kill-server must not race the next setUp's new-session.
    sockets = itertools.count()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "attention"
        self.socket = f"attn-test-{os.getpid()}-{next(self.sockets)}"
        self.tmux = ["tmux", "-L", self.socket]
        subprocess.run([*self.tmux, "-f", "/dev/null", "new-session", "-d", "-s", "w", "-x", "200", "-y", "50", "-n", "one", "sleep 300"], check=True)
        subprocess.run([*self.tmux, "split-window", "-h", "-t", "w:one", "sleep 300"], check=True)
        subprocess.run([*self.tmux, "split-window", "-v", "-t", "w:one", "sleep 300"], check=True)
        subprocess.run([*self.tmux, "new-window", "-t", "w", "-n", "two", "sleep 300"], check=True)
        subprocess.run([*self.tmux, "split-window", "-v", "-t", "w:two", "sleep 300"], check=True)
        self.env = {**os.environ, "OMP_ATTENTION_DIR": str(self.dir), "TMUX_ATTENTION_TMUX": " ".join(self.tmux)}
        self.server = subprocess.run([*self.tmux, "display", "-p", "#{pid}"], capture_output=True, text=True, check=True).stdout.strip()
        self.cockpit = self.panes("w:two")[-1]
        self.dir.mkdir(parents=True)
        (self.dir / "cockpit").write_text(f"{self.server} {self.cockpit}\n")

    def line(self, pane: str, title: str = "t") -> str:
        return line(pane, title, self.server)

    def tearDown(self) -> None:
        subprocess.run([*self.tmux, "kill-server"], check=False)
        self.tmp.cleanup()

    def panes(self, target: str) -> list[str]:
        out = subprocess.run([*self.tmux, "list-panes", "-t", target, "-F", "#{pane_id}"], capture_output=True, text=True, check=True).stdout
        return out.split()

    def geometry(self) -> dict[str, str]:
        """Cell -> pane id, for every pane on the server. Cells are what must not move."""
        out = subprocess.run([*self.tmux, "list-panes", "-a", "-F", "#{window_name}:#{pane_left},#{pane_top},#{pane_width}x#{pane_height} #{pane_id}"], capture_output=True, text=True, check=True).stdout
        return dict(l.split() for l in out.splitlines())

    def run_cmd(self, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(SCRIPT), *args], env=self.env, capture_output=True, text=True, check=check)

    def queue(self) -> list[dict]:
        path = self.dir / "queue.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines() if l] if path.exists() else []

    def cell_of(self, geometry: dict[str, str], pane: str) -> str:
        return next(c for c in geometry if geometry[c] == pane)

    def test_next_swaps_top_line_into_cockpit_cell_and_pops_it(self) -> None:
        agent = self.panes("w:one")[2]
        other = self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(self.line(agent) + "\n" + self.line(other) + "\n")
        before = self.geometry()
        self.assertEqual(self.run_cmd("next").returncode, 0)
        after = self.geometry()
        self.assertEqual(set(before), set(after), "the set of cells must not change")
        self.assertEqual({c for c in before if before[c] != after[c]}, {self.cell_of(before, self.cockpit), self.cell_of(before, agent)})
        self.assertEqual(after[self.cell_of(before, self.cockpit)], agent)
        self.assertEqual(after[self.cell_of(before, agent)], self.cockpit)
        self.assertEqual([q["pane"] for q in self.queue()], [other])
        self.assertEqual(json.loads((self.dir / "visiting").read_text())["pane"], agent)

    def test_next_again_returns_visitor_home_and_brings_the_next(self) -> None:
        first, second = self.panes("w:one")[2], self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(self.line(first) + "\n" + self.line(second) + "\n")
        before = self.geometry()
        self.run_cmd("next", check=True)
        self.run_cmd("next", check=True)
        after = self.geometry()
        self.assertEqual(after[self.cell_of(before, self.cockpit)], second)
        self.assertEqual(after[self.cell_of(before, first)], first, "first went home")
        self.assertEqual(self.queue(), [])
        self.run_cmd("next", check=True)
        self.assertEqual(self.geometry(), before, "everything back where it started")
        self.assertFalse((self.dir / "visiting").exists())

    def test_next_skips_a_pane_that_no_longer_exists(self) -> None:
        gone = self.panes("w:one")[1]
        alive = self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(self.line(gone) + "\n" + self.line(alive) + "\n")
        subprocess.run([*self.tmux, "kill-pane", "-t", gone], check=True)
        before = self.geometry()
        self.run_cmd("next", check=True)
        after = self.geometry()
        self.assertEqual(after[self.cell_of(before, self.cockpit)], alive)
        self.assertEqual(self.queue(), [])

    def test_next_with_empty_queue_changes_nothing(self) -> None:
        before = self.geometry()
        self.assertEqual(self.run_cmd("next").returncode, 0)
        self.assertEqual(self.geometry(), before)

    def test_next_without_cockpit_refuses(self) -> None:
        (self.dir / "cockpit").unlink()
        (self.dir / "queue.jsonl").write_text(self.line(self.panes("w:one")[0]) + "\n")
        before = self.geometry()
        self.assertNotEqual(self.run_cmd("next").returncode, 0)
        self.assertEqual(self.geometry(), before)

    def test_zoomed_cockpit_window_stays_zoomed(self) -> None:
        agent = self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(self.line(agent) + "\n")
        subprocess.run([*self.tmux, "resize-pane", "-Z", "-t", self.cockpit], check=True)
        self.run_cmd("next", check=True)
        zoomed = subprocess.run([*self.tmux, "display", "-p", "-t", "w:two", "#{window_zoomed_flag}"], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(zoomed, "1")

    def test_status_counts_waiting_lines(self) -> None:
        self.assertEqual(self.run_cmd("status", check=True).stdout, "")
        (self.dir / "queue.jsonl").write_text(self.line("%1") + "\n" + self.line("%2") + "\n")
        # The trailing space separates the segment from the next one in status-right.
        self.assertEqual(self.run_cmd("status", check=True).stdout, "⧗ 2 ")

    def test_recover_sends_stale_visitor_home_and_takes_over(self) -> None:
        agent = self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(self.line(agent) + "\n")
        before = self.geometry()
        self.run_cmd("next", check=True)
        # The cockpit process died mid-visit: `cockpit` still names its pane, `visiting` names the agent.
        new_cockpit = self.panes("w:two")[0]
        self.run_cmd("recover", new_cockpit, check=True)
        self.assertEqual(self.geometry(), before)
        self.assertFalse((self.dir / "visiting").exists())
        self.assertEqual((self.dir / "cockpit").read_text().strip(), f"{self.server} {new_cockpit}")

    def test_recover_refuses_while_another_cockpit_pane_lives(self) -> None:
        other = self.panes("w:two")[0]
        self.assertNotEqual(self.run_cmd("recover", other).returncode, 0)
        self.assertEqual((self.dir / "cockpit").read_text().strip(), f"{self.server} {self.cockpit}")

    # The bound only keeps a hang from stalling the suite: a passing test returns as
    # soon as its condition holds, and a loaded machine can take several seconds.
    def wait_for(self, condition, what: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail(f"timed out waiting for {what}")
            time.sleep(0.05)

    def test_two_concurrent_next_calls_displace_exactly_one_pane(self) -> None:
        first, second = self.panes("w:one")[2], self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(self.line(first) + "\n" + self.line(second) + "\n")
        before = self.geometry()
        procs = [subprocess.Popen([str(SCRIPT), "next"], env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
        self.assertEqual([p.wait() for p in procs], [0, 0])
        after = self.geometry()
        self.assertEqual(set(before), set(after), "the set of cells must not change")
        visitor = json.loads((self.dir / "visiting").read_text())["pane"]
        cockpit_cell = self.cell_of(before, self.cockpit)
        self.assertEqual(after[cockpit_cell], visitor, "the pane at the cockpit is the recorded visitor")
        self.assertEqual({c for c in before if before[c] != after[c]}, {cockpit_cell, self.cell_of(before, visitor)})
        self.assertEqual(self.queue(), [], "serialised calls consume both lines")
        self.run_cmd("next", check=True)
        self.assertEqual(self.geometry(), before, "the visitor went home and nobody replaced it")
        self.assertFalse((self.dir / "visiting").exists())

    # The cockpit view watches its directory with inotifywait (inotify-tools),
    # which installers/tmux.sh puts on the host; the agent-box image has none,
    # so inside a box these two tests skip rather than time out.
    @unittest.skipUnless(shutil.which("inotifywait"), "inotifywait (inotify-tools) is not installed; the cockpit view needs it")
    def test_cockpit_exit_sends_visitor_home(self) -> None:
        (self.dir / "cockpit").unlink()
        env = f"OMP_ATTENTION_DIR={shlex.quote(str(self.dir))} TMUX_ATTENTION_TMUX={shlex.quote(' '.join(self.tmux))}"
        cockpit = subprocess.run(
            [*self.tmux, "split-window", "-d", "-P", "-F", "#{pane_id}", "-t", "w:two", f"{env} {shlex.quote(str(SCRIPT))}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        # Keep the cell after the cockpit exits, so geometry can be compared whole.
        subprocess.run([*self.tmux, "set-option", "-p", "-t", cockpit, "remain-on-exit", "on"], check=True)
        self.wait_for(lambda: (self.dir / "cockpit").exists() and (self.dir / "cockpit").read_text().strip() == f"{self.server} {cockpit}", "the cockpit to start")
        agent = self.panes("w:one")[0]
        before = self.geometry()
        self.run_cmd("push", self.line(agent), check=True)
        self.run_cmd("next", check=True)
        self.assertEqual(self.geometry()[self.cell_of(before, cockpit)], agent)
        pane_pid = subprocess.run([*self.tmux, "display", "-p", "-t", cockpit, "#{pane_pid}"], capture_output=True, text=True, check=True).stdout.strip()
        # Ctrl-C: INT to the pane's whole process group, as the terminal would send it.
        os.killpg(int(pane_pid), signal.SIGINT)
        self.wait_for(lambda: not (self.dir / "visiting").exists(), "the visitor to be sent home")
        self.assertEqual(self.geometry(), before)
        # The record goes after the visitor, so poll for it too.
        self.wait_for(lambda: not (self.dir / "cockpit").exists(), "the cockpit record to be removed")

    def start_cockpit_directly(self) -> tuple[str, int]:
        """Launch the cockpit as the pane's own process (no shell in between): pane id and its pid."""
        (self.dir / "cockpit").unlink()
        out = subprocess.run(
            [*self.tmux, "split-window", "-d", "-P", "-F", "#{pane_id} #{pane_pid}", "-t", "w:two",
             "-e", f"OMP_ATTENTION_DIR={self.dir}", "-e", f"TMUX_ATTENTION_TMUX={' '.join(self.tmux)}",
             "--", "bash", str(SCRIPT)],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        cockpit, pid = out[0], int(out[1])
        subprocess.run([*self.tmux, "set-option", "-p", "-t", cockpit, "remain-on-exit", "on"], check=True)
        self.wait_for(lambda: (self.dir / "cockpit").exists() and (self.dir / "cockpit").read_text().strip() == f"{self.server} {cockpit}", "the cockpit to start")
        return cockpit, pid

    def watchers(self) -> list[str]:
        """inotifywait processes watching this test's directory."""
        out = subprocess.run(["pgrep", "-af", "--", f"inotifywait .*{self.dir}"], capture_output=True, text=True, check=False).stdout
        return out.splitlines()

    @unittest.skipUnless(shutil.which("inotifywait"), "inotifywait (inotify-tools) is not installed; the cockpit view needs it")
    def test_directly_launched_cockpit_exits_on_term_and_leaves_no_watcher(self) -> None:
        cockpit, pid = self.start_cockpit_directly()
        self.wait_for(lambda: len(self.watchers()) == 1, "the watcher to start")
        os.kill(pid, signal.SIGTERM)
        self.wait_for(lambda: not (self.dir / "cockpit").exists(), "the cockpit record to be removed")
        self.wait_for(lambda: self.watchers() == [], "the watcher to exit")
        dead = subprocess.run([*self.tmux, "display", "-p", "-t", cockpit, "#{pane_dead}"], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(dead, "1", "the script itself exited, not just its watcher")

    def test_next_with_cockpit_record_from_another_server_changes_nothing(self) -> None:
        # `bind a` runs on every server that loads .tmux.conf: the record may be another server's live cockpit.
        (self.dir / "cockpit").write_text(f"999999 {self.cockpit}\n")
        agent = self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(self.line(agent) + "\n")
        before = self.geometry()
        self.assertNotEqual(self.run_cmd("next").returncode, 0)
        self.assertEqual(self.geometry(), before)
        self.assertEqual([q["pane"] for q in self.queue()], [agent], "a foreign cockpit must not eat a queue line")
        self.assertEqual((self.dir / "cockpit").read_text(), f"999999 {self.cockpit}\n", "another server's record is left alone")
        self.assertFalse((self.dir / "visiting").exists())

    def test_next_with_dead_same_server_cockpit_removes_record(self) -> None:
        gone = self.panes("w:two")[0]
        subprocess.run([*self.tmux, "kill-pane", "-t", gone], check=True)
        (self.dir / "cockpit").write_text(f"{self.server} {gone}\n")
        agent = self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(self.line(agent) + "\n")
        before = self.geometry()
        self.assertNotEqual(self.run_cmd("next").returncode, 0)
        self.assertEqual(self.geometry(), before)
        self.assertEqual([q["pane"] for q in self.queue()], [agent], "a dead cockpit must not eat a queue line")
        self.assertFalse((self.dir / "cockpit").exists(), "the dead record is cleared")

    def test_recover_refuses_while_a_visitor_has_no_cockpit_record(self) -> None:
        # The visitor is away from home and the record naming the pane in its home cell is gone:
        # swapping with the new cockpit could land it in the wrong cell, deleting the record would strand it.
        agent = self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(self.line(agent) + "\n")
        self.run_cmd("next", check=True)
        (self.dir / "cockpit").unlink()
        swapped = self.geometry()
        result = self.run_cmd("recover", self.panes("w:two")[0])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(agent, result.stderr)
        self.assertEqual(self.geometry(), swapped)
        self.assertTrue((self.dir / "visiting").exists(), "the record stays for a human to act on")
        self.assertFalse((self.dir / "cockpit").exists())

    def test_recover_clears_a_visitor_record_whose_pane_is_gone(self) -> None:
        agent = self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(self.line(agent) + "\n")
        self.run_cmd("next", check=True)
        (self.dir / "cockpit").unlink()
        subprocess.run([*self.tmux, "kill-pane", "-t", agent], check=True)
        new_cockpit = self.panes("w:two")[0]
        before = self.geometry()
        self.run_cmd("recover", new_cockpit, check=True)
        self.assertEqual(self.geometry(), before)
        self.assertFalse((self.dir / "visiting").exists(), "nothing is left to restore")
        self.assertEqual((self.dir / "cockpit").read_text().strip(), f"{self.server} {new_cockpit}")

    def test_recover_with_visiting_from_another_server_swaps_nothing(self) -> None:
        # Records left by a previous server: its pane ids are meaningless here, even when they collide with live ones.
        (self.dir / "visiting").write_text(line(self.panes("w:one")[1], server="999999") + "\n")
        (self.dir / "cockpit").write_text(f"999999 {self.cockpit}\n")
        new_cockpit = self.panes("w:two")[0]
        before = self.geometry()
        self.run_cmd("recover", new_cockpit, check=True)
        self.assertEqual(self.geometry(), before)
        self.assertFalse((self.dir / "visiting").exists())
        self.assertEqual((self.dir / "cockpit").read_text().strip(), f"{self.server} {new_cockpit}")

    def test_pop_skips_line_from_another_server(self) -> None:
        foreign, alive = self.panes("w:one")[1], self.panes("w:one")[0]
        (self.dir / "queue.jsonl").write_text(line(foreign, server="999999") + "\n" + self.line(alive) + "\n")
        before = self.geometry()
        self.run_cmd("next", check=True)
        after = self.geometry()
        self.assertEqual(after[self.cell_of(before, self.cockpit)], alive)
        self.assertEqual(after[self.cell_of(before, foreign)], foreign, "a live pane with a foreign-server line is not touched")
        self.assertEqual(self.queue(), [])


if __name__ == "__main__":
    unittest.main()
