#!/usr/bin/env python3
"""installers/knives.sh stops when the statements it migrated cannot be pushed.

The tests also read the message the installer prints when `knives ledger migrate`
fails: a problem before migrate drops the statements, entries it could not send,
a problem after the drop, an error before its report, and a migrate that dies.

`knives ledger migrate` sends the ledger entries it wrote before it exits. So when
that push fails, migrate's own report and exit status say so, and the install stops.

Runs against the knives in KNIVES_BIN, else the pinned one mise resolves (never the
shims/knives wrapper), and is skipped when that knives has no `knives ledger`. Two more
tests pin the link to the registry's private part (repos.local.toml from the .secrets clone).
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
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

# Stands in for `knives`. With KNIVES_SHIM_SWEEP_DIES set, `ledger sweep` exits 101
# with nothing on stdout, as a panicking knives does. With KNIVES_SHIM_MIGRATE_DIES
# set, `ledger migrate` does the same. Everything else runs the real binary.
KNIVES_SHIM = r"""
if [ "${1:-}" = ledger ] && [ "${2:-}" = sweep ] && [ -n "${KNIVES_SHIM_SWEEP_DIES:-}" ]; then
    echo "thread 'main' panicked (test shim)" >&2
    exit 101
fi
if [ "${1:-}" = ledger ] && [ "${2:-}" = migrate ] && [ -n "${KNIVES_SHIM_MIGRATE_DIES:-}" ]; then
    echo "thread 'main' panicked (test shim)" >&2
    exit 101
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
        return subprocess.run(
            ["bash", str(self.dotfiles / "installers" / "knives.sh")],
            capture_output=True, text=True, env=self.env, check=False,
        )

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
        self.assert_install_succeeded(result)

    def test_control_an_accepted_push_carries_the_migrated_statements(self) -> None:
        # Given: an existing knives user, and a ledger remote that accepts pushes.
        # When: the installer runs.
        result = self.install()

        # Then: it succeeds, and the statement is on the remote, under this machine's ref.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The installer's own sweep ran, and swept.
        self.assertIn('"outcome":"swept"', result.stderr, result.stdout + result.stderr)
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
        # So the message names self.dotfiles as it is, not its resolved path. The installer quotes
        # the command with bash's `printf %q`, so ask the same bash how it quotes this path.
        rerun = subprocess.run(
            ["bash", "-c", 'printf "bash %q" "$1"', "_", str(self.dotfiles / "installers" / "knives.sh")],
            capture_output=True, text=True, env=self.env, check=True,
        ).stdout
        lines = result.stderr.splitlines()
        # A failure prints both lists whole, so it shows the message the installer printed.
        self.maxDiff = None
        self.assertEqual(
            lines[-3:],
            [
                "knives: `knives ledger migrate` exited 3.",
                "knives: the lines it printed above say what went wrong. Fix each problem they name"
                f" that needs a fix, then run: {rerun}",
                f"knives: if a line above asks for a change to repos.toml, make it in {self.dotfiles}/knives/repos.toml:"
                " at each run this installer replaces ~/.config/knives/repos.toml with a render of that file.",
            ],
            result.stdout + result.stderr,
        )
        self.assertTrue(
            any(knives_text in line for line in lines[:-3]),
            f"no line before the installer's message holds {knives_text!r}:\n" + result.stdout + result.stderr,
        )

    def assert_install_succeeded(self, result: subprocess.CompletedProcess[str]) -> None:
        """The installer exited 0, state.json holds no statement, and the remote holds this machine's ref."""
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.statements_left(), 0, result.stdout + result.stderr)
        self.assertEqual(self.refs_on_remote(), [f"refs/knives/{MACHINE}"], result.stdout + result.stderr)

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
        self.assert_install_succeeded(result)

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
        self.assert_install_succeeded(result)

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
        self.assert_install_succeeded(result)

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
        self.assert_install_succeeded(result)

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
        self.assert_install_succeeded(result)

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

    def test_the_command_the_message_names_runs_from_a_dotfiles_path_with_a_space(self) -> None:
        # Given: an existing knives user whose dotfiles are at a path with a space in it,
        # and whose git cannot reach the ledger repository.
        spaced = self.root / "dot files"
        self.dotfiles.rename(spaced)
        self.dotfiles = spaced
        (self.home / ".gitconfig").write_text(
            f'[url "{self.root / "unreachable.git"}"]\n\tinsteadOf = {LEDGER_URL}\n', encoding="utf-8"
        )

        # When: the installer joins the machine and migrates its statements.
        result = self.install()

        # Then: the installer stops with migrate's status, and its message names a command to run.
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        named = [line.split("then run: ", 1)[1] for line in result.stderr.splitlines() if "then run: " in line]
        self.assertEqual(len(named), 1, result.stdout + result.stderr)

        # When: git can reach the ledger repository again, and the user runs that command as printed.
        (self.home / ".gitconfig").write_text(f'[url "{self.remote}"]\n\tinsteadOf = {LEDGER_URL}\n', encoding="utf-8")
        rerun = subprocess.run(["bash", "-c", named[0]], capture_output=True, text=True, env=self.env, check=False)

        # Then: the command is this installer, it succeeds, and the statement is on the
        # remote, under this machine's ref.
        self.assert_install_succeeded(rerun)

    def ledger_git_dir(self) -> Path:
        """The git directory the installer makes for the ledger, `<owner>--<name>.git`."""
        ledger = LEDGER_URL.removeprefix("https://github.com/")
        return self.config / "ledger-repositories" / f"{ledger.replace('/', '--')}.git"

    def test_two_first_time_installs_join_the_ledger_one_at_a_time(self) -> None:
        real_flock = shutil.which("flock", path="/usr/local/bin:/usr/bin:/bin")
        if real_flock is None:
            self.skipTest("needs flock: where it is absent (macOS), the installer takes no lock")
        # A flock stub first on PATH runs the real flock. Before and after, it writes to the log
        # the path of the file that its fd argument names: `ask <path>`, then `done <path>
        # <exit status>`. The stub does not exec flock, and the lock stays on the open file of
        # that fd after the stub exits.
        log = self.root / "flock.log"
        write_executable(
            self.root / "bin" / "flock",
            f'path="$(readlink "/proc/$$/fd/${{@: -1}}")" || path=unknown\n'
            f'echo "ask $path" >>"{log}"\n'
            f'"{real_flock}" "$@" && status=0 || status=$?\n'
            f'echo "done $path $status" >>"{log}"\n'
            'exit "$status"',
        )
        lock = self.config / "ledger-repositories.lock"
        asked = f"ask {lock.resolve()}"
        returned = f"done {lock.resolve()} "
        got = f"{returned}0"
        git_dir = self.ledger_git_dir()

        def logged() -> list[str]:
            return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

        # Given: a new machine, and another install that holds the lock the installer
        # takes to join the ledger.
        (self.config / "state.json").unlink()
        holder = lock.open("w")
        fcntl.flock(holder, fcntl.LOCK_EX)

        # When: the installer starts, and asks flock for that lock.
        installer = subprocess.Popen(
            ["bash", str(self.dotfiles / "installers" / "knives.sh")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env,
            start_new_session=True,
        )
        try:
            for _ in range(1200):
                if asked in logged() or installer.poll() is not None:
                    break
                time.sleep(0.05)

            # Then: it asks for the lock.
            if installer.poll() is not None:
                stdout, stderr = installer.communicate()
                self.fail("the installer ended and never waited for ledger-repositories.lock:\n" + stdout + stderr)
            self.assertIn(asked, logged(), "the installer never asked flock for ledger-repositories.lock")

            # When: the other install keeps the lock for 1 s more. The test cannot see when flock
            # starts to wait, so it gives flock time to return. A flock that does not wait returns
            # in that time: with `flock -n`, it returned 9 to 22 ms after the ask in 20 runs.
            time.sleep(1)

            # Then: flock has not returned, and the installer has made no git directory.
            self.assertFalse(
                any(line.startswith(returned) for line in logged()),
                "flock returned while the other install held ledger-repositories.lock:\n" + "\n".join(logged()),
            )
            self.assertFalse(git_dir.exists(), "the installer made the git directory before it held the lock")

            # When: the other install lets the lock go.
            with log.open("a", encoding="utf-8") as log_file:
                log_file.write("released\n")
            holder.close()
            stdout, stderr = installer.communicate(timeout=120)

            # Then: flock got the lock only after the other install let it go, and the
            # installer joins the ledger and succeeds.
            self.assertEqual(installer.returncode, 0, stdout + stderr)
            self.assertIn(got, logged(), "flock never got ledger-repositories.lock:\n" + "\n".join(logged()))
            self.assertGreater(
                logged().index(got), logged().index("released"),
                "flock got ledger-repositories.lock before the other install let it go:\n" + "\n".join(logged()),
            )
            origin = subprocess.run(
                ["git", "--git-dir", str(git_dir), "config", "remote.origin.url"],
                capture_output=True, text=True, env=self.env, check=False,
            )
            self.assertEqual(origin.stdout.strip(), LEDGER_URL, stdout + stderr + origin.stderr)
        finally:
            # This block runs before tearDown, which deletes self.root. When the installer
            # still runs, the test kills its whole process group before it lets the lock go.
            # Else the subshell that waits in flock gets the lock and runs git in the deleted HOME.
            if installer.poll() is None:
                os.killpg(installer.pid, signal.SIGKILL)
            installer.communicate(timeout=60)
            holder.close()

    def test_a_git_directory_that_knives_makes_during_the_install_is_kept(self) -> None:
        real_git = shutil.which("git", path="/usr/local/bin:/usr/bin:/bin")
        self.assertIsNotNone(real_git, "needs git in /usr/local/bin, /usr/bin or /bin")
        git_dir = self.ledger_git_dir()
        joined = f"{HOSTNAME}-0000feed"
        # A git stub first on PATH stands in for a knives command that joins the ledger. When
        # the installer runs `git init` on the ledger's git directory, and the directory does
        # not exist, the stub first makes it as the join of knives 5.0.0 does. It makes the
        # directory in a scratch directory, gives it an origin and a machine name of its own,
        # and renames it into place. Then, and for every other call, the stub runs the real git.
        write_executable(
            self.root / "bin" / "git",
            f'if [ "$*" = "init --quiet --bare {git_dir}" ] && [ ! -e "{git_dir}" ]; then\n'
            f'    scratch="$(mktemp -d "{self.config}/.knives-joining-XXXXXX")"\n'
            '    made="$scratch/ledger.git"\n'
            f'    "{real_git}" init --quiet --bare "$made"\n'
            f'    "{real_git}" --git-dir="$made" config core.bare false\n'
            f'    "{real_git}" --git-dir="$made" config core.worktree "{self.config / "ledger"}"\n'
            f'    "{real_git}" --git-dir="$made" remote add origin "{LEDGER_URL}"\n'
            f'    "{real_git}" --git-dir="$made" config knives.machine "{joined}"\n'
            f'    mv "$made" "{git_dir}"\n'
            '    rmdir "$scratch"\n'
            'fi\n'
            f'exec "{real_git}" "$@"',
        )

        # Given: a new machine, and a knives command that joins the ledger after the
        # installer finds no git directory for it, and before the installer's `git init`.
        (self.config / "state.json").unlink()

        # When: the installer joins the machine.
        result = self.install()

        # Then: it succeeds, and the git directory keeps the origin and the machine name
        # that knives gave it, and is not bare.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        def config(*key: str) -> list[str]:
            return subprocess.run(
                ["git", "--git-dir", str(git_dir), "config", *key],
                capture_output=True, text=True, env=self.env, check=False,
            ).stdout.split()

        self.assertEqual(config("--get-all", "remote.origin.url"), [LEDGER_URL], result.stdout + result.stderr)
        self.assertEqual(config("knives.machine"), [joined], result.stdout + result.stderr)
        self.assertEqual(config("core.bare"), ["false"], result.stdout + result.stderr)

    def test_a_knives_command_never_finds_the_ledger_git_directory_half_made(self) -> None:
        real_git = shutil.which("git", path="/usr/local/bin:/usr/bin:/bin")
        self.assertIsNotNone(real_git, "needs git in /usr/local/bin, /usr/bin or /bin")
        git_dir = self.ledger_git_dir()
        log = self.root / "half-made.log"
        # knives reads every directory in ledger-repositories as a git directory of a ledger.
        # Found with no origin, it says the directory "is there already, and its origin is not"
        # the ledger and exits 3; found with no machine name, it refuses the sweep; found bare,
        # it reads no work tree (knives 5.0.0, src/ledger_sweep/join.rs:143-146). A git stub
        # first on PATH stands in for a knives command that runs at any moment of the install:
        # before each git call, when the ledger's git directory exists, it logs the call if
        # the directory has no origin, no knives.machine, or core.bare other than false.
        write_executable(
            self.root / "bin" / "git",
            f'if [ -e "{git_dir}" ]; then\n'
            f'    origin="$("{real_git}" --git-dir="{git_dir}" config remote.origin.url || true)"\n'
            f'    machine="$("{real_git}" --git-dir="{git_dir}" config knives.machine || true)"\n'
            f'    bare="$("{real_git}" --git-dir="{git_dir}" config core.bare || true)"\n'
            '    if [ -z "$origin" ] || [ -z "$machine" ] || [ "$bare" != false ]; then\n'
            f'        echo "before git $*: origin=$origin machine=$machine bare=$bare" >>"{log}"\n'
            '    fi\n'
            'fi\n'
            f'exec "{real_git}" "$@"',
        )

        # Given: a new machine.
        (self.config / "state.json").unlink()

        # When: the installer joins the machine to the ledger.
        result = self.install()

        # Then: it succeeds, and at no git call did the git directory stand there without
        # an origin, a machine name, or with core.bare other than false.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        half_made = log.read_text(encoding="utf-8") if log.exists() else ""
        self.assertEqual(half_made, "", "the ledger's git directory stood there half made:\n" + half_made)
        origin = subprocess.run(
            [real_git, "--git-dir", str(git_dir), "config", "remote.origin.url"],
            capture_output=True, text=True, env=self.env, check=False,
        )
        self.assertEqual(origin.stdout.strip(), LEDGER_URL, result.stdout + result.stderr + origin.stderr)

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
