#!/usr/bin/env python3
"""Tests for the tools step that follows dotfiles sync and publish moves.

A move can bring a new mise pin, so it must install the checkout's tools before it returns.
The step uses the checkout .bashrc environment, serializes mise install with the shared lock,
and reports failures or limits without undoing the move. These tests use recording stand-ins to
check the local, remote, publish, refusal, lock, and timeout paths without installing tools.
"""

from __future__ import annotations

import fcntl
import importlib.machinery
import importlib.util
import os
import signal
import subprocess
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYNC = ROOT / "scripts" / "dotfiles-sync"
PUBLISH = ROOT / "scripts" / "dotfiles-publish"
_SYNC_LOADER = importlib.machinery.SourceFileLoader("dotfiles_sync_fixture", str(Path(__file__).with_name("test_dotfiles_sync.py")))
_SYNC_SPEC = importlib.util.spec_from_loader(_SYNC_LOADER.name, _SYNC_LOADER)
assert _SYNC_SPEC is not None
_sync_fixture = importlib.util.module_from_spec(_SYNC_SPEC)
_SYNC_LOADER.exec_module(_sync_fixture)
DotfilesCheckout = _sync_fixture.DotfilesCheckout


class DotfilesTools(DotfilesCheckout):
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
if flock -n "$lock" true; then state=free; else state=held; fi
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

    def _run(self, script: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None, timeout: float = 15) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script), *args],
            cwd=cwd,
            env=env or self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def sync(self, *args: str, env: dict[str, str] | None = None, timeout: float = 15) -> subprocess.CompletedProcess[str]:
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
            ["git", "clone", "-q", str(self.origin), str(checkout)], env=env, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(cloned.returncode, 0, cloned.stderr)
        self._write_standin("mise", checkout / "bin" / "mise")
        return checkout, env

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
            ["git", "add", "local.txt"], cwd=local, env=local_env, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(committed.returncode, 0, committed.stderr)
        committed = subprocess.run(
            ["git", "commit", "-q", "-m", "local"], cwd=local, env=local_env, capture_output=True, text=True, timeout=10
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
        started = time.monotonic()
        mise = self.sync(
            "local", env={**self.env, "DOTFILES_TOOLS_TIMEOUT": "2", "FAKE_MISE_SLEEP": "30"}, timeout=15
        )
        self.assertLess(time.monotonic() - started, 15)
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
            "local", env={**self.env, "DOTFILES_TOOLS_TIMEOUT": "2", "FAKE_BUN_SLEEP": "30"}, timeout=15
        )
        self.assertLess(time.monotonic() - started, 15)
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
            result = self.sync("local", env={**self.env, "DOTFILES_TOOLS_TIMEOUT": "2"}, timeout=15)
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)
            held.close()

        self.assertLess(time.monotonic() - started, 15)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`mise install`", result.stderr)
        self.assertIn("exited 124", result.stderr)
        self.assertEqual(self.calls("mise"), [])

    def test_case_14_install_child_keeps_lock_after_timeout(self) -> None:
        pid_file = self.root / "mise.pid"
        lock = self.home / ".mise" / ".dotfiles-install.lock"
        result = self.sync(
            "local",
            env={
                **self.env,
                "DOTFILES_TOOLS_TIMEOUT": "2",
                "FAKE_MISE_SLEEP": "60",
                "FAKE_MISE_IGNORE_TERM": "1",
                "FAKE_PID_FILE": str(pid_file),
            },
            timeout=15,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("exited 124", result.stderr)
        self.assertTrue(pid_file.exists())
        pid = int(pid_file.read_text().strip())
        try:
            self.assertTrue(Path(f"/proc/{pid}").exists())
            lock_probe = subprocess.run(["flock", "-n", str(lock), "true"], capture_output=True, text=True, timeout=3)
            self.assertNotEqual(lock_probe.returncode, 0)
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            for _ in range(30):
                if not Path(f"/proc/{pid}").exists():
                    break
                time.sleep(0.1)
        released_probe = subprocess.run(["flock", "-n", str(lock), "true"], capture_output=True, text=True, timeout=3)
        self.assertEqual(released_probe.returncode, 0, released_probe.stderr)

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
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_tools_ran()

    def test_case_22_publish_uses_checkout_mise_without_path_or_mise_environment(self) -> None:
        minimal_bin = self.root / "without-mise"
        minimal_bin.mkdir()
        for name in ("git", "jj", "bun"):
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


if __name__ == "__main__":
    unittest.main()
