#!/usr/bin/env python3
"""installers/knives.sh stops when the statements it migrated cannot be pushed.

The installer's own comment: "a failure stops the install with knives' message".
For an existing knives user, `knives ledger migrate` writes the statements and
hands off a detached sweep; the installer's next `knives ledger sweep` can find
that sweep holding the lock, answer `busy` and exit 0, so a push that fails is
seen by nobody. These tests pin the interleaving that hides it: before the
installer's `knives ledger sweep` starts, the `knives` on its PATH takes the
ledger lock itself and holds it a moment, as a running sweep does, so that sweep
always finds the lock held (it happens on its own in about two installs of five).

Runs against the knives in KNIVES_BIN, else the one on PATH, and is skipped when
that knives has no `knives ledger` (any release before the shared ledger).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "knives.sh"
LIB = DOTFILES / "installers" / "lib.sh"
LEDGER_URL = "https://github.com/trajectory-labs-pbc/knives-ledger"
HOSTNAME = "installer-test"
# The installer names a machine `<hostname -s>-<first 8 hex of /etc/machine-id>`.
MACHINE = f"{HOSTNAME}-{Path('/etc/machine-id').read_text(encoding='utf-8')[:8]}"

# Stands in for `knives`. Before `ledger sweep` runs, a background holder takes
# the ledger lock, as a running sweep does, and keeps it for HOLD_SECONDS; the
# shim execs the real binary only once the holder signals that it holds the lock,
# so the sweep always finds it held. If the handed-off sweep still has the lock,
# the holder waits for it first. With KNIVES_SHIM_SWEEP_DIES set, `ledger sweep`
# instead exits 101 with nothing on stdout, as a panicking knives does.
# Everything else runs the real binary at once.
HOLD_SECONDS = 3
KNIVES_SHIM = rf"""
if [ "${{1:-}}" = ledger ] && [ "${{2:-}}" = sweep ] && [ -n "${{KNIVES_SHIM_SWEEP_DIES:-}}" ]; then
    echo "thread 'main' panicked (test shim)" >&2
    exit 101
fi
if [ "${{1:-}}" = ledger ] && [ "${{2:-}}" = sweep ]; then
    lock="$HOME/.config/knives/ledger.lock"
    ready="$HOME/.knives-shim-holds-lock"
    rm -f "$ready"
    ( exec 9>>"$lock"; flock 9; : > "$ready"; sleep {HOLD_SECONDS} ) &
    for _ in $(seq 1 600); do [ -e "$ready" ] && break; sleep 0.05; done
    [ -e "$ready" ] || {{ echo "test shim: never got the ledger lock" >&2; exit 97; }}
fi
exec "$KNIVES_BIN" "$@"
"""


def write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class KnivesInstallerMigratedPush(unittest.TestCase):
    def setUp(self) -> None:
        knives = os.environ.get("KNIVES_BIN") or shutil.which("knives") or ""
        has_ledger = bool(knives) and subprocess.run(
            [knives, "ledger", "--help"], capture_output=True, check=False
        ).returncode == 0
        if not has_ledger:
            self.skipTest("needs a knives with `knives ledger`; set KNIVES_BIN")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dotfiles = self.root / "dotfiles"
        self.home = self.root / "home"
        self.config = self.home / ".config" / "knives"
        self.remote = self.root / "ledger.git"
        stubs = self.root / "bin"
        for d in (self.dotfiles / "installers", self.dotfiles / "knives", self.config, stubs):
            d.mkdir(parents=True)
        for source in (INSTALLER, LIB):
            (self.dotfiles / "installers" / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        (self.dotfiles / "knives" / "repos.toml").write_text(
            '[repos.smoke]\nupstream = "https://github.com/sjawhar/legion-smoke"\n'
            'origin = "https://github.com/sjawhar/legion-smoke-installer-test"\n'
            'ledger = "trajectory-labs-pbc/knives-ledger"\n',
            encoding="utf-8",
        )
        # An existing knives user: one statement an older knives kept in state.json.
        (self.config / "state.json").write_text('{"tracked_pulls": {"smoke/feat/x": 544}}', encoding="utf-8")
        (self.home / ".gitconfig").write_text(f'[url "{self.remote}"]\n\tinsteadOf = {LEDGER_URL}\n', encoding="utf-8")
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        write_executable(stubs / "hostname", f"echo {HOSTNAME}")
        write_executable(stubs / "knives", KNIVES_SHIM)
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{stubs}:/usr/local/bin:/usr/bin:/bin",
            "KNIVES_BIN": knives,
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def refuse_pushes(self) -> None:
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text('#!/bin/sh\necho "push refused by the test" >&2\nexit 1\n', encoding="utf-8")
        hook.chmod(0o755)

    def install(self) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["bash", str(self.dotfiles / "installers" / "knives.sh")],
            capture_output=True, text=True, env=self.env, check=False,
        )
        # Let the handed-off sweep finish before anything is counted.
        lock = self.config / "ledger.lock"
        for _ in range(600):
            if subprocess.run(["flock", "-n", str(lock), "true"], check=False).returncode == 0:
                break
            time.sleep(0.1)
        return result

    def refs_on_remote(self) -> list[str]:
        out = subprocess.run(
            ["git", "--git-dir", str(self.remote), "for-each-ref", "--format=%(refname)", "refs/knives/"],
            capture_output=True, text=True, check=True,
        ).stdout
        return out.split()

    def statements_left(self) -> int:
        state = json.loads((self.config / "state.json").read_text(encoding="utf-8"))
        return len(state.get("tracked_pulls", {}))

    def test_a_refused_push_of_the_migrated_statements_stops_the_install(self) -> None:
        # Given: an existing knives user, and a ledger remote that refuses every push.
        self.refuse_pushes()

        # When: the installer joins the machine and migrates its statements.
        result = self.install()

        # Then: the statement left state.json for the ledger but never reached the
        # remote, and the installer says so rather than succeeding.
        self.assertEqual(self.statements_left(), 0, result.stdout)
        self.assertEqual(self.refs_on_remote(), [], result.stdout)
        self.assertNotEqual(
            result.returncode, 0,
            "the installer exited 0 though its migrated statement could not be pushed:\n"
            + result.stdout + result.stderr,
        )

    def test_control_an_accepted_push_carries_the_migrated_statements(self) -> None:
        # Given: an existing knives user, and a ledger remote that accepts pushes.
        # When: the installer runs, under the same interleaving.
        result = self.install()

        # Then: it succeeds, and the statement is on the remote, under this machine's ref.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The installer's own sweep found the handed-off one holding the lock and
        # waited for it, rather than leaving at once and hiding a failed push.
        self.assertIn('"waited":', result.stdout, "the interleaving under test did not happen")
        self.assertEqual(self.statements_left(), 0, result.stdout)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout)

    def test_a_new_machine_that_cannot_reach_the_ledger_yet_still_installs(self) -> None:
        # Given: a machine with no knives history, whose git cannot reach the
        # ledger repository (no GitHub login yet).
        (self.config / "state.json").unlink()
        (self.home / ".gitconfig").write_text(
            f'[url "{self.root / "unreachable.git"}"]\n\tinsteadOf = {LEDGER_URL}\n', encoding="utf-8"
        )

        # When: the installer joins it.
        result = self.install()

        # Then: the sweep fails, but nothing written here is stranded, so the
        # install goes on and says how to sync once git can reach GitHub.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"unsent":0', result.stdout.replace(" ", ""), result.stdout)
        self.assertIn("gh auth login", result.stderr)
        self.assertTrue((self.config / "ledger-repositories" / "trajectory-labs-pbc--knives-ledger.git").is_dir())

    def test_a_sweep_that_dies_without_output_stops_the_install(self) -> None:
        # Given: a new machine, and a sweep that crashes before printing anything,
        # so nothing says how much it left unsent.
        (self.config / "state.json").unlink()
        self.env["KNIVES_SHIM_SWEEP_DIES"] = "1"

        # When: the installer joins it.
        result = self.install()

        # Then: an uncounted backlog is treated as stranded, and the install stops.
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unsent: -1", result.stderr)

    def test_two_spellings_of_one_ledger_make_one_git_directory(self) -> None:
        # Given: two forks naming the same ledger repository in different case,
        # one with a .git suffix.
        (self.config / "state.json").unlink()
        with (self.dotfiles / "knives" / "repos.toml").open("a", encoding="utf-8") as registry:
            registry.write(
                '\n[repos.other]\nupstream = "https://github.com/sjawhar/legion-smoke-other"\n'
                'origin = "https://github.com/sjawhar/legion-smoke-other-installer-test"\n'
                'ledger = "Trajectory-Labs-PBC/knives-ledger.git"\n'
            )

        # When: the installer joins the machine.
        result = self.install()

        # Then: knives reads them as one repository, and so does the installer.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            sorted(p.name for p in (self.config / "ledger-repositories").iterdir()),
            ["trajectory-labs-pbc--knives-ledger.git"],
            result.stdout + result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
