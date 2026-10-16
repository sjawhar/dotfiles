#!/usr/bin/env python3
"""tmux shim: kill-server is refused on the default server and nowhere else, and a tmux that
hands the call back to the shim is skipped instead of looping."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "tmux"

# What mise's shim does when mise has no tmux version for the directory (a relocated HOME hides
# mise's global config): exec the first tmux on PATH outside its own directory, which is
# shims/tmux when the shims come first.
HAND_BACK = """#!/bin/bash
own="${BASH_SOURCE[0]%/*}"
IFS=':'
for d in $PATH; do
    [[ "$d" -ef "$own" ]] && continue
    [[ -x "$d/tmux" ]] && exec "$d/tmux" "$@"
done
echo "hand-back: no tmux on PATH" >&2
exit 127
"""


class TmuxGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.stub_dir = root / "bin"
        self.stub_dir.mkdir()
        self.argv_log = root / "argv"
        stub = self.stub_dir / "tmux"
        stub.write_text(
            f'#!/bin/bash\nprintf "%s\\n" "$@" > {self.argv_log}\n', encoding="utf-8"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.hand_back_dir = root / "hand-back"
        self.hand_back_dir.mkdir()
        hand_back = self.hand_back_dir / "tmux"
        hand_back.write_text(HAND_BACK, encoding="utf-8")
        hand_back.chmod(0o755)
        # bash for the shim's `#!/usr/bin/env bash` on a PATH without /usr/bin, whose tmux would
        # answer a call the test expects nothing to answer.
        self.bash_dir = root / "bash-only"
        self.bash_dir.mkdir()
        (self.bash_dir / "bash").symlink_to("/bin/bash")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_shim(self, *args: str, tmux: str | None = None, allow: bool = False,
                 path: list[Path] | None = None, marker: str | None = None):
        env = {
            "PATH": ":".join(map(str, path)) if path else f"{SHIM.parent}:{self.stub_dir}:/usr/bin:/bin",
            "HOME": self.temp_dir.name,
            "DOTFILES_DIR": str(DOTFILES),
        }
        if tmux is not None:
            env["TMUX"] = tmux
        if allow:
            env["TMUX_ALLOW_KILL_SERVER"] = "1"
        if marker is not None:
            env["DOTFILES_TMUX_SHIM"] = marker
        # A session of its own, so a shim that loops fails the test at the timeout and leaves no
        # process behind.
        proc = subprocess.Popen([str(SHIM), *args], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

    def assert_forwarded(self, result, *argv: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.argv_log.read_text().split("\n")[:-1], list(argv))

    def assert_refused(self, result) -> None:
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing `kill-server`", result.stderr)
        self.assertFalse(self.argv_log.exists(), "real tmux must not run")

    def test_ordinary_commands_pass_through_verbatim(self) -> None:
        self.assert_forwarded(self.run_shim("list-panes", "-a", "-F", "#{pane_id}"),
                              "list-panes", "-a", "-F", "#{pane_id}")

    def test_kill_server_outside_tmux_targets_default_and_is_refused(self) -> None:
        self.assert_refused(self.run_shim("kill-server"))

    def test_kill_server_inside_default_server_is_refused(self) -> None:
        self.assert_refused(self.run_shim("kill-server", tmux="/tmp/tmux-1000/default,123,0"))

    def test_command_prefix_and_chained_form_are_refused(self) -> None:
        self.assert_refused(self.run_shim("kill-serv"))
        self.assert_refused(self.run_shim("new-session", "-d", ";", "kill-server"))

    def test_explicit_socket_is_allowed(self) -> None:
        self.assert_forwarded(self.run_shim("-L", "scratch", "kill-server"), "-L", "scratch", "kill-server")
        self.assert_forwarded(self.run_shim("-S/tmp/x.sock", "kill-server"), "-S/tmp/x.sock", "kill-server")

    def test_inside_a_non_default_server_is_allowed(self) -> None:
        self.assert_forwarded(self.run_shim("kill-server", tmux="/tmp/tmux-1000/rtest,123,0"), "kill-server")

    def test_override_is_allowed(self) -> None:
        self.assert_forwarded(self.run_shim("kill-server", allow=True), "kill-server")

    def test_kill_session_and_lookalike_words_are_allowed(self) -> None:
        self.assert_forwarded(self.run_shim("kill-session", "-t", "legion-smoke1"),
                              "kill-session", "-t", "legion-smoke1")
        self.assert_forwarded(self.run_shim("send-keys", "tmux kill-server", "Enter"),
                              "send-keys", "tmux kill-server", "Enter")

    def test_capture_pane_S_is_not_a_socket_option(self) -> None:
        # -S after the command word is capture-pane's start line, not a server socket.
        self.assert_refused(self.run_shim("capture-pane", "-p", "-S", "-200", ";", "kill-server"))

    def test_a_tmux_that_hands_the_call_back_is_skipped_for_the_tmux_past_it(self) -> None:
        result = self.run_shim("display-message", "-p", "#{client_termfeatures}",
                               path=[SHIM.parent, self.hand_back_dir, self.stub_dir, self.bash_dir])
        self.assert_forwarded(result, "display-message", "-p", "#{client_termfeatures}")

    def test_when_every_tmux_hands_the_call_back_the_shim_fails_instead_of_looping(self) -> None:
        result = self.run_shim("display-message", "-p", "#{client_termfeatures}",
                               path=[SHIM.parent, self.hand_back_dir, self.bash_dir])
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn(f"{self.hand_back_dir / 'tmux'} handed the call back", result.stderr)

    def test_a_marker_from_another_pid_skips_nothing(self) -> None:
        # A server the shim started keeps the marker in its global environment, so every pane's
        # tmux call inherits it under another pid; pid 1 is never the shim.
        result = self.run_shim("list-panes", path=[SHIM.parent, self.stub_dir, self.bash_dir],
                               marker=f"1 {self.stub_dir / 'tmux'}")
        self.assert_forwarded(result, "list-panes")


if __name__ == "__main__":
    unittest.main()
