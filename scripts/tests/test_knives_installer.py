#!/usr/bin/env python3
"""installers/knives.sh stops when the statements it migrated cannot be pushed.

The tests also read the message the installer prints when `knives ledger migrate`
fails: one or two tests for each outcome of exit 3, and one for another status.

In knives v5.0.0, migrate starts no sweep in the background. It sends the
ledger entries that it wrote, and then it exits. Before the installer's
`knives ledger sweep` starts, the `knives` on its PATH (the test shim) takes
the ledger lock and holds it for HOLD_SECONDS. So that sweep always waits for
the lock.

Runs against the knives in KNIVES_BIN, else the pinned one mise resolves (never the
shims/knives wrapper), and is skipped when that knives has no `knives ledger`. Two more
tests pin the link to the registry's private part (repos.local.toml from the .secrets clone).
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
# the ledger lock, as a running sweep does, and keeps it for HOLD_SECONDS. The
# shim execs the real binary only once the holder signals that it holds the lock,
# so the sweep always finds it held. With KNIVES_SHIM_SWEEP_DIES set, `ledger sweep`
# instead exits 101 with nothing on stdout, as a panicking knives does.
# With KNIVES_SHIM_MIGRATE_DIES set, `ledger migrate` does the same.
# Everything else runs the real binary at once.
HOLD_SECONDS = 3
KNIVES_SHIM = rf"""
if [ "${{1:-}}" = ledger ] && [ "${{2:-}}" = sweep ] && [ -n "${{KNIVES_SHIM_SWEEP_DIES:-}}" ]; then
    echo "thread 'main' panicked (test shim)" >&2
    exit 101
fi
if [ "${{1:-}}" = ledger ] && [ "${{2:-}}" = migrate ] && [ -n "${{KNIVES_SHIM_MIGRATE_DIES:-}}" ]; then
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


def real_knives() -> str:
    """The knives binary itself, never a wrapper. On an agent session's PATH `knives` is
    shims/knives, which finds its binary through PATH again; with the test's stub first on that
    PATH the stub execs the shim, the shim finds the stub, and its loop guard refuses the run. So
    resolve the pin the way the shim does, through mise, unless KNIVES_BIN names one."""
    if os.environ.get("KNIVES_BIN"):
        return os.environ["KNIVES_BIN"]
    mise = shutil.which("mise") or str(DOTFILES / "bin" / "mise")
    found = subprocess.run(
        [mise, "which", "-C", "/", "knives", "--tool", "github:sjawhar/knives"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
    )
    return found.stdout.strip() if found.returncode == 0 else ""


class KnivesInstallerMigratedPush(unittest.TestCase):
    def setUp(self) -> None:
        knives = real_knives()
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
        # Wait until the ledger lock is free before the test counts anything. The shim's
        # background holder can keep it after the installer exits, when the sweep did not wait.
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
        # remote, and the installer stops with migrate's status and says what to run.
        self.assert_migrate_failed(result, "did not reach the ledger")
        self.assertIn("`knives ledger sweep` sends them", result.stderr, result.stdout + result.stderr)
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [], result.stdout + result.stderr)

        # When: the installer runs again, and the remote still refuses every push.
        result = self.install()

        # Then: the sweep finds the entry that only this machine holds, and the
        # installer says so rather than succeeding.
        self.assertNotEqual(
            result.returncode, 0,
            "the installer exited 0 though its migrated statement could not be pushed:\n"
            + result.stdout + result.stderr,
        )
        self.assertIn("unsent: 1", result.stderr, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [], result.stdout + result.stderr)

        # When: the remote accepts pushes again, and the installer runs again.
        (self.remote / "hooks" / "pre-receive").unlink()
        result = self.install()

        # Then: it succeeds, and the statement is on the remote, under this machine's ref.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout + result.stderr)

    def test_control_an_accepted_push_carries_the_migrated_statements(self) -> None:
        # Given: an existing knives user, and a ledger remote that accepts pushes.
        # When: the installer runs, under the same interleaving.
        result = self.install()

        # Then: it succeeds, and the statement is on the remote, under this machine's ref.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The installer's own sweep found that the shim's holder had the ledger lock,
        # and waited for it. So the interleaving under test happened.
        self.assertIn('"waited":', result.stderr, "the interleaving under test did not happen")
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout + result.stderr)

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
        self.assertIn('"unsent":0', result.stderr.replace(" ", ""), result.stdout + result.stderr)
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

    def assert_migrate_failed(self, result: subprocess.CompletedProcess[str], knives_text: str) -> None:
        """The installer stopped with migrate's status 3, and its message is the last three lines
        of stderr. A line that knives printed before the message holds knives_text."""
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        # lib.sh sets DOTFILES_DIR with `cd ... && pwd`, and that keeps each symlink in the path.
        # So the message names self.dotfiles as it is, not its resolved path.
        dotfiles = self.dotfiles
        lines = result.stderr.splitlines()
        # A failure prints both lists whole, so it shows the message the installer printed.
        self.maxDiff = None
        self.assertEqual(
            lines[-3:],
            [
                "knives: `knives ledger migrate` exited 3.",
                "knives: the lines it printed above say what went wrong. Fix each problem they name"
                f" that needs a fix, then run: bash {dotfiles}/installers/knives.sh",
                f"knives: if a line above asks for a change to repos.toml, make it in {dotfiles}/knives/repos.toml:"
                " at each run this installer replaces ~/.config/knives/repos.toml with a render of that file.",
            ],
            result.stdout + result.stderr,
        )
        self.assertTrue(
            any(knives_text in line for line in lines[:-3]),
            f"no line before the installer's message holds {knives_text!r}:\n" + result.stdout + result.stderr,
        )

    def test_a_migrate_that_cannot_pull_the_ledger_keeps_the_statements_and_says_what_to_run(self) -> None:
        # Given: an existing knives user whose git cannot reach the ledger repository.
        (self.home / ".gitconfig").write_text(
            f'[url "{self.root / "unreachable.git"}"]\n\tinsteadOf = {LEDGER_URL}\n', encoding="utf-8"
        )

        # When: the installer joins the machine and migrates its statements.
        result = self.install()

        # Then: the statement stays in state.json, and the installer stops with
        # migrate's status and says what to run.
        self.assert_migrate_failed(result, "could not be pulled")
        self.assertEqual(self.statements_left(), 1, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [], result.stdout + result.stderr)

        # When: git can reach the ledger repository again, and the installer runs again.
        (self.home / ".gitconfig").write_text(f'[url "{self.remote}"]\n\tinsteadOf = {LEDGER_URL}\n', encoding="utf-8")
        result = self.install()

        # Then: it succeeds, and the statement is on the remote, under this machine's ref.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout + result.stderr)

    def test_a_statement_of_a_fork_the_registry_lost_is_fixed_in_the_dotfiles_registry(self) -> None:
        # Given: an existing knives user with a statement of a fork that repos.toml no longer names.
        (self.config / "state.json").write_text('{"tracked_pulls": {"gone/feat/x": 7}}', encoding="utf-8")

        # When: the installer joins the machine and migrates its statements.
        result = self.install()

        # Then: the statement stays in state.json, and the installer stops with
        # migrate's status and says what to run.
        self.assert_migrate_failed(result, "no fork in repos.toml is named gone")
        self.assertEqual(self.statements_left(), 1, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [], result.stdout + result.stderr)

        # When: the registry file that the message names gets the fork again, and the
        # installer runs again.
        with (self.dotfiles / "knives" / "repos.toml").open("a", encoding="utf-8") as registry:
            registry.write(
                '\n[repos.gone]\nupstream = "https://github.com/sjawhar/legion-smoke-gone"\n'
                'origin = "https://github.com/sjawhar/legion-smoke-gone-installer-test"\n'
                f'ledger = "{LEDGER_URL.removeprefix("https://github.com/")}"\n'
            )
        result = self.install()

        # Then: it succeeds, and the statement is on the remote, under this machine's ref.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout + result.stderr)

    def test_a_sightings_file_migrate_cannot_read_stops_the_install_after_the_drop(self) -> None:
        # Given: an existing knives user whose seen.json is not a sightings file.
        (self.config / "seen.json").write_text("not json", encoding="utf-8")

        # When: the installer joins the machine and migrates its statements.
        result = self.install()

        # Then: the statement left state.json and is on the remote. But migrate stops at
        # seen.json, and the installer stops with migrate's status and says what to run.
        self.assert_migrate_failed(result, "workspace sightings not renamed")
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout + result.stderr)

        # When: the user removes seen.json, and the installer runs again.
        (self.config / "seen.json").unlink()
        result = self.install()

        # Then: it succeeds, and the statement stays on the remote, under this machine's ref.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout + result.stderr)

    def test_a_statement_the_ledger_already_states_otherwise_needs_only_a_second_run(self) -> None:
        # Given: a first install puts the statement on the ledger, and then state.json
        # names another pull for the same branch.
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        (self.config / "state.json").write_text('{"tracked_pulls": {"smoke/feat/x": 545}}', encoding="utf-8")

        # When: the installer runs again and migrates the new statement.
        result = self.install()

        # Then: the statement left state.json, the ledger keeps its own, and the
        # installer stops with migrate's status and says what to run.
        self.assert_migrate_failed(result, "but the ledger already states")
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout + result.stderr)

        # When: the installer runs again, and no one fixes anything first.
        result = self.install()

        # Then: it succeeds, and the ledger's statement stays on the remote, under this machine's ref.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout + result.stderr)

    def test_a_state_file_migrate_cannot_read_stops_the_install_before_its_report(self) -> None:
        # Given: an existing knives user whose state.json is not valid state.
        state = self.config / "state.json"
        state.write_text("not json", encoding="utf-8")
        before = state.read_bytes()

        # When: the installer joins the machine and migrates its statements.
        result = self.install()

        # Then: migrate stops before it changes state.json, and the installer stops
        # with migrate's status and says what to run.
        self.assert_migrate_failed(result, "is not valid state")
        self.assertEqual(state.read_bytes(), before, result.stdout + result.stderr)

        # When: state.json holds the statement again, and the installer runs again.
        state.write_text('{"tracked_pulls": {"smoke/feat/x": 544}}', encoding="utf-8")
        result = self.install()

        # Then: it succeeds, and the statement is on the remote, under this machine's ref.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout + result.stderr)

    def test_a_migrate_that_dies_without_output_stops_the_install_with_its_status(self) -> None:
        # Given: an existing knives user, and a migrate that crashes before it prints a report.
        self.env["KNIVES_SHIM_MIGRATE_DIES"] = "1"

        # When: the installer joins the machine and migrates its statements.
        result = self.install()

        # Then: the install stops with migrate's own status, and the installer points
        # to no report, because migrate printed none.
        self.assertEqual(result.returncode, 101, result.stdout + result.stderr)
        lines = result.stderr.splitlines()
        self.assertEqual(lines[-1], "knives: `knives ledger migrate` exited 101.", result.stdout + result.stderr)
        self.assertEqual(lines[-2], "thread 'main' panicked (test shim)", result.stdout + result.stderr)
        self.assertNotIn("the lines it printed above", result.stderr, result.stdout + result.stderr)
        self.assertEqual(self.statements_left(), 1, result.stdout + result.stderr)

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

    def private_registry(self) -> Path:
        return self.dotfiles / ".secrets" / "dotfiles" / "knives" / "repos.local.toml"

    def test_the_private_registry_is_linked_when_the_private_clone_holds_it(self) -> None:
        # Given: a new machine whose private clone (.secrets) holds the registry's private part.
        (self.config / "state.json").unlink()
        self.private_registry().parent.mkdir(parents=True)
        self.private_registry().write_text('[repos.smoke]\nforbidden = ["acme-corp"]\n', encoding="utf-8")

        # When: the installer joins it.
        result = self.install()

        # Then: knives finds the private part beside repos.toml, and reads it.
        link = self.config / "repos.local.toml"
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(link.is_symlink(), result.stdout + result.stderr)
        self.assertEqual(link.resolve(), self.private_registry().resolve())
        listed = subprocess.run(
            [self.env["KNIVES_BIN"], "repos", "--json"], capture_output=True, text=True,
            env=self.env, check=False,
        )
        self.assertNotIn("not a valid registry", listed.stderr)

    def test_no_private_registry_is_linked_without_the_private_clone(self) -> None:
        # Given: a new machine with no private clone.
        (self.config / "state.json").unlink()

        # When: the installer joins it.
        result = self.install()

        # Then: the install succeeds and leaves no private part beside repos.toml.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.config / "repos.local.toml").is_symlink())
        self.assertFalse((self.config / "repos.local.toml").exists())


if __name__ == "__main__":
    unittest.main()
