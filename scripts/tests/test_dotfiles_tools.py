#!/usr/bin/env python3
"""Tests for the tools step that follows dotfiles sync and publish moves.

A move can bring a new mise pin, so it must install the checkout's tools before it returns.
The step uses the checkout .bashrc environment, serializes mise install with the shared lock,
and reports failures or limits without undoing the move. These tests use recording stand-ins to
check the local, remote, publish, refusal, lock, and timeout paths without installing tools.
KnivesRegistry checks the step's first command, the real scripts/knives-registry-render.
"""

from __future__ import annotations

import contextlib
import fcntl
import importlib.machinery
import importlib.util
import os
import signal
import subprocess
import threading
import time
import unittest
from collections.abc import Iterator
from pathlib import Path

from scripts.tests.test_dotfiles_follow import REGISTRY_COPY, REGISTRY_V1, REGISTRY_V2, RegistryCopy, add_registry_render

ROOT = Path(__file__).resolve().parents[2]
SYNC = ROOT / "scripts" / "dotfiles-sync"
PUBLISH = ROOT / "scripts" / "dotfiles-publish"
# The limit on a sync, a publish or a git setup call: it only stops a stuck run. On a dev box at
# load average 900-1500 (288 CPUs, 2026-11-20) one run took up to 52 s, and a 15 s limit failed
# nine cases that do not hang. The limits under test (DOTFILES_TOOLS_TIMEOUT, the kill grace) stay
# short; a case whose limit fails runs into this guard and fails.
HANG_GUARD = 180
_SYNC_LOADER = importlib.machinery.SourceFileLoader("dotfiles_sync_fixture", str(Path(__file__).with_name("test_dotfiles_sync.py")))
_SYNC_SPEC = importlib.util.spec_from_loader(_SYNC_LOADER.name, _SYNC_LOADER)
assert _SYNC_SPEC is not None
_sync_fixture = importlib.util.module_from_spec(_SYNC_SPEC)
_SYNC_LOADER.exec_module(_sync_fixture)
DotfilesCheckout = _sync_fixture.DotfilesCheckout


class ToolsStep(DotfilesCheckout):
    """The fixture and helpers of every tools-step case. It holds no case itself, so a class with
    another fixture does not run every case again."""

    def setUp(self) -> None:
        super().setUp()
        self.root = Path(self.temp_dir.name)
        self.record = self.root / "record"
        self.ssh_record = self.root / "ssh-record"
        self.env.update(RECORD=str(self.record), SSH_RECORD=str(self.ssh_record))
        self._write_standin("mise", self.checkout / "bin" / "mise")
        self._write_standin("bun", self.root / "bin" / "bun")

        (self.upstream / ".bashrc").write_text(
            'export MISE_DATA_DIR="$HOME/.mise"\n'
            'export MISE_CACHE_DIR="$HOME/.cache/mise"\n'
            'export MISE_TRUSTED_CONFIG_PATHS="$HOME/.dotfiles"\n'
            'export PATH="$HOME/.dotfiles/bin:$PATH"\n'
        )
        (self.upstream / ".gitignore").write_text("bin/\nomp/plugins/node_modules/\n")
        (self.upstream / "mise.toml").write_text('[tools]\n"aqua:sharkdp/hyperfine" = "1.20.0"\n')
        plugins = self.upstream / "omp" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "package.json").write_text('{"name": "tools-test"}\n')
        self.commit_upstream("tools")
        self.tools_commit = self.run_ok("git", "rev-parse", "main", cwd=self.origin).strip()

    def _write_standin(self, tool: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        upper = tool.upper()
        path.write_text(
            f"""#!/usr/bin/env bash
set -eu
lock="${{MISE_DATA_DIR:-$HOME/.mise}}/.dotfiles-install.lock"
if flock -n -E 75 "$lock" true; then
    state=free
else
    rc=$?
    case "$rc" in
        75) state=held ;;
        *) state="probe-error-$rc" ;;
    esac
fi
origin_main=""
if [[ -n "${{ORIGIN_GIT:-}}" ]]; then
    origin_main="$(git --git-dir="$ORIGIN_GIT" rev-parse main)"
fi
printf '{tool} %s|%s|%s|%s|%s|%s|%s\n' "$*" "$PWD" "${{MISE_DATA_DIR:-}}" "${{MISE_CACHE_DIR:-}}" "$state" "$origin_main" "${{FAKE_REMOTE_HOST:-}}" >> "$RECORD"
if [[ -n "${{FAKE_FAIL_HOST:-}}" && "${{FAKE_FAIL_HOST}}" = "${{FAKE_REMOTE_HOST:-$(hostname)}}" ]]; then
    echo "{tool} ERROR failed on ${{FAKE_FAIL_HOST}}" >&2
    exit 23
fi
sleep_seconds="${{FAKE_{upper}_SLEEP:-0}}"
if [[ "${{FAKE_{upper}_IGNORE_TERM:-0}}" = 1 ]]; then
    trap '' TERM
    printf '%s\n' "$$" > "$FAKE_PID_FILE"
    exec sleep "$sleep_seconds"
fi
sleep "$sleep_seconds"
rc="${{FAKE_{upper}_RC:-0}}"
if [[ "$rc" != 0 ]]; then
    echo "{tool} ERROR forced failure" >&2
fi
exit "$rc"
"""
        )
        path.chmod(0o755)

    @staticmethod
    def _write_failing_mktemp(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/usr/bin/env bash\nexit 1\n")
        path.chmod(0o755)

    def _install_ssh(self) -> None:
        ssh = self.root / "bin" / "ssh"
        ssh.write_text(
            """#!/usr/bin/env bash
set -eu
while [[ $# -gt 0 && "$1" = -* ]]; do
    if [[ "$1" = -o ]]; then shift 2; else shift; fi
done
destination="$1"
shift
printf '%s|%s\n' "$destination" "$*" >> "$SSH_RECORD"
printf -v remote '%q ' "$@"
env FAKE_REMOTE_HOST="$destination" bash -c "$remote"
"""
        )
        ssh.chmod(0o755)

    def _run(self, script: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None, timeout: float = HANG_GUARD) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script), *args],
            cwd=cwd,
            env=env or self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def sync(self, *args: str, env: dict[str, str] | None = None, timeout: float = HANG_GUARD) -> subprocess.CompletedProcess[str]:
        return self._run(SYNC, *args, env=env, timeout=timeout)

    def publish(self, rev: str, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return self._run(PUBLISH, rev, env=env, cwd=cwd)

    def records(self) -> list[str]:
        if not self.record.exists():
            return []
        return self.record.read_text().splitlines()

    def calls(self, tool: str) -> list[str]:
        return [line for line in self.records() if line.startswith(f"{tool} ")]

    def clear_records(self) -> None:
        self.record.write_text("")

    def assert_tools_ran(self) -> None:
        self.assertEqual(len(self.calls("mise")), 1, self.records())
        self.assertEqual(len(self.calls("bun")), 1, self.records())

    def local_commit(self, path: str = "mine.txt", text: str = "mine\n") -> str:
        (self.checkout / path).write_text(text)
        self.jj("commit", "--quiet", "-m", "mine", path)
        return self.jj("log", "-r", "@-", "--no-graph", "-T", "change_id")

    def plain_checkout(self, name: str) -> tuple[Path, dict[str, str]]:
        home = self.root / f"{name}-home"
        home.mkdir()
        checkout = home / ".dotfiles"
        env = {**self.env, "HOME": str(home)}
        cloned = subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(checkout)], env=env, capture_output=True, text=True, timeout=HANG_GUARD
        )
        self.assertEqual(cloned.returncode, 0, cloned.stderr)
        self._write_standin("mise", checkout / "bin" / "mise")
        return checkout, env


class DotfilesTools(ToolsStep):
    """The step's cases in checkouts that hold no scripts/knives-registry-render."""

    def test_case_01_local_sync_uses_bashrc_environment_and_lock(self) -> None:
        result = self.sync("local")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        mise = self.calls("mise")[0].split("|")
        bun = self.calls("bun")[0].split("|")
        self.assertEqual(mise[:5], ["mise install", str(self.checkout), str(self.home / ".mise"), str(self.home / ".cache/mise"), "held"])
        self.assertEqual(bun[:5], ["bun install --frozen-lockfile", str(self.checkout / "omp" / "plugins"), str(self.home / ".mise"), str(self.home / ".cache/mise"), "free"])
        self.assertIn(f"tools: `mise install` passed in {self.checkout}", result.stdout)
        self.assertIn(f"tools: `bun install --frozen-lockfile` passed in {self.checkout}/omp/plugins", result.stdout)

    def test_case_02_sync_runs_tools_without_a_new_or_pin_commit(self) -> None:
        self.assertEqual(self.sync("local").returncode, 0)
        self.clear_records()
        self.assertEqual(self.sync("local").returncode, 0)
        (self.upstream / "not-a-pin.txt").write_text("changed\n")
        self.commit_upstream("not a pin")
        self.assertEqual(self.sync("local").returncode, 0)

        self.assertEqual(len(self.calls("mise")), 2, self.records())
        self.assertEqual(len(self.calls("bun")), 2, self.records())

    def test_case_03_remote_sync_runs_tools_in_ssh_session(self) -> None:
        self._install_ssh()
        result = self.sync("fakehost")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ssh_lines = self.ssh_record.read_text().splitlines()
        self.assertEqual(len(ssh_lines), 1, ssh_lines)
        self.assertIn("fakehost", ssh_lines[0])
        self.assertIn("DOTFILES_TOOLS_TIMEOUT=600", ssh_lines[0])
        self.assert_tools_ran()
        self.assertEqual(self.calls("mise")[0].split("|")[-1], "fakehost")

    def test_case_03b_remote_ssh_limit_covers_the_move_the_lock_wait_and_every_command(self) -> None:
        # The ssh limit is the remote host's whole budget: 180 s move, 300 s sync-lock wait, and
        # for each of the four tools commands (omp-link-rules, the registry render, `mise install`,
        # the Bun install) its limit plus its 10 s kill grace. Left at the move's 180 s, a remote
        # install longer than that is killed mid-download.
        self._install_ssh()
        ssh = self.root / "bin" / "ssh"
        ssh.rename(self.root / "bin" / "ssh-step")
        ssh.write_text(
            '#!/usr/bin/env bash\ntr "\\0" " " < /proc/$PPID/cmdline >> "$SSH_RECORD.parent"\n'
            'echo >> "$SSH_RECORD.parent"\nexec "$(dirname "$0")/ssh-step" "$@"\n'
        )
        ssh.chmod(0o755)
        for limit, outer in ((None, 2920), ("5", 540)):
            with self.subTest(DOTFILES_TOOLS_TIMEOUT=limit):
                Path(f"{self.ssh_record}.parent").write_text("")
                env = dict(self.env)
                if limit is not None:
                    env["DOTFILES_TOOLS_TIMEOUT"] = limit
                result = self.sync("fakehost", env=env)

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                parent = Path(f"{self.ssh_record}.parent").read_text().split()
                self.assertEqual(parent[:3], ["timeout", str(outer), "ssh"], parent)

    def test_case_04_default_host_list_runs_one_remote_step_per_machine(self) -> None:
        self._install_ssh()
        result = self.sync()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        destinations = [line.split("|", 1)[0] for line in self.ssh_record.read_text().splitlines()]
        self.assertEqual(destinations, ["sami@sami", "sami@sami-leanperf", "sami@sami-rocqperf"])
        self.assertEqual(len(self.calls("mise")), 3, self.records())
        self.assertEqual(len(self.calls("bun")), 3, self.records())

    def test_case_05_plain_git_checkout_runs_tools(self) -> None:
        _, env = self.plain_checkout("plain")
        result = self.sync("local", env=env)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()

    def test_case_06_refusals_do_not_run_tools(self) -> None:
        self.local_commit("unpublished.txt")
        unpublished = self.sync("local")
        self.assertNotEqual(unpublished.returncode, 0, unpublished.stdout + unpublished.stderr)
        self.assertIn("UNPUBLISHED", unpublished.stderr)
        self.assertEqual(self.records(), [])

        self.jj("abandon", "--quiet", "@-")
        self.jj("git", "fetch", "--quiet")
        self.jj("new", "--quiet", "main@origin")
        (self.checkout / "shared.txt").write_text("local\n")
        (self.upstream / "shared.txt").write_text("upstream\n")
        self.commit_upstream("conflict")
        conflict = self.sync("local")
        self.assertNotEqual(conflict.returncode, 0, conflict.stdout + conflict.stderr)
        self.assertIn("CONFLICT", conflict.stderr)
        self.assertEqual(self.records(), [])

        dirty, dirty_env = self.plain_checkout("dirty")
        (dirty / "shared.txt").write_text("dirty\n")
        dirty_result = self.sync("local", env=dirty_env)
        self.assertNotEqual(dirty_result.returncode, 0, dirty_result.stdout + dirty_result.stderr)
        self.assertIn("DIRTY", dirty_result.stderr)
        self.assertEqual(self.records(), [])

        local, local_env = self.plain_checkout("local")
        (local / "local.txt").write_text("local\n")
        committed = subprocess.run(
            ["git", "add", "local.txt"], cwd=local, env=local_env, capture_output=True, text=True, timeout=HANG_GUARD
        )
        self.assertEqual(committed.returncode, 0, committed.stderr)
        committed = subprocess.run(
            ["git", "commit", "-q", "-m", "local"], cwd=local, env=local_env, capture_output=True, text=True, timeout=HANG_GUARD
        )
        self.assertEqual(committed.returncode, 0, committed.stderr)
        local_result = self.sync("local", env=local_env)
        self.assertNotEqual(local_result.returncode, 0, local_result.stdout + local_result.stderr)
        self.assertIn("LOCAL COMMITS", local_result.stderr)
        self.assertEqual(self.records(), [])

    def test_case_07_here_is_move_only(self) -> None:
        result = self.sync("--here", "")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.records(), [])

    def test_case_08_failed_mise_stops_before_bun(self) -> None:
        result = self.sync("local", env={**self.env, "FAKE_MISE_RC": "7"})

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`mise install`", result.stderr)
        self.assertIn("exited 7", result.stderr)
        self.assertIn("mise ERROR forced failure", result.stderr)
        self.assertEqual(len(self.calls("mise")), 1, self.records())
        self.assertEqual(self.calls("bun"), [])
        self.assertEqual(
            self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "main@origin", "--no-graph", "-T", "commit_id"),
        )

    def test_case_09_failed_bun_is_reported(self) -> None:
        result = self.sync("local", env={**self.env, "FAKE_BUN_RC": "8"})

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`bun install --frozen-lockfile`", result.stderr)
        self.assertIn("exited 8", result.stderr)
        self.assertIn("bun ERROR forced failure", result.stderr)
        self.assertEqual(len(self.calls("mise")), 1, self.records())
        self.assertEqual(len(self.calls("bun")), 1, self.records())

    def test_case_10_mise_and_bun_timeouts_leave_the_move(self) -> None:
        # The limit under test is DOTFILES_TOOLS_TIMEOUT=2. The stand-in's sleep outlives its shell,
        # so the step also waits out its 10 s grace before it kills the group. A run that returns
        # before the 600 s install would end shows the limit worked; HANG_GUARD only stops a stuck run.
        started = time.monotonic()
        mise = self.sync(
            "local", env={**self.env, "DOTFILES_TOOLS_TIMEOUT": "2", "FAKE_MISE_SLEEP": "600"}
        )
        self.assertLess(time.monotonic() - started, HANG_GUARD)
        self.assertNotEqual(mise.returncode, 0, mise.stdout + mise.stderr)
        self.assertIn("`mise install`", mise.stderr)
        self.assertIn("exited 124", mise.stderr)
        self.assertIn("past its limit of 2 s", mise.stderr)
        self.assertEqual(
            self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "main@origin", "--no-graph", "-T", "commit_id"),
        )

        self.clear_records()
        started = time.monotonic()
        bun = self.sync(
            "local", env={**self.env, "DOTFILES_TOOLS_TIMEOUT": "2", "FAKE_BUN_SLEEP": "600"}
        )
        self.assertLess(time.monotonic() - started, HANG_GUARD)
        self.assertNotEqual(bun.returncode, 0, bun.stdout + bun.stderr)
        self.assertIn("`bun install --frozen-lockfile`", bun.stderr)
        self.assertIn("exited 124", bun.stderr)
        self.assertIn("past its limit of 2 s", bun.stderr)

    def test_case_11_host_loop_continues_after_a_failure(self) -> None:
        self._install_ssh()
        result = self.sync("firsthost", "secondhost", env={**self.env, "FAKE_FAIL_HOST": "firsthost"})

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        mise_hosts = [line.split("|")[-1] for line in self.calls("mise")]
        self.assertEqual(mise_hosts, ["firsthost", "secondhost"])
        self.assertEqual(len(self.calls("bun")), 1, self.records())

    def test_case_12_tools_wait_for_a_released_lock(self) -> None:
        lock_dir = self.home / ".mise"
        lock_dir.mkdir()
        lock_file = lock_dir / ".dotfiles-install.lock"
        held = lock_file.open("w")
        fcntl.flock(held, fcntl.LOCK_EX)

        def release() -> None:
            time.sleep(1)
            fcntl.flock(held, fcntl.LOCK_UN)
            held.close()

        releaser = threading.Thread(target=release)
        releaser.start()
        started = time.monotonic()
        result = self.sync("local", env={**self.env, "DOTFILES_TOOLS_TIMEOUT": "5"})
        releaser.join()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        self.assertEqual(self.calls("mise")[0].split("|")[4], "held")

    def test_case_13_tools_timeout_waiting_for_a_lock(self) -> None:
        lock_dir = self.home / ".mise"
        lock_dir.mkdir()
        lock_file = lock_dir / ".dotfiles-install.lock"
        held = lock_file.open("w")
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            started = time.monotonic()
            # The lock stays held until the run returns, so only the 2 s limit under test can end
            # the wait; HANG_GUARD stops the run if it does not.
            result = self.sync("local", env={**self.env, "DOTFILES_TOOLS_TIMEOUT": "2"})
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)
            held.close()

        self.assertLess(time.monotonic() - started, HANG_GUARD)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`mise install`", result.stderr)
        self.assertIn("exited 124", result.stderr)
        self.assertEqual(self.calls("mise"), [])

    def test_case_14_install_timeout_keeps_the_lock_through_the_grace_then_kills_the_child(self) -> None:
        # At the limit `timeout` ends flock at once; mise, which inherited flock's locked descriptor
        # (no -o), keeps the lock through the 10 s grace, so no second install starts beside a
        # survivor. After the grace the step kills it, and the lock is free when the step returns.
        pid_file = self.root / "mise.pid"
        lock = self.home / ".mise" / ".dotfiles-install.lock"
        sync = subprocess.Popen(
            ["bash", str(SYNC), "local"],
            env={
                **self.env,
                "DOTFILES_TOOLS_TIMEOUT": "2",
                "FAKE_MISE_SLEEP": "60",
                "FAKE_MISE_IGNORE_TERM": "1",
                "FAKE_PID_FILE": str(pid_file),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        pid = None
        try:
            # Reaching the install step is not under test; the grace after it starts is.
            deadline = time.monotonic() + HANG_GUARD
            while time.monotonic() < deadline:
                if pid_file.exists() and pid_file.read_text().strip():
                    break
                time.sleep(0.1)
            pid = int(pid_file.read_text().strip())
            # The limit fires 2 s after the install starts and the kill 10 s after that; 4 s in is
            # inside the grace with 2 s of margin on one side and 8 s on the other.
            time.sleep(4)
            self.assertTrue(Path(f"/proc/{pid}").exists(), "the survivor was killed before the grace ended")
            during = subprocess.run(["flock", "-n", "-E", "75", str(lock), "true"], capture_output=True, text=True, timeout=HANG_GUARD)
            self.assertEqual(during.returncode, 75, "the lock was free while the survivor still ran")
            stdout, stderr = sync.communicate(timeout=HANG_GUARD)
            self.assertNotEqual(sync.returncode, 0, stdout + stderr)
            self.assertIn("exited 124", stderr)
            self.assertFalse(Path(f"/proc/{pid}").exists(), f"survivor {pid} remains")
            after = subprocess.run(["flock", "-n", str(lock), "true"], capture_output=True, text=True, timeout=HANG_GUARD)
            self.assertEqual(after.returncode, 0, after.stderr)
        finally:
            if sync.poll() is None:
                sync.kill()
                sync.communicate()
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                for _ in range(30):
                    if not Path(f"/proc/{pid}").exists():
                        break
                    time.sleep(0.1)

    def test_case_15_invalid_tools_limits_warn_and_keep_default(self) -> None:
        for value in ("0", "abc", "9999"):
            with self.subTest(value=value):
                self.clear_records()
                result = self.sync("local", env={**self.env, "DOTFILES_TOOLS_TIMEOUT": value})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr.count("DOTFILES_TOOLS_TIMEOUT"), 1, result.stderr)
                self.assert_tools_ran()

    def test_case_16_without_a_plugin_manifest_skips_bun(self) -> None:
        (self.upstream / "omp" / "plugins" / "package.json").unlink()
        self.commit_upstream("remove plugins")
        result = self.sync("local")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.calls("mise")), 1, self.records())
        self.assertEqual(self.calls("bun"), [])

    def test_case_17_publish_rebase_runs_tools_after_a_successful_push(self) -> None:
        change = self.local_commit()
        result = self.publish(change, env={**self.env, "ORIGIN_GIT": str(self.origin)})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()
        pushed = self.run_ok("git", "rev-parse", "main", cwd=self.origin).strip()
        self.assertEqual(self.calls("mise")[0].split("|")[5], pushed)

    def test_case_17b_publish_whose_push_succeeds_exits_non_zero_when_the_step_fails(self) -> None:
        # R5: a failed install makes the run exit non-zero; the push stays.
        change = self.local_commit()
        result = self.publish(change, env={**self.env, "FAKE_MISE_RC": "1"})

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("`mise install`", result.stderr)
        self.assertIn("mise ERROR forced failure", result.stderr)
        self.assertEqual(self.calls("bun"), [], self.records())
        pushed = self.run_ok("git", "show", "main:mine.txt", cwd=self.origin)
        self.assertEqual(pushed, "mine\n")

    def test_case_18_publish_refused_push_runs_tools_and_keeps_failure(self) -> None:
        change = self.local_commit()
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n")
        hook.chmod(0o755)
        result = self.publish(change, env={**self.env, "DOTFILES_PUBLISH_TRIES": "1"})

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_tools_ran()

    def test_case_19_publish_later_failure_runs_tools_and_keeps_failure(self) -> None:
        change = self.local_commit()
        wrapper_dir = self.root / "jj-wrapper"
        wrapper_dir.mkdir()
        real_jj = self.root / "bin" / "jj"
        wrapper = wrapper_dir / "jj"
        wrapper.write_text(
            f"#!/usr/bin/env bash\nif [[ ${{1:-}} = rebase ]]; then\n  {real_jj} \"$@\"\n  exit 1\nfi\nexec {real_jj} \"$@\"\n"
        )
        wrapper.chmod(0o755)
        result = self.publish(change, env={**self.env, "PATH": f"{wrapper_dir}:{self.env['PATH']}"})

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_tools_ran()

    def test_case_20_publish_copy_and_no_op_do_not_run_tools(self) -> None:
        change = self.local_commit("other.txt")
        (self.checkout / "wip.txt").write_text("local working copy\n")
        (self.upstream / "wip.txt").write_text("upstream\n")
        self.commit_upstream("working copy conflict")
        copied = self.publish(change)
        self.assertEqual(copied.returncode, 0, copied.stdout + copied.stderr)
        self.assertEqual(self.records(), [])

        no_op = self.publish(self.tools_commit)
        self.assertEqual(no_op.returncode, 0, no_op.stdout + no_op.stderr)
        self.assertIn("already has", no_op.stdout)
        self.assertEqual(self.records(), [])

    def test_case_21_relative_publish_path_resolves_tools_before_chdir(self) -> None:
        change = self.local_commit()
        env = {**self.env, "DOTFILES_DIR": str(self.checkout)}
        result = subprocess.run(
            ["bash", "scripts/dotfiles-publish", change],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=HANG_GUARD,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()

    def test_case_21a_publish_from_home_with_relative_dotfiles_dir_runs_tools_after_rebase(self) -> None:
        change = self.local_commit()
        (self.upstream / "upstream.txt").write_text("upstream\n")
        self.commit_upstream("upstream")

        result = self.publish(
            change,
            cwd=self.home,
            env={**self.env, "DOTFILES_DIR": ".dotfiles", "ORIGIN_GIT": str(self.origin)},
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()

    def test_case_22_publish_uses_checkout_mise_without_path_or_mise_environment(self) -> None:
        minimal_bin = self.root / "without-mise"
        minimal_bin.mkdir()
        for name in ("git", "jj", "bun", "gh"):
            (minimal_bin / name).symlink_to(self.root / "bin" / name)
        self.assertFalse((minimal_bin / "mise").exists())
        env = {key: value for key, value in self.env.items() if not key.startswith("MISE_")}
        env["PATH"] = f"{minimal_bin}:/usr/bin:/bin"
        change = self.local_commit()
        (self.upstream / "upstream.txt").write_text("upstream\n")
        self.commit_upstream("upstream")
        result = self.publish(change, env=env)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()
        mise = self.calls("mise")[0].split("|")
        self.assertEqual(mise[:4], ["mise install", str(self.checkout), str(self.home / ".mise"), str(self.home / ".cache/mise")])

    def test_case_23_tools_mode_installs_in_the_named_checkout_from_any_directory(self) -> None:
        # `--tools DIR` names the checkout the step installs. Run from another directory, the
        # step must still run `mise install` in DIR: mise reads the directory's mise.toml, so an
        # install run elsewhere reports "passed in DIR" while DIR's pins stay missing.
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        result = self._run(SYNC, "--tools", str(self.checkout), cwd=elsewhere)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()
        self.assertEqual(self.calls("mise")[0].split("|")[1], str(self.checkout), self.records())

    def test_case_23a_tools_mode_falls_back_from_an_unusable_tmpdir(self) -> None:
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        missing_tmp = self.root / "missing-tmp"
        result = self._run(SYNC, "--tools", str(self.checkout), env={**self.env, "TMPDIR": str(missing_tmp)})

        self.assertFalse(missing_tmp.exists())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()
        self.assertIn(f"tools: `mise install` passed in {self.checkout}", result.stdout)
        self.assertIn(f"tools: `bun install --frozen-lockfile` passed in {self.checkout}/omp/plugins", result.stdout)

    def test_case_23e_a_failure_after_the_tmp_fallback_shows_the_last_lines_of_its_output(self) -> None:
        # The /tmp fallback keeps the output in a file, so a failure reports its last lines rather
        # than "its output is above".
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        result = self._run(
            SYNC, "--tools", str(self.checkout),
            env={**self.env, "TMPDIR": str(self.root / "missing-tmp"), "FAKE_MISE_RC": "7"},
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("exited 7", result.stderr)
        self.assertIn("last lines of its output:\nmise ERROR forced failure", result.stderr)
        self.assertNotIn("no temporary file could be made", result.stderr)

    def test_case_23f_tools_mode_with_a_relative_tmpdir_runs_the_step(self) -> None:
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        (self.root / "rel-tmp").mkdir()
        result = self._run(SYNC, "--tools", str(self.checkout), cwd=self.root, env={**self.env, "TMPDIR": "rel-tmp"})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()
        self.assertEqual(list((self.root / "rel-tmp").iterdir()), [])

    def test_case_23b_tools_mode_runs_when_no_temporary_file_can_be_made(self) -> None:
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        self._write_failing_mktemp(self.checkout / "bin" / "mktemp")

        result = self._run(SYNC, "--tools", str(self.checkout))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no temporary file could be made", result.stderr)
        self.assertIn(f"tools: `mise install` passed in {self.checkout}", result.stdout)
        self.assertIn(f"tools: `bun install --frozen-lockfile` passed in {self.checkout}/omp/plugins", result.stdout)
        self.assert_tools_ran()

    def test_case_23c_tools_mode_reports_a_failure_when_no_temporary_file_can_be_made(self) -> None:
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        self._write_failing_mktemp(self.checkout / "bin" / "mktemp")

        result = self._run(SYNC, "--tools", str(self.checkout), env={**self.env, "FAKE_MISE_RC": "7"})

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`mise install`", result.stderr)
        self.assertIn("exited 7", result.stderr)
        self.assertIn("mise ERROR forced failure", result.stderr)
        self.assertIn("its output is above on stderr", result.stderr)
        self.assertEqual(len(self.calls("mise")), 1, self.records())
        self.assertEqual(self.calls("bun"), [], self.records())

    @unittest.skipIf(os.geteuid() == 0, "root enters a mode-000 directory")
    def test_case_23d_tools_mode_uses_tmp_after_a_mode_000_tmpdir(self) -> None:
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        blocked_tmp = self.root / "blocked-tmp"
        blocked_tmp.mkdir()
        blocked_tmp.chmod(0)
        self.addCleanup(lambda: blocked_tmp.chmod(0o700) if blocked_tmp.exists() else None)

        result = self._run(SYNC, "--tools", str(self.checkout), env={**self.env, "TMPDIR": str(blocked_tmp)})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()

    def test_case_24_the_sync_lock_is_free_while_the_tools_step_installs(self) -> None:
        # A long install must not hold the sync lock: dotfiles-follow moves the checkout with
        # `--here`, which waits for that lock and reports a held one as a failed sync.
        common = self.run_ok("git", "rev-parse", "--git-common-dir", cwd=self.checkout).strip()
        sync_lock = self.checkout / common / "dotfiles-sync.lock"
        probe = self.root / "sync-lock-probe"
        (self.checkout / "bin" / "mise").write_text(
            "#!/usr/bin/env bash\n"
            f'if flock -n -E 75 {sync_lock} true; then echo free; else rc=$?; case "$rc" in 75) echo held ;; *) echo probe-error-$rc ;; esac; fi > {probe}\n'
        )
        result = self.sync("local")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(probe.read_text(), "free\n")

    def test_case_25_tools_mode_takes_a_relative_checkout(self) -> None:
        # The step enters DIR before `mise install`, so a relative DIR must be made absolute
        # first, or DIR/bin/mise no longer names the checkout's mise once the step is in DIR.
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        result = self._run(SYNC, "--tools", ".dotfiles", cwd=self.home)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()
        self.assertEqual(self.calls("mise")[0].split("|")[1], str(self.checkout), self.records())
        self.assertIn(f"tools: `mise install` passed in {self.checkout}\n", result.stdout)

    def test_case_26_relative_publish_path_with_cdpath_still_runs_the_tools_step(self) -> None:
        # With CDPATH exported, `cd` of a relative name prints the directory it enters; a `$(cd …)`
        # capture of the script's own directory would take that line too and name no dotfiles-sync.
        change = self.local_commit()
        env = {**self.env, "DOTFILES_DIR": str(self.checkout), "CDPATH": "."}
        result = subprocess.run(
            ["bash", "scripts/dotfiles-publish", change],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=HANG_GUARD,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()

    def test_case_27_tools_mode_with_cdpath_takes_a_relative_checkout(self) -> None:
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        result = self._run(SYNC, "--tools", ".dotfiles", cwd=self.home, env={**self.env, "CDPATH": "."})

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()
        self.assertEqual(self.calls("mise")[0].split("|")[1], str(self.checkout), self.records())
        self.assertIn(f"tools: `mise install` passed in {self.checkout}\n", result.stdout)
    def test_case_28_relative_mise_data_dir_uses_one_absolute_lock_directory(self) -> None:
        (self.upstream / ".bashrc").write_text(
            'export MISE_DATA_DIR="${MISE_DATA_DIR:-$HOME/.mise}"\n'
            'export MISE_CACHE_DIR="$HOME/.cache/mise"\n'
            'export MISE_TRUSTED_CONFIG_PATHS="$HOME/.dotfiles"\n'
            'export PATH="$HOME/.dotfiles/bin:$PATH"\n'
        )
        self.commit_upstream("preserve caller mise data")
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        caller = self.root / "relative-mise-caller"
        caller.mkdir()

        result = self._run(
            SYNC,
            "--tools",
            str(self.checkout),
            cwd=caller,
            env={**self.env, "MISE_DATA_DIR": "rel"},
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        mise = self.calls("mise")[0].split("|")
        self.assertEqual(mise[2], str(caller / "rel"), self.records())
        self.assertEqual(mise[4], "held", self.records())

    @unittest.skipIf(os.geteuid() == 0, "root enters a mode-000 directory")
    def test_case_28a_tools_mode_reports_the_flock_error_for_an_inaccessible_mise_data_dir(self) -> None:
        (self.upstream / ".bashrc").write_text(
            'export MISE_DATA_DIR="${MISE_DATA_DIR:-$HOME/.mise}"\n'
            'export MISE_CACHE_DIR="$HOME/.cache/mise"\n'
            'export MISE_TRUSTED_CONFIG_PATHS="$HOME/.dotfiles"\n'
            'export PATH="$HOME/.dotfiles/bin:$PATH"\n'
        )
        self.commit_upstream("preserve inaccessible mise data")
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        blocked_data = self.root / "blocked-mise-data"
        blocked_data.mkdir()
        blocked_data.chmod(0)
        self.addCleanup(lambda: blocked_data.chmod(0o700) if blocked_data.exists() else None)

        result = self._run(
            SYNC,
            "--tools",
            str(self.checkout),
            env={**self.env, "MISE_DATA_DIR": str(blocked_data)},
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`mise install`", result.stderr)
        self.assertIn("exited ", result.stderr)
        self.assertIn("flock:", result.stderr)

    def test_case_29_relative_bun_path_is_absolute_before_the_plugins_cd(self) -> None:
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        relative_bun = self.root / "relative-bun"
        relative_bun.mkdir()
        (relative_bun / "bun").symlink_to(self.root / "bin" / "bun")
        safe = self.root / "safe"
        safe.mkdir()
        for name in ("bash", "flock", "mkdir", "mktemp", "rm", "sleep", "tail", "timeout"):
            source = next(
                (
                    directory / name
                    for directory in (Path("/usr/bin"), Path("/bin"))
                    if (directory / name).exists()
                ),
                None,
            )
            if source is None:
                self.fail(f"required command is absent: {name}")
            (safe / name).symlink_to(source)

        result = self._run(
            SYNC,
            "--tools",
            str(self.checkout),
            cwd=self.root,
            env={**self.env, "PATH": f"relative-bun:{safe}"},
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()

    def test_case_30_missing_bun_reports_the_tools_error_shape(self) -> None:
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()

        without_bun = self.root / "without-bun"
        without_bun.mkdir()
        for name in ("bash", "flock", "mkdir", "mktemp", "rm", "sleep", "tail", "timeout"):
            source = next(
                (
                    directory / name
                    for directory in (Path("/usr/bin"), Path("/bin"))
                    if (directory / name).exists()
                ),
                None,
            )
            if source is None:
                self.fail(f"required command is absent: {name}")
            (without_bun / name).symlink_to(source)

        result = self._run(
            SYNC,
            "--tools",
            str(self.checkout),
            cwd=self.root,
            env={**self.env, "PATH": str(without_bun), "TMPDIR": str(self.root / "missing-tmp")},
        )

        self.assertEqual(result.returncode, 127, result.stdout + result.stderr)
        self.assertIn("`bun install --frozen-lockfile`", result.stderr)
        self.assertIn("exited 127", result.stderr)
        self.assertIn("bun: command not found", result.stderr)
        self.assertEqual(len(self.calls("mise")), 1, self.records())
        self.assertEqual(self.calls("bun"), [], self.records())

    def test_case_30a_missing_mise_reports_tools_error_with_an_unusable_tmpdir(self) -> None:
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        (self.checkout / "bin" / "mise").rename(self.checkout / "bin" / "mise-hidden")
        without_mise = self.root / "without-mise"
        without_mise.mkdir()
        for name in ("bash", "flock", "mkdir", "mktemp", "rm", "sleep", "tail", "timeout"):
            source = next(
                (
                    directory / name
                    for directory in (Path("/usr/bin"), Path("/bin"))
                    if (directory / name).exists()
                ),
                None,
            )
            if source is None:
                self.fail(f"required command is absent: {name}")
            (without_mise / name).symlink_to(source)

        result = self._run(
            SYNC,
            "--tools",
            str(self.checkout),
            cwd=self.root,
            env={**self.env, "PATH": str(without_mise), "TMPDIR": str(self.root / "missing-tmp")},
        )

        self.assertEqual(result.returncode, 127, result.stdout + result.stderr)
        self.assertIn("`mise install`", result.stderr)
        self.assertIn("exited 127", result.stderr)
        self.assertIn("mise: command not found", result.stderr)
        self.assertEqual(self.calls("mise"), [], self.records())
        self.assertEqual(self.calls("bun"), [], self.records())

    def test_case_31_mise_from_a_relative_path_entry_is_absolute_before_the_checkout_cd(self) -> None:
        # With no bin/mise in the checkout the step takes mise from PATH, which can be a relative
        # entry of the caller's directory; the runner enters the checkout before it runs it.
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        relative_mise = self.root / "relative-mise"
        relative_mise.mkdir()
        (self.checkout / "bin" / "mise").rename(relative_mise / "mise")
        result = self._run(
            SYNC,
            "--tools",
            str(self.checkout),
            cwd=self.root,
            env={**self.env, "PATH": f"relative-mise:{self.root / 'bin'}:/usr/bin:/bin"},
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()
        self.assertEqual(self.calls("mise")[0].split("|")[1], str(self.checkout), self.records())

    def test_case_32_a_checkout_without_the_registry_render_runs_mise_and_bun_alone(self) -> None:
        # A checkout on a main older than scripts/knives-registry-render, as every fixture above
        # is: the step skips the render, with no line about it, and passes.
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.clear_records()
        self.assertFalse((self.checkout / "scripts" / "knives-registry-render").exists())
        result = self._run(SYNC, "--tools", str(self.checkout))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()
        self.assertNotIn("knives-registry-render", result.stdout + result.stderr)
        self.assertFalse((self.home / ".config" / "knives").exists())

    def with_rule_linker(self, rule: str = "new-rule.md") -> None:
        """Main gains the real omp-link-rules, the installer helpers it sources, and one rule."""
        for path in ("scripts/omp-link-rules", "installers/lib.sh"):
            target = self.upstream / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / path).read_bytes())
            target.chmod((ROOT / path).stat().st_mode)
        (self.upstream / "omp" / "rules").mkdir(parents=True, exist_ok=True)
        (self.upstream / "omp" / "rules" / rule).write_text("---\nalwaysApply: true\n---\nA rule.\n")
        self.commit_upstream("a new omp rule")

    def test_case_32_a_rule_the_move_brings_is_linked_into_omp(self) -> None:
        # With dotfiles-follow retired on sami-devbox, dotfiles-sync is what moves the checkout,
        # and the rule of dotfiles#133 (omp/rules/codegraph.md) stayed dark until linked by hand.
        self.with_rule_linker()
        result = self.sync("local")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        link = self.home / ".omp" / "agent" / "rules" / "new-rule.md"
        self.assertTrue(link.is_symlink(), result.stdout + result.stderr)
        self.assertEqual(os.readlink(link), str(self.checkout / "omp" / "rules" / "new-rule.md"))
        self.assertIn(f"tools: `omp-link-rules` passed in {self.checkout}\n", result.stdout)
        self.assert_tools_ran()

    def test_case_32a_a_failed_rule_link_is_reported_and_the_installs_still_run(self) -> None:
        self.with_rule_linker()
        rules = self.home / ".omp" / "agent" / "rules"
        rules.mkdir(parents=True)
        (rules / "new-rule.md").write_text("an edit made in place\n")
        result = self.sync("local")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`omp-link-rules`", result.stderr)
        self.assertIn("is a file that differs", result.stderr)
        self.assertFalse((rules / "new-rule.md").is_symlink())
        self.assert_tools_ran()

    def test_case_32b_a_checkout_that_is_not_home_dotfiles_links_no_rule(self) -> None:
        # A link made from another clone would point every session's rules at that clone.
        self.with_rule_linker()
        clone = self.root / "clone"
        self.run_ok("git", "clone", "-q", str(self.origin), str(clone))
        self._write_standin("mise", clone / "bin" / "mise")
        result = self._run(SYNC, "--tools", str(clone))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"tools: {clone} is not ~/.dotfiles; omp rules not linked\n", result.stdout)
        self.assertFalse((self.home / ".omp" / "agent" / "rules" / "new-rule.md").exists())
        self.assert_tools_ran()


RENDER = "knives-registry-render --if-changed"


class KnivesRegistry(RegistryCopy, ToolsStep):
    """sami-rocqperf, sami-leanperf and the laptop run no dotfiles-follow timer and move only
    through dotfiles-sync and dotfiles-publish, so the tools step starts with the real
    scripts/knives-registry-render --if-changed: the machine's copy of the knives registry,
    ~/.config/knives/repos.toml, follows the committed knives/repos.toml with no person's command
    (dispatch://INFRA-3365, R5). The copy the render must leave is worked out by RegistryCopy
    (test_dotfiles_follow), never by running the render."""

    def setUp(self) -> None:
        super().setUp()
        add_registry_render(self.upstream)
        self.commit_registry(REGISTRY_V1)

    def commit_registry(self, source: str) -> None:
        path = self.upstream / "knives" / "repos.toml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(source)
        self.commit_upstream("knives registry")

    @staticmethod
    def render_passed(checkout: Path) -> str:
        return f"tools: `{RENDER}` passed in {checkout}\n"

    def assert_on_main(self) -> None:
        self.assertEqual(
            self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "main@origin", "--no-graph", "-T", "commit_id"),
        )

    def assert_render_failed(self, result: subprocess.CompletedProcess[str]) -> None:
        """The step's report of a render that could not write into a read-only ~/.config/knives."""
        self.assertIn(f"dotfiles-sync: tools step: `{RENDER}` in {self.checkout} exited 1\nlast lines of its output:\n", result.stderr)
        # mktemp quotes the template as the locale does ('...' or ‘...’): only the words are checked.
        failure = next((line for line in result.stderr.splitlines() if "mktemp: failed to create file via template" in line), "")
        self.assertIn(f"{self.copy}.XXXXXX", failure)
        self.assertTrue(failure.endswith(": Permission denied"), result.stderr)
        self.assertNotIn(self.render_passed(self.checkout), result.stdout)
        self.assertEqual(self.copy.read_text(), self.rendered(REGISTRY_V1))

    @contextlib.contextmanager
    def read_only_copy_directory(self) -> Iterator[None]:
        knives = self.copy.parent
        knives.chmod(0o555)
        try:
            yield
        finally:
            knives.chmod(0o755)

    def test_a_sync_over_a_registry_change_writes_the_copy_before_the_installs(self) -> None:
        first = self.sync("local")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assert_copy_renders(REGISTRY_V1)
        before = self.identity(self.copy)
        self.commit_registry(REGISTRY_V2)
        self.clear_records()

        result = self.sync("local")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_copy_renders(REGISTRY_V2)
        self.assertNotEqual(self.identity(self.copy), before)
        self.assertIn(self.render_passed(self.checkout), result.stdout)
        self.assertLess(
            result.stdout.index(self.render_passed(self.checkout)),
            result.stdout.index(f"tools: `mise install` passed in {self.checkout}\n"),
        )
        self.assert_tools_ran()

    def test_a_second_sync_writes_nothing(self) -> None:
        first = self.sync("local")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assert_copy_renders(REGISTRY_V1)
        written = (self.identity(self.copy), self.identity(self.render_record))
        self.clear_records()

        again = self.sync("local")

        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn(self.render_passed(self.checkout), again.stdout)
        self.assertEqual((self.identity(self.copy), self.identity(self.render_record)), written)
        self.assert_tools_ran()

    def test_tools_mode_after_a_pull_that_changes_the_registry_writes_the_copy(self) -> None:
        checkout, env = self.plain_checkout("plain")
        home = Path(env["HOME"])
        first = self._run(SYNC, "--tools", str(checkout), env=env)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assert_copy_renders(REGISTRY_V1, home)
        self.commit_registry(REGISTRY_V2)
        self.run_ok("git", "pull", "-q", "--ff-only", cwd=checkout)
        self.clear_records()

        result = self._run(SYNC, "--tools", str(checkout), env=env)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(self.render_passed(checkout), result.stdout)
        self.assert_copy_renders(REGISTRY_V2, home)
        self.assert_tools_ran()

    def other_clone(self) -> Path:
        """A clone of origin that is not ~/.dotfiles, whose knives/repos.toml (REGISTRY_V2) differs
        from the one ~/.dotfiles holds (REGISTRY_V1), so a copy rendered from it would show."""
        clone = self.root / "clone"
        self.run_ok("git", "clone", "-q", str(self.origin), str(clone))
        (clone / "knives" / "repos.toml").write_text(REGISTRY_V2)
        self._write_standin("mise", clone / "bin" / "mise")
        return clone

    def test_tools_mode_in_a_checkout_that_is_not_home_dotfiles_writes_no_copy(self) -> None:
        # The copy is what scripts/leak-terms-check reads, so only ~/.dotfiles writes it, as only
        # ~/.dotfiles links the omp rules; a publish from another clone runs `--tools` there. That
        # clone writes no copy when there is none yet, leaves the one ~/.dotfiles wrote as it was,
        # whichever dotfiles-sync runs, and writes none in a HOME with no ~/.dotfiles. mise and Bun
        # still run, and the step passes.
        clone = self.other_clone()
        skipped = f"tools: {clone} is not ~/.dotfiles; knives registry copy not written\n"

        def refused(result: subprocess.CompletedProcess[str]) -> None:
            """The step passed, said it wrote no copy, ran no render, and ran mise and Bun."""
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(skipped, result.stdout)
            self.assertNotIn(RENDER, result.stdout + result.stderr)
            self.assert_tools_ran()

        with self.subTest(copy="none yet"):
            result = self._run(SYNC, "--tools", str(clone))
            self.assertFalse(self.copy.exists(), result.stdout)
            refused(result)
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.assert_copy_renders(REGISTRY_V1)
        written = (self.copy.read_text(), self.identity(self.copy), self.identity(self.render_record))
        own = self.checkout / "scripts" / "dotfiles-sync"
        own.write_bytes(SYNC.read_bytes())
        for name, script in (("this checkout's dotfiles-sync", SYNC), ("~/.dotfiles's own dotfiles-sync", own)):
            with self.subTest(copy="written by ~/.dotfiles", script=name):
                self.clear_records()
                result = self._run(script, "--tools", str(clone))
                self.assertEqual((self.copy.read_text(), self.identity(self.copy), self.identity(self.render_record)), written)
                refused(result)
        with self.subTest(home="no ~/.dotfiles"):
            bare = self.root / "bare-home"
            bare.mkdir()
            self.clear_records()
            result = self._run(SYNC, "--tools", str(clone), env={**self.env, "HOME": str(bare)})
            self.assertFalse((bare / REGISTRY_COPY).exists(), result.stdout)
            refused(result)

    def test_tools_mode_in_home_dotfiles_writes_a_stale_copy_by_any_spelling_of_its_path(self) -> None:
        # The control of the case above: `--tools` writes the copy for ~/.dotfiles named as
        # $HOME/.dotfiles, $HOME/./.dotfiles or $HOME//.dotfiles/, and, with ~/.dotfiles a symlink,
        # by its real path or as ~/.dotfiles. A copy that holds the render's bytes already gets the
        # record alone, so each row first gives the copy other bytes and removes the record.
        moved = self.sync("local")
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.assert_copy_renders(REGISTRY_V1)

        def writes(named: str, entered: Path) -> None:
            """`--tools NAMED` renders over a stale copy; the step names the checkout as ENTERED."""
            self.copy.write_text("# stale\n")
            self.render_record.unlink(missing_ok=True)
            stale = self.identity(self.copy)
            self.clear_records()
            result = self._run(SYNC, "--tools", named)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(self.render_passed(entered), result.stdout)
            self.assertNotIn("knives registry copy not written", result.stdout)
            self.assert_copy_renders(REGISTRY_V1)
            self.assertNotEqual(self.identity(self.copy), stale)
            self.assert_tools_ran()

        for named in (str(self.checkout), f"{self.home}/./.dotfiles", f"{self.home}//.dotfiles/"):
            with self.subTest(named=named):
                writes(named, self.checkout)
        real = self.root / "real-dotfiles"
        self.checkout.rename(real)
        self.checkout.symlink_to(real)
        for named, entered in ((str(real), real), (str(self.checkout), self.checkout)):
            with self.subTest(symlinked_home_dotfiles=named):
                writes(named, entered)

    def test_a_publish_whose_rebase_brings_the_registry_writes_the_copy(self) -> None:
        # dotfiles-publish runs `dotfiles-sync --tools` after the rebase moved the files.
        change = self.local_commit()
        result = self.publish(change)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(self.render_passed(self.checkout), result.stdout)
        self.assert_copy_renders(REGISTRY_V1)
        self.assert_tools_ran()

    def test_here_moves_and_writes_no_copy(self) -> None:
        # dotfiles-follow renders through its own step after `--here`.
        result = self.sync("--here", "")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.checkout / "knives" / "repos.toml").read_text(), REGISTRY_V1)
        self.assertFalse(self.copy.exists())
        self.assertEqual(self.records(), [])

    @unittest.skipIf(os.geteuid() == 0, "root writes into a read-only directory")
    def test_a_render_that_cannot_write_keeps_the_move_and_the_installs_and_fails_the_run(self) -> None:
        first = self.sync("local")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.commit_registry(REGISTRY_V2)
        self.clear_records()

        with self.read_only_copy_directory():
            result = self.sync("local")

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assert_on_main()
            self.assertEqual((self.checkout / "knives" / "repos.toml").read_text(), REGISTRY_V2)
            self.assert_tools_ran()
            self.assert_render_failed(result)

            # The step alone returns the render's status once mise and Bun passed.
            self.clear_records()
            tools = self._run(SYNC, "--tools", str(self.checkout))
            self.assertEqual(tools.returncode, 1, tools.stdout + tools.stderr)
            self.assert_tools_ran()
            self.assert_render_failed(tools)

    @unittest.skipIf(os.geteuid() == 0, "root writes into a read-only directory")
    def test_without_a_plugin_manifest_the_step_still_returns_the_render_failure(self) -> None:
        first = self.sync("local")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        (self.upstream / "omp" / "plugins" / "package.json").unlink()
        self.commit_registry(REGISTRY_V2)
        self.clear_records()

        with self.read_only_copy_directory():
            result = self.sync("local")

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assert_on_main()
            self.assertEqual(len(self.calls("mise")), 1, self.records())
            self.assertEqual(self.calls("bun"), [], self.records())
            self.assert_render_failed(result)


if __name__ == "__main__":
    unittest.main()
