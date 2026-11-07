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
        # A runtime directory of this test's own, so the command files the guard writes for a
        # box's run-shell stay inside it instead of reaching this user's /run/user/<uid>.
        self.runtime = self.root / "run"
        self.runtime.mkdir(mode=0o700)
        self.commands = self.runtime / "agentbox-tmux-guard-commands"
        self.relay = subprocess.Popen(
            ["socat", f"UNIX-LISTEN:{self.guard_sock},fork,mode=600,unlink-early",
             f"EXEC:{GUARD},nofork"],
            env={**os.environ,
                 "XDG_RUNTIME_DIR": str(self.runtime),
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

    def box(self, *args: str, cwd: str | None = None,
            **extra: str) -> subprocess.CompletedProcess[str]:
        """What a box runs: `tmux …` with shims/ first on PATH, inside the box's environment."""
        env = {"HOME": os.environ["HOME"], "PATH": f"{DOTFILES / 'shims'}:/usr/bin:/bin",
               "AGENTBOX_TMUX_GUARD_SOCK": str(self.guard_sock),
               "TMUX_TMPDIR": str(self.root / "boxtmp"), **extra}
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

    def test_a_command_the_box_starts_carries_the_boxs_session_marker(self) -> None:
        """A host pane or command a box starts is an agent's, and must say so to ~/.dotfiles/shims.

        A new pane takes the host SERVER's environment, not the box's, so a box's `new-window`
        made a host pane with no session marker at all and every identity shim in it read the
        person's own login (dispatch://INFRA-1538, rounds 3 and 4). The relay adds the marker it
        knows for certain, the box, whatever shim the box runs: with `-e` for the commands that
        make a pane, and with an `env` prefix for run-shell and the rest, which take no -e.
        """
        out = self.root / "pane-env.txt"
        # printenv, not "$AGENTBOX_SESSION": the shell expands its own variables before `env`
        # sets them, so only a program that reads the environment it is given sees the marker.
        write = f"printenv AGENTBOX_SESSION > {out}"
        for command, args in (
            ("new-window", ("new-window", "-d")),
            ("split-window", ("split-window", "-d", "-t", self.box_pane)),
            ("respawn-window", ("respawn-window", "-k", "-t", self.other_pane)),
            # run-shell makes no pane: `scripts/agentbox move` starts the box relauncher with it.
            ("run-shell", ("run-shell", "-b", "-t", self.box_pane)),
        ):
            with self.subTest(command=command):
                out.unlink(missing_ok=True)
                done = self.box(*args, f"{write}; sleep 5")
                self.assertEqual(done.returncode, 0, done.stderr)
                for _ in range(100):
                    if out.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(out.exists(), "the command never ran")
                self.assertEqual(out.read_text().strip(), "agentbox-testbox01")

    def test_a_shell_command_the_box_runs_is_marked_in_every_part_of_itself(self) -> None:
        """`env <marker> <command>` set the marker for the first simple command only, and broke
        a builtin; the original runs whole, inside a shell of its own (round 5)."""
        out = self.root / "compound.txt"
        for shape in ("true && printenv AGENTBOX_SESSION > {out}",
                      ": ; printenv AGENTBOX_SESSION > {out}",
                      "cd /tmp && printenv AGENTBOX_SESSION > {out}"):
            with self.subTest(shape=shape.split()[0]):
                out.unlink(missing_ok=True)
                done = self.box("run-shell", "-b", "-t", self.box_pane, shape.format(out=out))
                self.assertEqual(done.returncode, 0, done.stderr)
                for _ in range(100):
                    if out.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(out.exists(), "the command never ran")
                self.assertEqual(out.read_text().strip(), "agentbox-testbox01")

    # A boxed caller's command passes through the box's shims/tmux AND through this relay.
    # Exactly one of them may rewrite it, and it is this one, on the machine the tmux server
    # runs on. While both doubled every `#`, a marked box's `${#v}` arrived as `${##v}` and
    # read 0, and tmux leaves `##` alone before `[`, so a literal `#[fg=red]` arrived doubled
    # (round 7 of dispatch://INFRA-1538). The command now travels in a file of its own.
    MARKED = {"OMP_SESSION_ID": "01a00000-0000-7000-8000-000000000000"}

    def ran(self, command: str, out: Path, **extra: str) -> str:
        out.unlink(missing_ok=True)
        done = self.box("run-shell", "-b", "-t", self.box_pane, command, **extra)
        self.assertEqual(done.returncode, 0, done.stderr)
        for _ in range(100):
            if out.exists():
                break
            time.sleep(0.1)
        self.assertTrue(out.exists(), f"the command never ran: {done.stderr}")
        return out.read_text()

    def test_a_boxed_agents_command_reaches_the_shell_byte_for_byte(self) -> None:
        out = self.root / "bytes.txt"
        for label, extra in (("unmarked box", {}), ("marked box", self.MARKED)):
            for name, command, want in (
                ("parameter length", f'v=abc; printf %s "${{#v}}" > {out}', "3"),
                ("prefix removal", f'v=a/b/c; printf %s "${{v#*/}}" > {out}', "b/c"),
                ("literal style", f"printf %s '#[fg=red]' > {out}", "#[fg=red]"),
                ("doubled hash", f"printf %s 'a##b' > {out}", "a##b"),
            ):
                with self.subTest(caller=label, shape=name):
                    self.assertEqual(self.ran(command, out, **extra), want)

    def test_a_tmux_format_cannot_break_out_of_the_marked_shell(self) -> None:
        """tmux expands `#{…}` and `#S`, `#T` and the rest in a run-shell argument AFTER the
        relay has quoted it, so a format value holding a quote — a server option, a window
        title any program can set — ended the quoted word and the rest ran in tmux's own
        unmarked shell (round 6 of dispatch://INFRA-1538). The command travels in a file now,
        so tmux reads nothing in it at all."""
        sentinel = self.root / "escaped.txt"
        out = self.root / "format.txt"
        self.srv("set-option", "-g", "@opt", f"'; touch {sentinel}; #")
        for label, extra in (("unmarked box", {}), ("marked box", self.MARKED)):
            with self.subTest(caller=label):
                sentinel.unlink(missing_ok=True)
                got = self.ran(f'printf "%s" "#{{@opt}}" > {out}', out, **extra)
                self.assertFalse(sentinel.exists(), "the format's payload ran")
                self.assertEqual(got, "#{@opt}", "the format was expanded")

    def test_the_relay_marks_a_boxed_agents_shell_command(self) -> None:
        # The relay cannot see the box's environment, so it sets the two markers it knows.
        out = self.root / "markers.txt"
        for label, extra in (("unmarked box", {}), ("marked box", self.MARKED)):
            with self.subTest(caller=label):
                got = self.ran(f'printf "%s %s" "$AGENTBOX_SESSION" "$AGENT_SHIM_AGENT" > {out}',
                               out, **extra)
                self.assertEqual(got, "agentbox-testbox01 1")

    def test_a_command_directory_that_is_not_this_users_own_is_refused(self) -> None:
        # `makedirs(exist_ok=True)` plus `chmod` would follow a symlink someone else put there
        # (round 8 of dispatch://INFRA-1538).
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.commands.parent.mkdir(parents=True, exist_ok=True)
        self.commands.symlink_to(elsewhere)
        done = self.box("run-shell", "-b", "-t", self.box_pane, "true")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("not a directory of this user's own", done.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [], "a command file was written through it")

    def test_a_queued_command_survives_a_later_failure_in_the_same_call(self) -> None:
        """Nothing but the file's own first line removes it. Removing it by age, or because the
        tmux call exited non-zero, would take the file of a command tmux has only QUEUED —
        `run-shell -b -d 1` here — and lose the work (round 9 of dispatch://INFRA-1538)."""
        out = self.root / "queued.txt"
        done = self.box("run-shell", "-b", "-d", "1", "-t", self.box_pane,
                        f'printf "%s" queued > {out}',
                        ";", "pipe-pane", "-t", "%999", "cat > /dev/null")
        self.assertNotEqual(done.returncode, 0, "the second command was meant to fail")
        for _ in range(100):
            if out.exists():
                break
            time.sleep(0.1)
        self.assertTrue(out.exists(), "the queued command's file was taken from under it")
        self.assertEqual(out.read_text(), "queued")

    def test_a_command_that_hands_the_server_a_tmux_command_is_refused(self) -> None:
        """if-shell runs its branch in the server, where no marker of the box's follows it, and
        a binding or a hook a box installs outlives the box (round 5 of INFRA-1538)."""
        for command in (["if-shell", "true", "new-window"],
                        ["bind-key", "X", "new-window"],
                        ["set-hook", "-g", "pane-died", "new-window"],
                        ["confirm-before", "kill-pane"],
                        ["command-prompt", "new-window"],
                        ["display-menu", "x", "x", "new-window"],
                        ["run-shell", "-C", "new-window"]):
            with self.subTest(command=command[0]):
                before = self.panes()
                done = self.box(*command)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("refusing `tmux", done.stderr)
                self.assertEqual(self.panes(), before, "the refused command still made a pane")

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
