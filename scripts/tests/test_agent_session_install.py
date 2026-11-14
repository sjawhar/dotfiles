#!/usr/bin/env python3
"""scripts/agent-session installs a missing pinned agent before it refuses.

The launcher must run one locked ``mise install`` in DOTFILES_DIR, then use ``mise which`` for
its pinned binary. These tests use a recording mise stand-in so no installed tool or user mise
directory affects the result.
"""

from __future__ import annotations

import fcntl
import multiprocessing
import os
import signal
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "agent-session"


AGENT_BODY = """printf 'AGENT_PWD=%s\\n' \"$PWD\"\nprintf 'AGENT_ARG=%s\\n' \"$@\"\n"""


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def write_agent(path: Path) -> None:
    executable(path, AGENT_BODY)


def hold_install_lock(lock: str, ready: str, release: str, agent: str | None) -> None:
    """Hold the launch/install lock until the parent requests its release."""
    lock_path = Path(lock)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        Path(ready).touch()
        while not Path(release).exists():
            time.sleep(0.01)
        if agent is not None:
            write_agent(Path(agent))


class AgentSessionInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-session-install-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.dotfiles = self.root / "dotfiles"
        self.dotfiles_scripts = self.dotfiles / "scripts"
        self.dotfiles_scripts.mkdir(parents=True)
        (self.dotfiles / "shims").mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.safe_bin = self.root / "safe-bin"
        self.safe_bin.mkdir()
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        self.caller = self.root / "caller"
        self.caller.mkdir()
        self.record = self.root / "record"
        self.record.touch()
        self.data = self.root / "mise-data"

        for name in ("bash", "cat", "chmod", "flock", "id", "mkdir", "mktemp", "realpath", "rm", "sleep", "tail", "timeout", "true"):
            source = next((directory / name for directory in (Path("/usr/bin"), Path("/bin")) if (directory / name).exists()), None)
            if source is None:
                self.fail(f"required command is absent: {name}")
            (self.safe_bin / name).symlink_to(source)

        executable(self.dotfiles_scripts / "agent-secrets-session", 'printf "agent-secrets-session\\n" >> "$RECORD"\nexec "$@"')
        executable(self.dotfiles_scripts / "gh-agent-config", "echo /nonexistent")
        (self.dotfiles_scripts / "agent-spend-env.sh").write_text("agent_spend_env() { :; }\n", encoding="utf-8")
        self.mise_body = r'''
data="${MISE_DATA_DIR:-$HOME/.mise}"
lock="$data/.dotfiles-install.lock"
if flock -n -E 75 "$lock" true; then
    state=free
else
    rc=$?
    case "$rc" in
        75) state=held ;;
        *) state="probe-error-$rc" ;;
    esac
fi
printf '%s|argv=%s|PWD=%s|MISE_DATA_DIR=%s|%s\n' "$1" "$*" "$PWD" "$data" "$state" >> "$RECORD"
case "$1" in
    install)
        printf 'mise install output\n' >&2
        if [[ "${FAKE_MISE_WRITE_AGENT:-1}" = 1 ]]; then
            agent="$data/installs/fakeagent/1.0/bin/fakeagent"
            mkdir -p "${agent%/*}"
            cat > "$agent" <<'AGENT'
#!/bin/bash
printf 'AGENT_PWD=%s\n' "$PWD"
printf 'AGENT_ARG=%s\n' "$@"
if [[ -n "${FAKE_AGENT_RECORD_CDPATH:-}" ]]; then
    printf 'AGENT_CDPATH=%s\n' "$CDPATH"
fi
AGENT
            chmod +x "$agent"
        fi
        if [[ "${FAKE_MISE_IGNORE_TERM:-0}" = 1 ]]; then
            trap '' TERM
            printf '%s\n' "$$" > "$FAKE_PID_FILE"
            exec sleep "${FAKE_MISE_SLEEP:-60}"
        fi
        if [[ -n "${FAKE_MISE_SLEEP:-}" ]]; then sleep "$FAKE_MISE_SLEEP"; fi
        exit "${FAKE_MISE_EXIT:-0}"
        ;;
    which)
        if [[ "${FAKE_MISE_WHICH_SHIM:-0}" = 1 ]]; then
            shim="$data/shims/fakeagent"
            mkdir -p "${shim%/*}"
            cat > "$shim" <<'AGENT'
#!/bin/bash
exit 0
AGENT
            chmod +x "$shim"
            printf '%s\n' "$shim"
            exit 0
        fi
        agent="$data/installs/fakeagent/1.0/bin/fakeagent"
        if [[ -x "$agent" ]]; then
            printf '%s\n' "$agent"
        else
            printf 'mise ERROR fakeagent is not a mise bin\n' >&2
            exit 1
        fi
        ;;
    *)
        printf 'mise ERROR unsupported command %s\n' "$1" >&2
        exit 2
        ;;
esac
'''
        executable(self.bin / "mise", self.mise_body)

    def env(self, *, mise_on_path: bool = True, data: bool = True, **extra: str) -> dict[str, str]:
        path = [str(self.safe_bin)]
        if mise_on_path:
            path.insert(0, str(self.bin))
        env = {
            "DOTFILES_DIR": str(self.dotfiles),
            "ENVOY_MACHINE_ID": "test-machine",
            "HOME": str(self.root),
            "PATH": os.pathsep.join(path),
            "RECORD": str(self.record),
            "TMPDIR": str(self.tmp),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "AGENT_SESSION_NVME_HOME": str(self.root / "no-nvme"),
        }
        if data:
            env["MISE_DATA_DIR"] = str(self.data)
        env.update(extra)
        return env

    def launch(
        self,
        *args: str,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: float = 15,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), "fakeagent", *args],
            cwd=self.caller if cwd is None else cwd,
            env=self.env() if env is None else env,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )

    def records(self) -> list[str]:
        return self.record.read_text(encoding="utf-8").splitlines()

    def assert_agent_started(self, result: subprocess.CompletedProcess[str], *args: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"AGENT_PWD={self.caller}", *(f"AGENT_ARG={arg}" for arg in args)],
        )
        self.assertIn("agent-secrets-session", self.records())

    def install_record(self) -> str:
        return next(line for line in self.records() if line.startswith("install|"))

    def start_locker(self, *, write_agent_after_release: bool) -> tuple[multiprocessing.Process, Path]:
        lock = self.data / ".dotfiles-install.lock"
        ready = self.root / "locker-ready"
        release = self.root / "locker-release"
        agent = self.data / "installs/fakeagent/1.0/bin/fakeagent" if write_agent_after_release else None
        locker = multiprocessing.Process(
            target=hold_install_lock,
            args=(str(lock), str(ready), str(release), None if agent is None else str(agent)),
        )
        locker.start()
        self.addCleanup(self.release_locker, locker, release)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), "locker did not acquire the install lock")
        return locker, release

    @staticmethod
    def release_locker(locker: multiprocessing.Process, release: Path) -> None:
        release.touch()
        locker.join(timeout=5)
        if locker.is_alive():
            locker.kill()
            locker.join(timeout=5)

    def test_missing_agent_installs_in_dotfiles_and_launches_from_the_caller(self) -> None:
        result = self.launch("--mode", "test")

        self.assert_agent_started(result, "--mode", "test")
        self.assertNotIn("mise install output", result.stdout)
        install = self.install_record()
        self.assertIn(f"PWD={self.dotfiles}", install)
        self.assertIn(f"MISE_DATA_DIR={self.data}", install)
        self.assertTrue(install.endswith("|held"), install)
        which = next(line for line in self.records() if line.startswith("which|"))
        self.assertTrue(which.endswith("|free"), which)

    def test_unusable_tmpdir_falls_back_and_launches_the_agent(self) -> None:
        missing_tmp = self.root / "missing-tmp"
        result = self.launch("--mode", "tmpdir-fallback", env=self.env(TMPDIR=str(missing_tmp)))

        self.assertFalse(missing_tmp.exists())
        self.assert_agent_started(result, "--mode", "tmpdir-fallback")
        self.assertTrue(self.install_record().startswith("install|"), self.records())
        self.assertTrue(any(line.startswith("which|argv=which fakeagent") for line in self.records()))

    def test_unusable_tmpdir_without_an_agent_refuses(self) -> None:
        missing_tmp = self.root / "missing-tmp"
        result = self.launch(env=self.env(TMPDIR=str(missing_tmp), FAKE_MISE_WRITE_AGENT="0"))

        self.assertFalse(missing_tmp.exists())
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("no fakeagent on PATH other than a wrapper", result.stderr)
        self.assertTrue(self.install_record().startswith("install|"), self.records())
        self.assertTrue(any(line.startswith("which|argv=which fakeagent") for line in self.records()))
        # The /tmp fallback keeps mise's output for the refusal.
        self.assertIn("mise install output", result.stderr)
        self.assertIn("mise ERROR fakeagent is not a mise bin", result.stderr)

    def test_relative_dotfiles_dir_with_cdpath_starts_missing_agent(self) -> None:
        result = self.launch(
            "--mode",
            "relative-dotfiles",
            cwd=self.root,
            env=self.env(DOTFILES_DIR="dotfiles", CDPATH=".", FAKE_AGENT_RECORD_CDPATH="1"),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"AGENT_PWD={self.root}",
                "AGENT_ARG=--mode",
                "AGENT_ARG=relative-dotfiles",
                "AGENT_CDPATH=.",
            ],
        )
        self.assertIn("agent-secrets-session", self.records())
    def test_relative_mise_data_dir_is_absolute_for_the_locked_install(self) -> None:
        data = self.caller / "rel-data"
        result = self.launch(
            "--mode",
            "relative-data",
            cwd=self.caller,
            env=self.env(MISE_DATA_DIR="rel-data"),
        )

        self.assert_agent_started(result, "--mode", "relative-data")
        install = self.install_record()
        self.assertIn(f"MISE_DATA_DIR={data}", install)
        self.assertTrue(install.endswith("|held"), install)

    def test_relative_mise_path_is_absolute_for_the_install(self) -> None:
        relative_mise = self.caller / "bin" / "mise"
        relative_mise.parent.mkdir()
        relative_mise.symlink_to(self.bin / "mise")
        result = self.launch(
            "--mode",
            "relative-mise",
            cwd=self.caller,
            env=self.env(PATH=f"bin:{self.safe_bin}"),
        )

        self.assert_agent_started(result, "--mode", "relative-mise")
        self.assertTrue(self.install_record().endswith("|held"), self.install_record())

    def test_relative_dotfiles_dir_puts_an_absolute_shims_dir_first_on_the_agents_path(self) -> None:
        # agent-session puts $DOTFILES_DIR/shims first on the agent's PATH; a relative entry there
        # would name another directory as soon as the agent changes directory.
        on_path = self.root / "on-path"
        executable(on_path / "fakeagent", 'printf "AGENT_PATH=%s\\n" "$PATH"')
        result = self.launch(
            cwd=self.root,
            env=self.env(DOTFILES_DIR="dotfiles", PATH=f"{on_path}:{self.safe_bin}"),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0].split("=", 1)[1].split(":")[0], str(self.dotfiles / "shims"))

    def test_failed_install_that_provides_the_agent_still_launches_it(self) -> None:
        result = self.launch("--mode", "failed-install", env=self.env(FAKE_MISE_EXIT="1"))

        self.assert_agent_started(result, "--mode", "failed-install")
        self.assertIn("agent-session: `mise install`", result.stderr)
        self.assertIn("exited 1", result.stderr)
        self.assertIn("mise install output", result.stderr)

    def test_timed_out_install_that_provides_the_agent_still_launches_it(self) -> None:
        started = time.monotonic()
        result = self.launch("--mode", "timed-out-install", env=self.env(DOTFILES_TOOLS_TIMEOUT="2", FAKE_MISE_SLEEP="5"))

        self.assertLess(time.monotonic() - started, 15)
        self.assert_agent_started(result, "--mode", "timed-out-install")
        self.assertIn("exited 124 (past its limit of 2 s, lock wait included)", result.stderr)
        self.assertIn("mise install output", result.stderr)

    def test_timed_out_install_kills_survivor_and_releases_lock(self) -> None:
        pid_file = self.root / "mise.pid"
        lock = self.data / ".dotfiles-install.lock"
        result = self.launch(
            "--mode",
            "survivor-killed",
            env=self.env(
                DOTFILES_TOOLS_TIMEOUT="2",
                FAKE_MISE_SLEEP="60",
                FAKE_MISE_IGNORE_TERM="1",
                FAKE_PID_FILE=str(pid_file),
            ),
            timeout=40,
        )

        self.assert_agent_started(result, "--mode", "survivor-killed")
        self.assertIn("exited 124", result.stderr)
        self.assertTrue(pid_file.exists())
        pid = int(pid_file.read_text().strip())
        try:
            self.assertFalse(Path(f"/proc/{pid}").exists(), f"survivor {pid} remains")
            lock_probe = subprocess.run(["flock", "-n", str(lock), "true"], capture_output=True, text=True, timeout=3)
            self.assertEqual(lock_probe.returncode, 0, lock_probe.stderr)
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            for _ in range(30):
                if not Path(f"/proc/{pid}").exists():
                    break
                time.sleep(0.1)

    def test_timed_out_install_that_ends_inside_the_grace_is_not_killed(self) -> None:
        # The grace gives an installer that outlives its limit time to end by itself; only one
        # still running at the end of the grace is killed. This one ignores TERM and ends 4 s in.
        pid_file = self.root / "mise.pid"
        started = time.monotonic()
        result = self.launch(
            "--mode",
            "ends-in-grace",
            env=self.env(
                DOTFILES_TOOLS_TIMEOUT="1",
                FAKE_MISE_SLEEP="4",
                FAKE_MISE_IGNORE_TERM="1",
                FAKE_PID_FILE=str(pid_file),
            ),
            timeout=40,
        )
        elapsed = time.monotonic() - started

        self.assert_agent_started(result, "--mode", "ends-in-grace")
        self.assertGreaterEqual(elapsed, 3.5, "the installer was killed before the grace ended")
        self.assertLess(elapsed, 9, "the launch waited out the whole grace for an installer already gone")

    def test_timed_out_install_without_the_agent_refuses(self) -> None:
        started = time.monotonic()
        result = self.launch(env=self.env(DOTFILES_TOOLS_TIMEOUT="2", FAKE_MISE_SLEEP="5", FAKE_MISE_WRITE_AGENT="0"))

        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("exited 124 (past its limit of 2 s, lock wait included)", result.stderr)
        self.assertIn("no fakeagent on PATH other than a wrapper", result.stderr)
        self.assertIn("mise install output", result.stderr)

    def test_mise_which_without_a_binary_refuses_with_its_output(self) -> None:
        result = self.launch(env=self.env(FAKE_MISE_WRITE_AGENT="0"))

        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("no fakeagent on PATH other than a wrapper", result.stderr)
        self.assertIn("mise install output", result.stderr)
        self.assertIn("mise ERROR fakeagent is not a mise bin", result.stderr)
        self.assertTrue(any(line.startswith("which|argv=which fakeagent") for line in self.records()))

    def test_relative_tmpdir_keeps_mise_which_output_in_the_refusal(self) -> None:
        (self.root / "rel-tmp").mkdir()
        result = self.launch(cwd=self.root, env=self.env(FAKE_MISE_WRITE_AGENT="0", TMPDIR="rel-tmp"))

        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("mise install output", result.stderr)
        self.assertIn("mise ERROR fakeagent is not a mise bin", result.stderr)

    def test_mise_which_shim_is_refused(self) -> None:
        result = self.launch(env=self.env(FAKE_MISE_WHICH_SHIM="1"))

        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("no fakeagent on PATH other than a wrapper", result.stderr)
        self.assertNotIn("AGENT_PWD=", result.stdout)
        self.assertTrue(any(line.startswith("which|argv=which fakeagent") for line in self.records()))

    def test_dotfiles_mise_and_default_data_directory_are_used_without_mise_on_path(self) -> None:
        executable(self.dotfiles / "bin" / "mise", self.mise_body)
        result = self.launch("--mode", "fallback", env=self.env(mise_on_path=False, data=False))

        self.assert_agent_started(result, "--mode", "fallback")
        install = self.install_record()
        self.assertIn(f"MISE_DATA_DIR={self.root / '.mise'}", install)
        self.assertIn(f"PWD={self.dotfiles}", install)

    def test_relative_dotfiles_dir_finds_the_dotfiles_mise_without_mise_on_path(self) -> None:
        # R6: mise on PATH, else $DOTFILES_DIR/bin/mise. A relative DOTFILES_DIR is supported
        # (test_relative_dotfiles_dir_with_cdpath_starts_missing_agent), so the fallback must
        # still name the checkout's mise after the install enters DOTFILES_DIR.
        executable(self.dotfiles / "bin" / "mise", self.mise_body)
        result = self.launch(
            "--mode",
            "relative-fallback",
            cwd=self.root,
            env=self.env(mise_on_path=False, DOTFILES_DIR="dotfiles"),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"AGENT_PWD={self.root}", "AGENT_ARG=--mode", "AGENT_ARG=relative-fallback"],
        )
        self.assertIn(f"PWD={self.dotfiles}", self.install_record())

    def test_lock_waits_for_another_install_then_launches_the_agent(self) -> None:
        locker, release = self.start_locker(write_agent_after_release=True)
        launch = subprocess.Popen(
            [str(SCRIPT), "fakeagent", "--mode", "waited"],
            cwd=self.caller,
            env=self.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(0.2)
            self.assertFalse(any(line.startswith("install|") for line in self.records()), self.records())
            release.touch()
            locker.join(timeout=5)
            self.assertFalse(locker.is_alive(), "locker did not release the install lock")
            stdout, stderr = launch.communicate(timeout=15)
        finally:
            if launch.poll() is None:
                launch.kill()
                launch.communicate(timeout=5)

        result = subprocess.CompletedProcess(launch.args, launch.returncode, stdout, stderr)
        self.assert_agent_started(result, "--mode", "waited")
        self.assertTrue(self.install_record().endswith("|held"), self.install_record())

    def test_lock_past_the_limit_uses_an_agent_mise_already_knows(self) -> None:
        agent = self.data / "installs/fakeagent/1.0/bin/fakeagent"
        write_agent(agent)
        locker, release = self.start_locker(write_agent_after_release=False)
        started = time.monotonic()
        try:
            result = self.launch("--mode", "lock-timeout", env=self.env(DOTFILES_TOOLS_TIMEOUT="2"))
        finally:
            release.touch()
            locker.join(timeout=5)

        self.assertLess(time.monotonic() - started, 15)
        self.assert_agent_started(result, "--mode", "lock-timeout")
        self.assertIn("exited 124 (past its limit of 2 s, lock wait included)", result.stderr)
        self.assertFalse(any(line.startswith("install|") for line in self.records()), self.records())
        self.assertTrue(any(line.startswith("which|argv=which fakeagent") for line in self.records()))

    def test_lock_past_the_limit_without_an_agent_refuses(self) -> None:
        locker, release = self.start_locker(write_agent_after_release=False)
        started = time.monotonic()
        try:
            result = self.launch(env=self.env(DOTFILES_TOOLS_TIMEOUT="2"))
        finally:
            release.touch()
            locker.join(timeout=5)

        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("exited 124 (past its limit of 2 s, lock wait included)", result.stderr)
        self.assertIn("no fakeagent on PATH other than a wrapper", result.stderr)
        self.assertIn("mise ERROR fakeagent is not a mise bin", result.stderr)
        self.assertFalse(any(line.startswith("install|") for line in self.records()), self.records())
        self.assertTrue(any(line.startswith("which|argv=which fakeagent") for line in self.records()))

    def test_agent_already_on_path_does_not_call_mise(self) -> None:
        write_agent(self.bin / "fakeagent")
        result = self.launch("--mode", "path-agent")

        self.assert_agent_started(result, "--mode", "path-agent")
        self.assertFalse(any(line.startswith(("install|", "which|")) for line in self.records()), self.records())


if __name__ == "__main__":
    unittest.main()
