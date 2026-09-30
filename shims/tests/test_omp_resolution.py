#!/usr/bin/env python3
"""How shims/omp finds the omp it execs: a local build on PATH, else the pinned release via mise.

Technique: a fake mise data dir holding the "release" omp, and a `mise` stub that resolves the
command the way `mise x` does (mise 2026.8.10, src/cli/exec.rs): on PATH, with the tool dir it
adds ahead of the PATH it was given, while a tool dir already on that PATH keeps its place. No
agent-secrets helper unit exists under the fake HOME, so scripts/agent-secrets-session execs
the argv unregistered. Every hop from the shim to omp is an exec, so a resolution that leads
back into a wrapper spins in one pid until the timeout here kills it.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "omp"
LOOP_TIMEOUT = 10

# `mise x`, as mise 2026.8.10 runs it: the command is resolved with the tool dir it adds ahead
# of the PATH it was given (a tool dir already there keeps its place). What the CHILD's PATH
# holds depends on whether the PATH mise was handed carries mise's own shims dir: with it (every
# PATH .bashrc builds, the normal case, modelled here) the caller entries ahead of that dir stay
# first, so anything the child looks up by name finds the shims dir's omp; without it the tool
# dirs come first. Measured.
MISE_X = r"""
[[ "$1" == x && "$3" == -- ]] || { echo "unexpected mise $*" >&2; exit 99; }
tool="$MISE_DATA_DIR/installs/github-sjawhar-oh-my-pi/1.0.0/bin"
case ":$PATH:" in
    *":$tool:"*) lookup="$PATH" child="$PATH" ;;
    *) lookup="$tool:$PATH" child="$PATH:$tool" ;;
esac
program="$(PATH="$lookup" command -v "$4")"
PATH="$child" exec "$program" "${@:5}"
"""

# The release omp; with --nested it launches omp again as a child, as a session's tool call can.
RELEASE_OMP = r"""
if [[ "${1:-}" == --nested ]]; then
    echo "NESTED $("$STUB_SHIM" --version)"
else
    echo "RELEASE omp $*"
fi
"""


def write_stub(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class OmpResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        self.stub_dir = self.root / "bin"
        write_stub(self.stub_dir, "mise", MISE_X)
        self.mise_data = self.root / "mise"
        self.release_dir = self.mise_data / "installs" / "github-sjawhar-oh-my-pi" / "1.0.0" / "bin"
        write_stub(self.release_dir, "omp", RELEASE_OMP)
        self.env = {
            "DOTFILES_DIR": str(DOTFILES),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "MISE_DATA_DIR": str(self.mise_data),
            "STUB_SHIM": str(SHIM),
            # A seed file already named: the shim never reaches for the real one.
            "NATS_NKEY_SEED_FILE": "/dev/null",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_shim(
        self,
        *path_dirs: Path,
        args: tuple[str, ...] = ("--version",),
        command: Path = SHIM,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        path = ":".join([str(self.stub_dir), *map(str, path_dirs), "/usr/bin", "/bin"])
        try:
            return subprocess.run(
                [str(command), *args],
                capture_output=True,
                text=True,
                env={**self.env, "PATH": path, **(extra_env or {})},
                check=False,
                timeout=LOOP_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            pass
        self.fail(f"shims/omp was still running after {LOOP_TIMEOUT}s: it re-entered itself")

    def test_a_mise_tool_dir_already_on_path_still_reaches_the_release(self) -> None:
        """An inherited PATH naming the install dir, without mise's own record of adding it."""
        result = self.run_shim(self.release_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "RELEASE omp --version\n")

    def test_an_omp_wrapper_on_path_is_not_a_local_build(self) -> None:
        other_checkout = self.root / "other-checkout" / "shims"
        other_checkout.mkdir(parents=True)
        shutil.copy2(SHIM, other_checkout / "omp")
        alias = self.root / "alias"
        alias.mkdir()
        (alias / "omp").symlink_to(SHIM)
        for name, directory in (
            ("another checkout's copy of the shim", other_checkout),
            ("a symlink to this shim", alias),
        ):
            with self.subTest(wrapper=name):
                result = self.run_shim(directory)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "RELEASE omp --version\n")

    def test_a_local_build_on_path_wins_over_the_release(self) -> None:
        local = self.root / "local-build"
        write_stub(local, "omp", 'echo "LOCAL omp $*"')
        result = self.run_shim(local)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "LOCAL omp --version\n")

    def test_re_entry_fails_loudly_instead_of_looping(self) -> None:
        """Any resolution that leads back into the shim stops at the second entry."""
        write_stub(self.stub_dir, "mise", 'exec "$STUB_SHIM" "${@:5}"')
        result = self.run_shim()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertIn("refusing to loop", result.stderr)
        self.assertIn("mise x github:sjawhar/oh-my-pi -- omp", result.stderr)

    def test_a_same_pid_relaunch_is_refused_only_inside_the_loop_window(self) -> None:
        """omp's /restart can exec the shim in its own pid; only a fast re-entry is a loop."""
        # The session's omp, exec'ing `omp` in place with the marker it inherited.
        restart = write_stub(
            self.root / "session",
            "omp",
            'export DOTFILES_OMP_SHIM="$$ $STUB_MARKER_TAIL"\nexec "$STUB_SHIM" "$@"',
        )
        now = int(time.time())
        target = "mise x github:sjawhar/oh-my-pi -- omp"
        for name, tail, expected_rc in (
            ("marker written just now", f"{now} {target}", 1),
            ("marker written a minute ago", f"{now - 60} {target}", 0),
            ("marker with no time (7bd6eb92's format)", target, 0),
        ):
            with self.subTest(marker=name):
                result = self.run_shim(command=restart, extra_env={"STUB_MARKER_TAIL": tail})
                self.assertEqual(result.returncode, expected_rc, result.stderr)
                if expected_rc:
                    self.assertEqual(result.stdout, "")
                    self.assertIn("refusing to loop", result.stderr)
                else:
                    self.assertEqual(result.stdout, "RELEASE omp --version\n")

    def test_omp_launched_from_inside_a_session_starts(self) -> None:
        """The re-entry guard is per process: a child of omp launching omp is a new session."""
        result = self.run_shim(args=("--nested",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "NESTED RELEASE omp --version\n")


if __name__ == "__main__":
    unittest.main()
