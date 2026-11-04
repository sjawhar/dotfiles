#!/usr/bin/env python3
"""A box cannot kill the host tmux server, or a pane that is not its own, through its relay.

Every agent box is given the host's tmux server so a boxed lead can drive other sessions' panes
(dispatch://INFRA-702). That also gave every box `kill-server`: at 16:53:58Z on 2026-11-04 the
host server died and took every pane on sami-devbox with it, and the relaunches that followed
cost another 74 minutes (dispatch://INFRA-1192).

So the relay no longer carries tmux's protocol: `scripts/agentbox-tmux-guard` answers the box's
socket, refuses the kills that would end another session, and runs everything else with the
host's own tmux client. These tests run the real guard, over a real unix socket, against a
tmux server of their own (`-S <tmpdir>/server -f /dev/null`, never the `dev` or `agents`
servers), and drive it through the real `shims/tmux`, which is what a box runs.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
GUARD = DOTFILES / "scripts" / "agentbox-tmux-guard"
SHIM = DOTFILES / "shims" / "tmux"


def tmux_binary() -> str:
    return shutil.which("tmux") or "/usr/bin/tmux"


@unittest.skipUnless(shutil.which("socat") and shutil.which("tmux"),
                     "needs socat and tmux, as every host that runs boxes has")
class TmuxGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.server = self.root / "server"
        self.guard_sock = self.root / "guard"
        self.tmux = tmux_binary()
        # A server of this test's own, with no user config: ~/.tmux.conf restores the workspace
        # snapshot, which would resurrect this host's real sessions into the test server.
        self.srv("new-session", "-d", "-s", "own", "cat")
        self.srv("new-session", "-d", "-s", "other", "cat")
        self.addCleanup(self.kill_test_server)
        self.own_pane = self.srv("display-message", "-p", "-t", "own", "#{pane_id}").strip()
        self.other_pane = self.srv("display-message", "-p", "-t", "other", "#{pane_id}").strip()
        # The box's own pane is a split of the `own` window, so the test can kill it and still
        # have a window to look at - which is also the real shape: a box owns one pane of a
        # window it shares with nothing else.
        self.box_pane = self.srv("split-window", "-d", "-t", "own", "-P", "-F", "#{pane_id}",
                                 "cat").strip()
        self.relay = subprocess.Popen(
            ["socat", f"UNIX-LISTEN:{self.guard_sock},fork,mode=600,unlink-early",
             f"EXEC:{GUARD},nofork"],
            env={**os.environ,
                 "AGENTBOX_GUARD_BOX": "agentbox-testbox01",
                 "AGENTBOX_GUARD_PANE": self.box_pane,
                 "AGENTBOX_GUARD_SOCK": str(self.server),
                 "AGENTBOX_GUARD_STATE": str(self.state),
                 "AGENTBOX_GUARD_TMUX": self.tmux},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self.stop_relay)
        for _ in range(100):
            if self.guard_sock.is_socket():
                break
            time.sleep(0.05)
        self.assertTrue(self.guard_sock.is_socket(), "the guard's socket never appeared")

    def stop_relay(self) -> None:
        if self.relay.poll() is None:
            self.relay.send_signal(signal.SIGTERM)
            try:
                self.relay.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.relay.kill()

    def kill_test_server(self) -> None:
        subprocess.run([self.tmux, "-S", str(self.server), "kill-server"],
                       capture_output=True, check=False)

    def srv(self, *args: str) -> str:
        """Straight to the test server, as the host would: not through the guard."""
        done = subprocess.run([self.tmux, "-S", str(self.server), "-f", "/dev/null", *args],
                              capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(done.returncode, 0, f"{args}: {done.stderr}")
        return done.stdout

    def box(self, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        """What a box runs: `tmux …` with shims/ first on PATH, inside the box's environment."""
        env = {"HOME": os.environ["HOME"], "PATH": f"{DOTFILES / 'shims'}:/usr/bin:/bin",
               "AGENTBOX_TMUX_GUARD_SOCK": str(self.guard_sock),
               "TMUX_TMPDIR": str(self.root / "boxtmp")}
        return subprocess.run([str(SHIM), *args], capture_output=True, text=True, check=False,
                              timeout=60, env=env, cwd=cwd or str(self.root))

    def server_alive(self) -> bool:
        done = subprocess.run([self.tmux, "-S", str(self.server), "list-sessions"],
                              capture_output=True, text=True, check=False, timeout=30)
        return done.returncode == 0

    def panes(self) -> set[str]:
        return set(self.srv("list-panes", "-a", "-F", "#{pane_id}").split())

    # ── what must be refused ─────────────────────────────────────────────────────────────────

    def test_kill_server_is_refused_and_the_server_lives(self) -> None:
        done = self.box("kill-server")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("refusing `kill-server`", done.stderr)
        self.assertIn("ends EVERY agent pane on this machine", done.stderr)
        self.assertTrue(self.server_alive(), "the guard let kill-server through")

    def test_naming_the_relay_socket_does_not_get_round_the_guard(self) -> None:
        """shims/tmux let `-S <socket> kill-server` past its own check; the guard does not."""
        done = self.box("-S", str(self.guard_sock), "kill-server")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("refusing", done.stderr)
        self.assertTrue(self.server_alive())

    def test_the_allow_kill_server_escape_hatch_does_not_reach_the_host_server(self) -> None:
        env_done = subprocess.run(
            [str(SHIM), "kill-server"], capture_output=True, text=True, check=False, timeout=60,
            env={"HOME": os.environ["HOME"], "PATH": f"{DOTFILES / 'shims'}:/usr/bin:/bin",
                 "TMUX_ALLOW_KILL_SERVER": "1",
                 "AGENTBOX_TMUX_GUARD_SOCK": str(self.guard_sock)})
        self.assertEqual(env_done.returncode, 1, env_done.stdout)
        self.assertIn("refusing `kill-server`", env_done.stderr)
        self.assertTrue(self.server_alive())

    def test_an_unambiguous_prefix_of_kill_server_is_refused(self) -> None:
        done = self.box("kill-serv")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("refusing", done.stderr)
        self.assertTrue(self.server_alive())

    def test_kill_server_chained_after_an_allowed_command_is_refused(self) -> None:
        done = self.box("display-message", "-p", "ok", ";", "kill-server")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("refusing", done.stderr)
        self.assertTrue(self.server_alive())
        self.assertNotIn("ok", done.stdout, "the chain ran before it was judged")

    def test_killing_another_sessions_pane_is_refused(self) -> None:
        done = self.box("kill-pane", "-t", self.other_pane)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("this box does not own", done.stderr)
        self.assertIn(self.other_pane, done.stderr)
        self.assertIn(self.other_pane, self.panes())

    def test_killing_another_session_is_refused(self) -> None:
        done = self.box("kill-session", "-t", "other")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("this box does not own", done.stderr)
        self.assertIn(self.other_pane, self.panes())

    def test_killing_every_other_session_is_refused(self) -> None:
        done = self.box("kill-session", "-a", "-t", "own")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("this box does not own", done.stderr)
        self.assertIn(self.other_pane, self.panes())

    def test_killing_the_window_the_box_shares_with_another_pane_is_refused(self) -> None:
        done = self.box("kill-window", "-t", self.own_pane)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(self.own_pane, done.stderr)
        self.assertIn(self.own_pane, self.panes())
        self.assertIn(self.box_pane, self.panes())

    def test_a_kill_with_no_target_is_refused_rather_than_aimed_at_the_newest_pane(self) -> None:
        done = self.box("kill-pane")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("It names no target", done.stderr)
        self.assertEqual(self.panes(), self.panes() | {self.other_pane, self.box_pane})

    def test_a_real_tmux_binary_pointed_at_the_relay_gets_no_server(self) -> None:
        """The socket speaks the guard's protocol, so the binary cannot bypass the shim."""
        done = subprocess.run([self.tmux, "-S", str(self.guard_sock), "kill-server"],
                              capture_output=True, text=True, check=False, timeout=60)
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue(self.server_alive())

    # ── what a lead must still be able to do ─────────────────────────────────────────────────

    def test_a_lead_steers_another_sessions_pane(self) -> None:
        marker = "HELLO-FROM-A-BOXED-LEAD"
        sent = self.box("send-keys", "-t", self.other_pane, "-l", marker)
        self.assertEqual(sent.returncode, 0, sent.stderr)
        seen = ""
        for _ in range(40):
            got = self.box("capture-pane", "-p", "-t", self.other_pane)
            self.assertEqual(got.returncode, 0, got.stderr)
            seen = got.stdout
            if marker in seen:
                break
            time.sleep(0.1)
        self.assertIn(marker, seen)

    def test_a_lead_reads_and_lists_panes_and_the_server_version(self) -> None:
        listed = self.box("list-panes", "-a", "-F", "#{pane_id} #{session_name}")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn(self.other_pane, listed.stdout)
        version = self.box("display-message", "-p", "#{version}")
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertTrue(version.stdout.strip(), "display-message printed nothing")

    def test_an_argument_holding_a_newline_survives_the_relay(self) -> None:
        done = self.box("display-message", "-p", "one\ntwo")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[:2], ["one", "two"])

    def test_a_capture_pane_start_line_is_not_read_as_a_socket_option(self) -> None:
        done = self.box("capture-pane", "-p", "-S", "-5", "-t", self.other_pane)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_box_may_kill_its_own_pane(self) -> None:
        done = self.box("kill-pane", "-t", self.box_pane)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(self.box_pane, self.panes())
        self.assertIn(self.own_pane, self.panes())
        self.assertTrue(self.server_alive())

    def test_a_box_may_kill_a_window_it_made_itself(self) -> None:
        made = self.box("new-window", "-d", "-t", "own", "-P", "-F", "#{pane_id}", "cat")
        self.assertEqual(made.returncode, 0, made.stderr)
        pane = made.stdout.strip()
        self.assertIn(pane, self.panes())
        self.assertIn(pane, (self.state / "owned-panes").read_text(encoding="utf-8").split())
        killed = self.box("kill-window", "-t", pane)
        self.assertEqual(killed.returncode, 0, killed.stderr)
        self.assertNotIn(pane, self.panes())
        self.assertIn(self.other_pane, self.panes())

    def test_a_refusal_is_logged_for_the_box_health_lead(self) -> None:
        self.box("kill-server")
        log = (self.state / "tmux-guard.log").read_text(encoding="utf-8")
        self.assertIn("agentbox-testbox01", log)
        self.assertIn("REFUSED", log)


if __name__ == "__main__":
    unittest.main()
