#!/usr/bin/env python3
"""How shims/knives finds the knives it execs: a local build past it on PATH, else the pin.

Technique: a fake mise data dir holding a stale install (the version an old session's PATH
names) and the pinned one, and a `mise` stub answering `mise which -C / knives --tool
github:sjawhar/knives` with the pinned binary's path, as mise 2026.8.10 does. The stub reads
its stdin, so a shim that let mise inherit a hook's payload would hand knives an empty one.
Every hop from the shim to knives is an exec, so a resolution that leads back into a wrapper
spins in one pid until the timeout here kills it.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "knives"
LOOP_TIMEOUT = 10

MISE_WHICH = r"""
cat > /dev/null
[[ "$*" == "which -C / knives --tool github:sjawhar/knives" ]] || { echo "unexpected mise $*" >&2; exit 99; }
if [[ -n "${STUB_PIN_MISSING:-}" ]]; then
    echo "mise ERROR github:sjawhar/knives@2.0.0 is not installed" >&2
    exit 1
fi
echo "${STUB_PINNED:-${MISE_DATA_DIR:?mise was given no data dir}/installs/github-sjawhar-knives/2.0.0/bin/knives}"
"""

# A knives build. `gh` without the depth marker re-enters through the shim in this pid, as a
# direct `knives gh` does through shims/gh; `--nested` starts knives again as a child process.
KNIVES = r"""
if [[ "${{1:-}}" == gh && -z "${{STUB_GH_DEPTH:-}}" ]]; then
    STUB_GH_DEPTH=1 exec "$STUB_SHIM" "$@"
fi
if [[ "${{1:-}}" == --nested ]]; then
    echo "NESTED $("$STUB_SHIM" --version)"
    exit 0
fi
echo "{label} knives $*"
if [[ ! -t 0 ]]; then
    echo "stdin: $(cat)"
fi
"""


def write_stub(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class KnivesShim(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.stub_dir = self.root / "bin"
        write_stub(self.stub_dir, "mise", MISE_WHICH)
        self.mise_data = self.root / ".mise"
        installs = self.mise_data / "installs" / "github-sjawhar-knives"
        self.stale_dir = installs / "1.0.0" / "bin"
        write_stub(self.stale_dir, "knives", KNIVES.format(label="STALE"))
        write_stub(installs / "2.0.0" / "bin", "knives", KNIVES.format(label="PINNED"))
        self.env = {
            "HOME": str(self.root),
            "MISE_DATA_DIR": str(self.mise_data),
            "STUB_SHIM": str(SHIM),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_shim(
        self,
        *path_dirs: Path,
        args: tuple[str, ...] = ("--version",),
        stdin: str = "",
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Runs the shim with PATH = stub dir, `path_dirs` (SHIM.parent where given), system dirs."""
        path = ":".join([str(self.stub_dir), *map(str, path_dirs), "/usr/bin", "/bin"])
        try:
            return subprocess.run(
                [str(SHIM), *args],
                input=stdin,
                capture_output=True,
                text=True,
                env={**self.env, "PATH": path, **(extra_env or {})},
                check=False,
                timeout=LOOP_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            pass
        self.fail(f"shims/knives was still running after {LOOP_TIMEOUT}s: it re-entered itself")

    def test_a_stale_install_dir_on_path_still_reaches_the_pin(self) -> None:
        """An old session's PATH: the shims dir, then the install dir pinned when it started."""
        result = self.run_shim(SHIM.parent, self.stale_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PINNED knives --version\nstdin: \n")

    def test_a_caller_without_mise_data_dir_gets_the_pin_from_home_mise(self) -> None:
        """A hook harness or unit environment without .bashrc's MISE_DATA_DIR: mise's own
        default data dir is not ~/.mise, so the shim names it for mise as for its own scan."""
        del self.env["MISE_DATA_DIR"]
        result = self.run_shim(SHIM.parent, self.stale_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PINNED knives --version\nstdin: \n")

    def test_a_path_without_mise_gets_the_pin_through_the_dotfiles_mise(self) -> None:
        """A unit, harness or git credential helper whose PATH has the shims dir but not
        ~/.dotfiles/bin, where mise lives: the shim runs that mise by its path."""
        path = ":".join([str(SHIM.parent), str(self.stale_dir), "/usr/bin", "/bin"])
        result = subprocess.run(
            [str(SHIM), "--version"],
            input="",
            capture_output=True,
            text=True,
            env={**self.env, "DOTFILES_DIR": str(self.root), "PATH": path},
            check=False,
            timeout=LOOP_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PINNED knives --version\nstdin: \n")

    def test_arguments_and_stdin_reach_knives_untouched(self) -> None:
        payload = '{"hook_event_name":"SessionStart","session_id":"s 1"}'
        result = self.run_shim(
            SHIM.parent, args=("hook", "claude-code", "two words", ""), stdin=payload
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"PINNED knives hook claude-code two words \nstdin: {payload}\n")

    def test_a_local_build_past_the_shim_wins_over_the_pin(self) -> None:
        local = self.root / "local-build"
        write_stub(local, "knives", KNIVES.format(label="LOCAL"))
        result = self.run_shim(SHIM.parent, local)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "LOCAL knives --version\nstdin: \n")

    def test_a_knives_ahead_of_the_shim_on_path_already_had_its_turn(self) -> None:
        ahead = self.root / "ahead"
        write_stub(ahead, "knives", KNIVES.format(label="AHEAD"))
        result = self.run_shim(ahead, SHIM.parent)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PINNED knives --version\nstdin: \n")

    def test_a_knives_wrapper_past_the_shim_is_not_a_local_build(self) -> None:
        other_checkout = self.root / "other-checkout" / "shims"
        other_checkout.mkdir(parents=True)
        shutil.copy2(SHIM, other_checkout / "knives")
        alias = self.root / "alias"
        alias.mkdir()
        (alias / "knives").symlink_to(SHIM)
        for name, directory in (
            ("another checkout's copy of the shim", other_checkout),
            ("a symlink to this shim", alias),
        ):
            with self.subTest(wrapper=name):
                result = self.run_shim(SHIM.parent, directory)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "PINNED knives --version\nstdin: \n")

    def test_knives_gh_re_entering_through_its_gh_shim_runs(self) -> None:
        """`knives gh` execs shims/gh, which execs `knives gh` again: two entries in one pid."""
        result = self.run_shim(SHIM.parent, args=("gh", "pr", "view"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PINNED knives gh pr view\nstdin: \n")

    def test_a_pin_that_resolves_back_to_the_shim_fails_loudly_instead_of_looping(self) -> None:
        result = self.run_shim(SHIM.parent, extra_env={"STUB_PINNED": str(SHIM)})
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertIn("refusing to loop", result.stderr)
        self.assertIn(f"({SHIM})", result.stderr)

    def test_knives_launched_from_inside_knives_starts(self) -> None:
        """The re-entry guard is per process: a child of knives running knives is new."""
        result = self.run_shim(SHIM.parent, args=("--nested",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "NESTED PINNED knives --version\nstdin: \n")

    def test_a_pin_mise_has_not_installed_fails_loudly(self) -> None:
        result = self.run_shim(SHIM.parent, self.stale_dir, extra_env={"STUB_PIN_MISSING": "1"})
        self.assertEqual(result.returncode, 127, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertIn("is not installed", result.stderr)
        self.assertIn("mise did not resolve the pinned github:sjawhar/knives", result.stderr)


if __name__ == "__main__":
    unittest.main()
