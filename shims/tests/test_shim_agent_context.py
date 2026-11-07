#!/usr/bin/env python3
"""The agent test the identity shims share (INFRA-1538).

`shims/az`, `gcloud`, `gh`, `git` and `jj` each hold a copy of `agent_context`. The copies are
kept identical by hand (there is no sourced library in `shims/`), so the first test here compares
them byte for byte; the second drives the function itself over every case, with the stdin of each
case named, so the matrix is proved once for all five copies and each shim's own test file carries
only its own end-to-end case. `shims/secrets` has its own marker loop in POSIX sh, tested in
`test_secrets_shim.py`.

A command that arrives over SSH in a session with no terminal is an agent's: on 2026-11-07 an
agent's `ssh sami-devbox az account show` ran as Sami@Theorem.dev, because the shim saw no
session marker and took the call for a person's. The session decides, not the command's own
stdin: sshd sets `SSH_TTY` for the session it gave a terminal, so `echo x | gh …` in a person's
interactive SSH shell, and git handing a credential helper its request down a pipe, stay the
person's. `AGENT_SHIM_PERSON=1` is the person's escape; it does not turn off the session
markers, which still mean an agent.
"""

from __future__ import annotations

import json
import os
import pty
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SHIMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stdin_cases import SSH_CONNECTION, SSH_PERSON, SSH_TTY, run_with_tty, run_without_tty  # noqa: E402

# shims/tmux holds the same function: it decides what it marks and what it refuses with it.
COPIES = ("az", "gcloud", "gh", "git", "jj", "tmux")
MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION",
           "CLAUDECODE", "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "LEGION_ROLE", "LEGION_TREE",
           "LEGION_CONTROLLER", "LEGION_DAEMON_URL")
ROUTING_INCLUDE = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "include.path",
    "GIT_CONFIG_VALUE_0": "/home/u/.dotfiles/gh-app-routes.gitconfig",
}
BASE_ENV = {"PATH": "/usr/bin:/bin"}


def agent_context_source(shim: str) -> str:
    text = (SHIMS / shim).read_text(encoding="utf-8")
    start = text.index("agent_context() {")
    return text[start : text.index("\n}\n", start) + 3]


class AgentContextCopies(unittest.TestCase):
    def test_every_copy_is_identical(self) -> None:
        first = agent_context_source(COPIES[0])
        self.assertIn("SSH_CONNECTION", first)
        self.assertIn("SSH_TTY", first)
        self.assertIn("AGENT_SHIM_PERSON", first)
        for shim in COPIES[1:]:
            self.assertEqual(agent_context_source(shim), first, f"shims/{shim} has drifted")


class AgentContextCases(unittest.TestCase):
    """Each case: the environment, the stdin, and whether the function says agent (exit 0)."""

    def probe(self, shim: str, env: dict[str, str], tty: bool) -> int:
        run = run_with_tty if tty else run_without_tty
        script = "set -u\n" + agent_context_source(shim) + "\nagent_context\n"
        return run(["bash", "-c", script], env={**BASE_ENV, **env}).returncode

    def assert_all(self, env: dict[str, str], tty: bool, agent: bool) -> None:
        for shim in COPIES:
            with self.subTest(shim=shim, env=sorted(env), tty=tty):
                self.assertEqual(self.probe(shim, env, tty), 0 if agent else 1)

    def test_ssh_with_no_terminal_in_the_session_is_an_agent(self) -> None:
        # Whatever this one command reads on stdin: a pipe into it is not a person.
        self.assert_all({"SSH_CONNECTION": SSH_CONNECTION}, tty=False, agent=True)
        self.assert_all({"SSH_CONNECTION": SSH_CONNECTION}, tty=True, agent=True)

    def test_a_persons_ssh_session_is_a_person_even_with_a_pipe_on_stdin(self) -> None:
        # The P2 case of the review: `echo x | gh …`, and git's credential helper, in a terminal
        # shell the person is sitting at.
        person = {"SSH_CONNECTION": SSH_CONNECTION, "SSH_TTY": SSH_TTY}
        self.assert_all(person, tty=False, agent=False)
        self.assert_all(person, tty=True, agent=False)

    def test_agent_shim_person_is_the_escape(self) -> None:
        self.assert_all(
            {"SSH_CONNECTION": SSH_CONNECTION, "AGENT_SHIM_PERSON": "1"}, tty=False, agent=False
        )

    def test_a_local_shell_is_a_person_with_or_without_a_terminal(self) -> None:
        self.assert_all({}, tty=False, agent=False)
        self.assert_all({}, tty=True, agent=False)

    def test_the_markers_still_mean_an_agent(self) -> None:
        for marker in MARKERS:
            self.assert_all({marker: "x", **SSH_PERSON}, tty=True, agent=True)
            # The escape is for the SSH rule only; a session marker is not a person.
            self.assert_all({marker: "x", "AGENT_SHIM_PERSON": "1"}, tty=True, agent=True)
        self.assert_all({**ROUTING_INCLUDE, **SSH_PERSON}, tty=True, agent=True)
        self.assert_all({**ROUTING_INCLUDE, "AGENT_SHIM_PERSON": "1"}, tty=True, agent=True)

    def test_an_empty_ssh_connection_is_not_an_ssh_command(self) -> None:
        self.assert_all({"SSH_CONNECTION": ""}, tty=False, agent=False)

    def test_an_empty_ssh_tty_is_no_terminal(self) -> None:
        # sshd sets SSH_TTY only for a session it gave one; an empty value is not one.
        self.assert_all({"SSH_CONNECTION": SSH_CONNECTION, "SSH_TTY": ""}, tty=True, agent=True)

    def test_tmux_decides_nothing_by_itself(self) -> None:
        # Round 3 of INFRA-1538 removed the "inside tmux means the person" exemption: a pane
        # takes the SERVER's environment, so an agent's own `tmux new-window` under the person's
        # server produced an unmarked pane that then ran as the person. What marks a pane is the
        # marker shims/tmux passes it; TmuxPanes below drives that through a real server.
        tmux = {"TMUX": "/tmp/tmux-1000/default,42,0"}
        self.assert_all({**tmux, "SSH_CONNECTION": SSH_CONNECTION}, tty=False, agent=True)
        self.assert_all(tmux, tty=False, agent=False)
        for marker in MARKERS:
            self.assert_all({**tmux, marker: "x"}, tty=False, agent=True)
        self.assert_all({**tmux, **ROUTING_INCLUDE}, tty=False, agent=True)

    def test_every_harness_marker_means_an_agent(self) -> None:
        # The set is one list now, shared with shims/secrets: Claude Code, an envoy session and
        # a Legion pane (packages/daemon/internal/runtime/tmux/spawn.go) are agents too.
        for marker in ("CLAUDECODE", "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "LEGION_ROLE", "LEGION_TREE",
                       "LEGION_CONTROLLER", "LEGION_DAEMON_URL"):
            self.assert_all({marker: "1"}, tty=True, agent=True)


TMUX = shutil.which("tmux")
# The line .tmux.conf adds, read from the file so the two cannot drift apart.
UPDATE_ENVIRONMENT = "set -ga update-environment \" SSH_TTY\""


@unittest.skipUnless(TMUX, "tmux is not installed")
class TmuxPanes(unittest.TestCase):
    """What a pane of a real tmux server is taken for (round 3 of INFRA-1538).

    A pane takes the SERVER's environment, never the environment of whoever asked for the pane,
    so nothing a tmux client holds reaches it by itself. Every case here drives a real server
    through `shims/tmux`, the tmux on an agent's PATH, and no case puts a marker in a pane by
    hand: either the shim carries the caller's markers across with tmux's -e, or the pane has
    none.
    """

    ROUTING_INCLUDE_PATH = "/home/u/.dotfiles/gh-app-routes.gitconfig"

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="tmux-panes-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.socket = self.dir / "sock"
        self.probe = self.dir / "probe.sh"
        self.probe.write_text(
            "#!/bin/bash\nset -u\n" + agent_context_source("az") +
            'if agent_context; then verdict=AGENT; else verdict=PERSON; fi\n'
            'printf \'{"verdict":"%s","ssh_connection":"%s","ssh_tty":"%s","tmux":"%s",'
            '"omp":"%s","routing":"%s","shim_agent":"%s","pwd":"%s"}\' '
            '"$verdict" "${SSH_CONNECTION:-}" "${SSH_TTY:-}" "${TMUX:-}" "${OMP_SESSION_ID:-}" '
            '"${GIT_CONFIG_VALUE_0:-}" "${AGENT_SHIM_AGENT:-}" "$PWD" > "$1"\n'
        )
        self.probe.chmod(0o755)
        # shims/tmux hands a shell command to tmux through a file under XDG_RUNTIME_DIR, which
        # must be a directory of this user's own; keep it inside this test.
        self.runtime = self.dir / "run"
        self.runtime.mkdir(mode=0o700)
        # shims/tmux first, as it is on an agent's PATH; it finds the real tmux past itself.
        self.person_env = {
            "PATH": f"{SHIMS}{os.pathsep}{Path(TMUX).parent}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(self.dir), "TERM": "xterm-256color", "TMUX_TMPDIR": str(self.dir),
            "XDG_RUNTIME_DIR": str(self.runtime),
        }
        self.addCleanup(self.kill_server)

    def kill_server(self) -> None:
        subprocess.run([TMUX, "-S", str(self.socket), "kill-server"],
                       env=self.person_env, capture_output=True, timeout=30, check=False)

    def tmux(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        """A tmux command through shims/tmux, the way any caller on PATH reaches it."""
        return subprocess.run([str(SHIMS / "tmux"), "-S", str(self.socket), *args],
                              env=env or self.person_env, capture_output=True, text=True,
                              timeout=30, check=False)

    def agent_env(self, **extra: str) -> dict[str, str]:
        """An agent's own environment: an omp session id and the gh-app routing include."""
        return {**self.person_env, "OMP_SESSION_ID": "01a00000-0000-7000-8000-000000000000",
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "include.path",
                "GIT_CONFIG_VALUE_0": self.ROUTING_INCLUDE_PATH, **extra}

    def start_server(self, env: dict[str, str], config: str = "/dev/null") -> None:
        started = self.tmux("-f", config, "new-session", "-d", "-s", "s0", "sleep 600", env=env)
        self.assertEqual(started.returncode, 0, started.stderr)

    def pane(self, *args: str, env: dict[str, str] | None = None, name: str = "probe",
             around: str = "{probe}") -> dict[str, str]:
        """Run the probe in a new pane made by this tmux command, and read what it saw.

        The pane is told where to write as an ARGUMENT: a variable of this test would not reach
        it, since a pane takes the server's environment and not the caller's — which is the
        whole subject here. `around` puts the probe inside a larger shell command.
        """
        out = self.dir / f"{name}.json"
        probe = f"{shlex.quote(str(self.probe))} {shlex.quote(str(out))}"
        opened = self.tmux(*args, around.format(probe=probe), env=env)
        self.assertEqual(opened.returncode, 0, opened.stderr)
        deadline = time.time() + 20
        while time.time() < deadline and not out.exists():
            time.sleep(0.1)
        self.assertTrue(out.exists(), f"the pane never ran the probe: {opened.stderr}")
        return json.loads(out.read_text())

    def test_a_server_an_ssh_agent_starts_makes_agent_panes(self) -> None:
        # Case (a): an agent whose only evidence is the SSH rule starts a server of its own. The
        # server, and so every pane of it, holds SSH_CONNECTION and no SSH_TTY.
        env = {**self.person_env, "SSH_CONNECTION": SSH_CONNECTION}
        got = self.pane("-f", "/dev/null", "new-session", "-d", "-s", "s0", env=env)
        self.assertTrue(got["tmux"], "the probe did not run inside tmux")
        self.assertEqual(got["ssh_connection"], SSH_CONNECTION, got)
        self.assertEqual(got["ssh_tty"], "", got)
        self.assertEqual(got["verdict"], "AGENT", got)

    def test_an_agents_window_under_the_persons_server_is_an_agent_pane(self) -> None:
        # Case (b): the person's own server, and an agent asks it for a window. The markers are
        # in the CALLER's environment only; shims/tmux is what puts them in the pane.
        self.start_server(self.person_env)
        got = self.pane("new-window", "-t", "s0", env=self.agent_env())
        self.assertEqual(got["omp"], "01a00000-0000-7000-8000-000000000000", got)
        self.assertEqual(got["routing"], self.ROUTING_INCLUDE_PATH, got)
        self.assertEqual(got["verdict"], "AGENT", got)

    def test_a_split_and_the_two_respawns_carry_the_markers_too(self) -> None:
        for name, args in (("split", ("split-window", "-t", "s0")),
                           ("respawn-pane", ("respawn-pane", "-k", "-t", "s0")),
                           ("respawn-window", ("respawn-window", "-k", "-t", "s0"))):
            with self.subTest(command=name):
                # A respawned pane whose probe exits ends the session, and with it the server.
                self.kill_server()
                self.start_server(self.person_env)
                got = self.pane(*args, env=self.agent_env(), name=name)
                self.assertEqual(got["verdict"], "AGENT", got)

    def test_a_later_command_of_a_list_is_marked_as_well_as_the_first(self) -> None:
        # `tmux list-windows -t s0 \; new-window …`: the shim marked only the first command, so
        # the window of the second had no marker (round 4 of INFRA-1538).
        self.start_server(self.person_env)
        got = self.pane("list-windows", "-t", "s0", ";", "new-window", "-d", "-t", "s0",
                        env=self.agent_env())
        self.assertEqual(got["omp"], "01a00000-0000-7000-8000-000000000000", got)
        self.assertEqual(got["verdict"], "AGENT", got)

    def test_run_shell_from_an_agent_runs_a_marked_command(self) -> None:
        # run-shell makes no pane and takes no -e, so its shell command gets an `env` prefix
        # instead. It is marked and not refused: `scripts/agentbox move` starts the box
        # relauncher with `tmux run-shell -b` (round 4 of INFRA-1538).
        self.start_server(self.person_env)
        got = self.pane("run-shell", "-b", "-t", "s0", env=self.agent_env(), name="run-shell")
        self.assertEqual(got["omp"], "01a00000-0000-7000-8000-000000000000", got)
        self.assertEqual(got["routing"], self.ROUTING_INCLUDE_PATH, got)
        self.assertEqual(got["verdict"], "AGENT", got)

    def test_a_persons_run_shell_is_untouched(self) -> None:
        self.start_server(self.person_env)
        got = self.pane("run-shell", "-b", "-t", "s0", env=self.person_env, name="person-run")
        self.assertEqual(got["omp"], "", got)
        self.assertEqual(got["verdict"], "PERSON", got)

    def test_a_shell_command_is_marked_in_every_part_of_itself(self) -> None:
        # `env … <command>` set the markers for the first simple command only, so `true &&
        # <probe>` ran unmarked, and it broke a builtin (`cd … && …` exited 127). The whole
        # original now runs inside a shell of its own (round 5 of INFRA-1538).
        self.start_server(self.person_env)
        for name, around in (("and", "true && {probe}"), ("semicolon", ": ; {probe}"),
                             ("builtin", "cd /tmp && {probe}")):
            with self.subTest(shape=name):
                got = self.pane("run-shell", "-b", "-t", "s0", env=self.agent_env(),
                                name=f"compound-{name}", around=around)
                self.assertEqual(got["omp"], "01a00000-0000-7000-8000-000000000000", got)
                self.assertEqual(got["shim_agent"], "1", got)
                self.assertEqual(got["verdict"], "AGENT", got)
                if name == "builtin":
                    self.assertEqual(got["pwd"], "/tmp", "the shell builtin did not run")

    def test_pipe_pane_marks_its_whole_command_too(self) -> None:
        self.start_server(self.person_env)
        got = self.pane("pipe-pane", "-t", "s0", env=self.agent_env(), name="pipe",
                        around="true && {probe}")
        self.assertEqual(got["verdict"], "AGENT", got)

    def test_a_tmux_format_cannot_break_out_of_the_marked_shell(self) -> None:
        """tmux expands `#{…}` and `#S`, `#T` and the rest in a run-shell argument AFTER this
        shim has quoted it, so a format value holding a quote — a server option, a window title
        any program can set — ended the sh -c word and the rest ran in tmux's own unmarked
        shell (round 6 of INFRA-1538). Every `#` is doubled before quoting; tmux turns `##`
        back into `#`, and nothing is expanded."""
        self.start_server(self.person_env)
        sentinel = self.dir / "escaped.txt"
        out = self.dir / "format.txt"
        # The person's own server option, holding a payload: end the quoted word, run a
        # command of the attacker's, and comment out the remainder.
        payload = f"'; touch {sentinel}; #"
        self.assertEqual(self.tmux("set-option", "-g", "@opt", payload,
                                   env=self.person_env).returncode, 0)
        got = self.tmux("run-shell", f'printf "%s" "#{{@opt}}" > {out}', env=self.agent_env())
        self.assertEqual(got.returncode, 0, got.stderr)
        deadline = time.time() + 20
        while time.time() < deadline and not out.exists():
            time.sleep(0.1)
        self.assertTrue(out.exists(), "the command never ran")
        self.assertFalse(sentinel.exists(), "the format's payload ran")
        self.assertEqual(out.read_text(), "#{@opt}", "the format was expanded")

    def test_an_agents_command_reaches_the_shell_byte_for_byte(self) -> None:
        # The command travels in a file now: doubling every `#` was no transport, since tmux
        # leaves `##` alone before `[` and a boxed caller had both wrappers double it
        # (round 7 of INFRA-1538).
        self.start_server(self.person_env)
        out = self.dir / "hash.txt"
        for name, command, want in (
            ("comment", f'printf "%s" ok > {out} # the rest is a comment', "ok"),
            ("doubled hash", f'printf "%s" "a##b" > {out}', "a##b"),
            ("literal style", f"printf %s '#[fg=red]' > {out}", "#[fg=red]"),
            ("parameter length", f'v=abc; printf %s "${{#v}}" > {out}', "3"),
            ("prefix removal", f'v=a/b/c; printf %s "${{v#*/}}" > {out}', "b/c"),
            ("quotes and newline", f"printf %s \"it's\n\\\"here\\\"\" > {out}", "it's\n\"here\""),
        ):
            with self.subTest(shape=name):
                out.unlink(missing_ok=True)
                got = self.tmux("run-shell", command, env=self.agent_env())
                self.assertEqual(got.returncode, 0, got.stderr)
                deadline = time.time() + 20
                while time.time() < deadline and not out.exists():
                    time.sleep(0.1)
                self.assertTrue(out.exists(), "the command never ran")
                self.assertEqual(out.read_text(), want)

    def test_the_command_file_removes_itself_and_is_private(self) -> None:
        # `rm -f -- "$0"` is its first line, so nothing is left behind and nothing can swap it
        # between the write and the run; `$0` is that path, which is the one visible difference
        # from `sh -c`.
        self.start_server(self.person_env)
        out = self.dir / "self.txt"
        got = self.tmux("run-shell", f'printf "%s" "$0" > {out}', env=self.agent_env())
        self.assertEqual(got.returncode, 0, got.stderr)
        deadline = time.time() + 20
        while time.time() < deadline and not out.exists():
            time.sleep(0.1)
        self.assertTrue(out.exists(), "the command never ran")
        path = Path(out.read_text())
        self.assertEqual(path.parent, self.runtime / "tmux-shim", path)
        self.assertRegex(path.name, r"^agent-[A-Za-z0-9._-]+$")
        self.assertEqual(oct(path.parent.stat().st_mode)[-3:], "700")
        for _ in range(100):
            if not path.exists():
                break
            time.sleep(0.1)
        self.assertFalse(path.exists(), "the command file was left behind")

    def test_a_command_directory_that_is_not_this_users_own_is_refused(self) -> None:
        # `mkdir -p` plus `chmod` would follow a symlink someone else put there, and a name
        # under a shared /tmp is another user's to guess (round 8 of INFRA-1538).
        self.start_server(self.person_env)
        elsewhere = self.dir / "elsewhere"
        elsewhere.mkdir()
        (self.runtime / "tmux-shim").symlink_to(elsewhere)
        got = self.tmux("run-shell", "true", env=self.agent_env())
        self.assertEqual(got.returncode, 1, got.stdout)
        self.assertIn("REFUSED in an agent session", got.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [], "a command file was written through it")

    def test_without_xdg_runtime_dir_the_user_runtime_directory_is_used(self) -> None:
        # A cron job or a system unit has /run/user/<uid> though its environment does not name
        # it (round 9 of INFRA-1538).
        runtime = Path(f"/run/user/{os.getuid()}")
        if not runtime.is_dir() or runtime.stat().st_uid != os.getuid():
            self.skipTest("this machine has no /run/user/<uid> of this user's own")
        self.start_server(self.person_env)
        out = self.dir / "fallback.txt"
        env = {k: v for k, v in self.agent_env().items() if k != "XDG_RUNTIME_DIR"}
        got = self.tmux("run-shell", f'printf "%s" "$0" > {out}', env=env)
        self.assertEqual(got.returncode, 0, got.stderr)
        deadline = time.time() + 20
        while time.time() < deadline and not out.exists():
            time.sleep(0.1)
        self.assertTrue(out.exists(), "the command never ran")
        self.assertEqual(Path(out.read_text()).parent, runtime / "tmux-shim")

    def test_a_queued_command_survives_a_later_failure_in_the_same_call(self) -> None:
        # Nothing but the file's own first line removes it. Removing it by age, or because the
        # tmux call exited non-zero, would take the file of a command tmux has only QUEUED —
        # `run-shell -b -d 1` here — and lose the work (round 9 of INFRA-1538).
        self.start_server(self.person_env)
        out = self.dir / "queued.txt"
        got = self.tmux("run-shell", "-b", "-d", "1", f'printf "%s" queued > {out}',
                        ";", "pipe-pane", "-t", "%999", "cat > /dev/null",
                        env=self.agent_env())
        self.assertNotEqual(got.returncode, 0, "the second command was meant to fail")
        deadline = time.time() + 20
        while time.time() < deadline and not out.exists():
            time.sleep(0.1)
        self.assertTrue(out.exists(), "the queued command's file was taken from under it")
        self.assertEqual(out.read_text(), "queued")

    def test_an_ssh_only_agent_marks_the_pane_it_opens_under_the_persons_server(self) -> None:
        # The round-5 case: no session marker at all, an agent only because the command came
        # over SSH with no terminal in its session. AGENT_SHIM_AGENT=1 carries that into the
        # pane, which every agent_context copy reads as a marker, so the pane refuses the
        # person's --account exactly as a marked session does (test_gcloud_shim_routing.py
        # drives that marker through the account guard).
        self.start_server(self.person_env)
        env = {**self.person_env, "SSH_CONNECTION": SSH_CONNECTION}
        got = self.pane("new-window", "-t", "s0", env=env, name="ssh-only")
        self.assertEqual(got["omp"], "", "the caller had no session marker to pass")
        self.assertEqual(got["ssh_connection"], "", "the pane is not itself an ssh command")
        self.assertEqual(got["shim_agent"], "1", got)
        self.assertEqual(got["verdict"], "AGENT", got)

    def test_a_nested_tmux_command_is_refused_for_an_agent_and_runs_for_a_person(self) -> None:
        self.start_server(self.person_env)
        for command in (["if-shell", "true", "display-message ok"],
                        ["bind-key", "X", "display-message ok"],
                        ["set-hook", "-g", "pane-died", "display-message ok"],
                        # command-prompt and display-menu need an attached client, so the
                        # person's half of those two is in TmuxMarkedArgv instead.
                        ["run-shell", "-C", "display-message ok"]):
            with self.subTest(command=command[0]):
                refused = self.tmux(*command, env=self.agent_env())
                self.assertEqual(refused.returncode, 1, refused.stdout)
                self.assertIn("tmux shim: REFUSED in an agent session", refused.stderr)
                allowed = self.tmux(*command, env=self.person_env)
                self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_the_persons_own_window_is_still_the_persons(self) -> None:
        self.start_server(self.person_env)
        got = self.pane("new-window", "-t", "s0", env=self.person_env)
        self.assertEqual(got["omp"], "", got)
        self.assertEqual(got["verdict"], "PERSON", got)

    def reattach(self, config: str, ssh_tty: str) -> dict[str, str]:
        """Attach to the running server from an 'ssh session', then open a window in it."""
        client_env = {**self.person_env, "SSH_CONNECTION": SSH_CONNECTION, "SSH_TTY": ssh_tty}
        master, slave = pty.openpty()
        attach = subprocess.Popen([TMUX, "-S", str(self.socket), "attach", "-t", "s0"],
                                  stdin=slave, stdout=slave, stderr=slave, env=client_env)
        try:
            deadline = time.time() + 20
            while time.time() < deadline and not self.tmux("list-clients").stdout.strip():
                time.sleep(0.1)
            self.assertTrue(self.tmux("list-clients").stdout.strip(), "no client attached")
            return self.pane("new-window", "-t", "s0", name=Path(config).name)
        finally:
            attach.terminate()
            attach.wait(timeout=20)
            os.close(slave)
            os.close(master)

    def test_the_dotfiles_tmux_config_keeps_a_reattaching_person_the_person(self) -> None:
        # tmux's default update-environment carries SSH_CONNECTION into the windows a reattached
        # session opens afterwards and NOT SSH_TTY, which read as a non-interactive ssh command.
        # `.tmux.conf` adds SSH_TTY to that list; with it, the window stays the person's.
        self.assertIn(UPDATE_ENVIRONMENT, (SHIMS.parent / ".tmux.conf").read_text(encoding="utf-8"))
        config = self.dir / "with-ssh-tty.conf"
        config.write_text(UPDATE_ENVIRONMENT + "\n")
        self.start_server(self.person_env, config=str(config))
        got = self.reattach(str(config), SSH_TTY)
        self.assertEqual(got["ssh_tty"], SSH_TTY, got)
        self.assertEqual(got["verdict"], "PERSON", got)

    def test_without_that_line_a_reattached_window_is_taken_for_an_agent(self) -> None:
        # The deliberate cost of dropping the tmux exemption: a server started before that line
        # existed carries SSH_CONNECTION into its new windows and not SSH_TTY, so they read as a
        # non-interactive ssh command. Safe direction, and AGENT_SHIM_PERSON=1 is the escape.
        config = self.dir / "plain.conf"
        config.write_text("")
        self.start_server(self.person_env, config=str(config))
        got = self.reattach(str(config), SSH_TTY)
        self.assertEqual(got["ssh_tty"], "", got)
        self.assertEqual(got["verdict"], "AGENT", got)


class TmuxMarkedArgv(unittest.TestCase):
    """Which tmux commands shims/tmux marks, read off the argv it hands the real tmux.

    `TmuxPanes` above drives the ones that make a pane end to end. The forms a test cannot
    easily stand up — `display-popup`, which needs an attached client, and `pipe-pane`, which
    needs a pane producing output — are checked here, against a tmux that records its argv.
    """

    MARKER = "01a00000-0000-7000-8000-000000000000"

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="tmux-argv-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        stub = self.dir / "bin"
        stub.mkdir()
        (stub / "tmux").write_text("#!/bin/bash\nprintf '%s\\n' \"$@\"\n")
        (stub / "tmux").chmod(0o755)
        # The shim writes the command file under XDG_RUNTIME_DIR; keep it inside this test.
        self.env = {"PATH": f"{SHIMS}{os.pathsep}{stub}{os.pathsep}/usr/bin{os.pathsep}/bin",
                    "HOME": str(self.dir), "TMUX_TMPDIR": str(self.dir),
                    "XDG_RUNTIME_DIR": str(self.dir)}

    def argv(self, *args: str, agent: bool = True) -> list[str]:
        env = {**self.env, "OMP_SESSION_ID": self.MARKER} if agent else dict(self.env)
        done = subprocess.run([str(SHIMS / "tmux"), *args], env=env, capture_output=True,
                              text=True, timeout=30, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.splitlines()

    DURABLE = "AGENT_SHIM_AGENT=1"

    def test_every_pane_making_command_gets_the_markers_as_e(self) -> None:
        for command in ("new-session", "new", "new-window", "neww", "split-window", "splitw",
                        "respawn-pane", "respawnp", "respawn-window", "respawnw",
                        "display-popup", "popup"):
            with self.subTest(command=command):
                got = self.argv(command, "-d", "-t", "s0")
                self.assertEqual(got[:5], [command, "-e", f"OMP_SESSION_ID={self.MARKER}",
                                           "-e", self.DURABLE], got)

    def test_an_agent_with_no_marker_of_its_own_still_marks_the_pane(self) -> None:
        # The SSH rule alone: AGENT_SHIM_AGENT=1 is the provenance that then travels (round 5).
        env = {**self.env, "SSH_CONNECTION": SSH_CONNECTION}
        done = subprocess.run([str(SHIMS / "tmux"), "new-window", "-d"], env=env,
                              capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), ["new-window", "-e", self.DURABLE, "-d"])

    def wrapped(self, got: list[str]) -> str:
        """The command the shim put in a file, read back from the argv it handed tmux."""
        prefix = f"env OMP_SESSION_ID={self.MARKER} {self.DURABLE} /bin/sh "
        wrapper = next((word for word in got if word.startswith(prefix)), "")
        self.assertTrue(wrapper, got)
        path = Path(wrapper[len(prefix):])
        self.assertRegex(str(path), r"^[A-Za-z0-9._/-]+$")
        text = path.read_text()
        path.unlink()  # the stub tmux never ran it, so its own `rm` never happened
        self.assertTrue(text.startswith('rm -f -- "$0"\n'), text)
        return text[len('rm -f -- "$0"\n'):].rstrip("\n")

    def test_every_shell_running_command_travels_in_a_marked_file(self) -> None:
        # Not text in the tmux argument: tmux expands formats in that, and doubling every `#`
        # was no transport either (rounds 6 and 7). The file holds the original bytes.
        for command in ("run-shell", "run", "pipe-pane", "pipep"):
            for shape in ("echo hello", "true && echo hello", "cd /tmp && echo 'it worked'",
                          "printf %s '#[fg=red]'", 'v=abc; printf %s "${#v}"'):
                with self.subTest(command=command, shape=shape):
                    got = self.argv(command, "-t", "s0", shape)
                    self.assertEqual(self.wrapped(got), shape)
                    self.assertNotIn("-e", got, "a command that takes no -e was given one")

    def test_a_boxed_caller_leaves_the_shell_command_to_the_relay(self) -> None:
        # Both wrappers would otherwise rewrite one command, and two rewrites broke `${#v}`
        # (round 7). The relay wraps it, on the host where the tmux server is.
        sock = self.dir / "relay.sock"
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(sock))
        self.addCleanup(server.close)
        env = {**self.env, "OMP_SESSION_ID": self.MARKER, "AGENTBOX_TMUX_GUARD_SOCK": str(sock)}
        done = subprocess.run([str(SHIMS / "tmux"), "-S", str(sock), "run-shell", "echo hello"],
                              env=env, capture_output=True, text=True, timeout=30, check=False)
        # The relay is a bare socket here, so the call gets no answer; what matters is the argv
        # the shim sent, which the shim's own stub cannot show. The file is the proof: none.
        self.assertFalse(list(self.dir.glob("**/agent-*")), "the shim wrapped a relayed command")
        del done

    def test_a_command_that_hands_the_server_a_tmux_command_is_refused(self) -> None:
        for command in (["if-shell", "true", "new-window"], ["if", "true", "new-window"],
                        ["bind-key", "X", "new-window"], ["bind", "X", "new-window"],
                        ["set-hook", "-g", "pane-died", "new-window"],
                        ["confirm-before", "kill-pane"], ["command-prompt", "new-window"],
                        ["display-menu", "x", "x", "new-window"], ["menu", "x", "x", "new-window"],
                        ["run-shell", "-C", "new-window"], ["run-shell", "-bC", "new-window"]):
            with self.subTest(command=" ".join(command[:2])):
                done = subprocess.run([str(SHIMS / "tmux"), *command],
                                      env={**self.env, "OMP_SESSION_ID": self.MARKER},
                                      capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("tmux shim: REFUSED in an agent session", done.stderr)
                self.assertEqual(done.stdout, "", "the command reached tmux")
                # The same command from a person's shell is none of this shim's business.
                self.assertEqual(self.argv(*command, agent=False), command)

    def test_a_command_that_neither_makes_a_pane_nor_runs_a_shell_is_untouched(self) -> None:
        for command in (["send-keys", "-t", "%1", "hello"], ["list-panes", "-a"],
                        ["display-message", "-p", "#{pane_id}"], ["kill-window", "-t", "s0"]):
            with self.subTest(command=command[0]):
                self.assertEqual(self.argv(*command), command)

    def test_a_persons_command_is_never_marked(self) -> None:
        for command in (["new-window", "-d"], ["run-shell", "-b", "echo hello"]):
            with self.subTest(command=command[0]):
                self.assertEqual(self.argv(*command, agent=False), command)

    def test_a_list_marks_each_command_in_it(self) -> None:
        got = self.argv("list-windows", ";", "new-window", "-d", ";", "run-shell", "echo hi")
        self.assertEqual(got.count(f"OMP_SESSION_ID={self.MARKER}"), 1, got)
        self.assertEqual(self.wrapped(got), "echo hi")
        self.assertEqual(got[0], "list-windows", got)

    def test_a_command_word_that_ends_the_command_keeps_the_list_intact(self) -> None:
        # `tmux new-window\; list-panes`: the separator rides on the command word itself.
        got = self.argv("new-window;", "list-panes")
        self.assertEqual(got, ["new-window", "-e", f"OMP_SESSION_ID={self.MARKER}", "-e",
                               self.DURABLE, ";", "list-panes"], got)


if __name__ == "__main__":
    unittest.main()
