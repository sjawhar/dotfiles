#!/usr/bin/env python3
"""Tests for dotfiles-follow: it fast-forwards a clean checkout and installs its tools, keeps the
SECRET_FILES copies, and refuses, changing nothing, on every other change.

Each case builds a throwaway origin and a clone of it at $HOME/.dotfiles in a temp dir, with the
real devbox/sami-devbox-provision (so the kept files are read from its SECRET_FILES) and the real
devbox/sami-devbox-check (so the pin lines that run are the check's own), and stand-in `mise` and
`knives` on PATH. Envoy is a fake that records each message. A refusal case prints the alert text
and asserts that HEAD, the index and every file of the checkout are as they were.
"""

import argparse
import fcntl
import gc
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "dotfiles-follow"
LOADER = importlib.machinery.SourceFileLoader("dotfiles_follow", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
FOLLOW = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(FOLLOW)

PIN = "3.2.2"
FAKE_MISE = """#!/bin/sh
# mise install: exits FAKE_MISE_RC; mise ls --missing: lists nothing.
echo "$*" >> "$FAKE_LOG"
case "$1" in
    install)
        if flock -n -E 75 "${MISE_DATA_DIR:-$HOME/.mise}/.dotfiles-install.lock" true; then
            lock=free
        else
            rc=$?
            case "$rc" in
                75) lock=held ;;
                *) lock=probe-error-$rc ;;
            esac
        fi
        echo "install $lock ${MISE_DATA_DIR:-$HOME/.mise}" >> "$FAKE_LOG"
        [ "${FAKE_MISE_RC:-0}" = 0 ] || echo "mise ERROR Failed to install github:sjawhar/knives@3.2.2: HTTP 403" >&2
        exit "${FAKE_MISE_RC:-0}"
        ;;
    ls) exit 0 ;;
esac
exit 0
"""
FAKE_KNIVES = f"""#!/bin/sh
echo "knives {PIN}"
"""
FAKE_BUN = """#!/bin/sh
# bun install --frozen-lockfile: logs its directory and data directory, then exits FAKE_BUN_RC.
echo "bun $* in ${PWD##*/.dotfiles/} data=${MISE_DATA_DIR-unset}" >> "$FAKE_LOG"
[ "${FAKE_BUN_RC:-0}" = 0 ] || echo "error: lockfile had changes, but lockfile is frozen" >&2
exit "${FAKE_BUN_RC:-0}"
"""
PLUGINS_V1 = '{\n  "dependencies": {\n    "@sjawhar/pi-envoy": "15.1.2"\n  }\n}\n'
PLUGINS_V2 = '{\n  "dependencies": {\n    "@sjawhar/pi-envoy": "15.5.0"\n  }\n}\n'
LOCK_V1 = '{ "@sjawhar/pi-envoy": ["@sjawhar/pi-envoy@15.1.2"] }\n'
LOCK_V2 = '{ "@sjawhar/pi-envoy": ["@sjawhar/pi-envoy@15.5.0"] }\n'

# sweep-hooks.sh as the leak-check tag lays it out: settings, a `sweep ROOTS...` function, then the
# endless loop from `i=0`, which dotfiles-follow never runs. FAKE_SWEEP picks the pass's outcome.
FAKE_SWEEP = """#!/usr/bin/env bash
set -uo pipefail
here=$(dirname "$(readlink -f "$0")")
log=$here/sweep-hooks.log
fast_roots=(/fast)
slow_roots=(/slow)
sweep() {
  echo "sweep $* here=$here" >> "$FAKE_LOG.sweep"
  case "${FAKE_SWEEP:-ok}" in
    ok) echo "2026-11-03T00:00:00Z sweep [$*]: 3 checkouts of the four public repos, 0 installed, 0 failed" >> "$log" ;;
    installed) echo "2026-11-03T00:00:00Z INSTALLED /src/new" >> "$log"
      echo "2026-11-03T00:00:00Z sweep [$*]: 3 checkouts of the four public repos, 1 installed, 0 failed" >> "$log" ;;
    failed) echo "2026-11-03T00:00:00Z FAILED /src/broken" >> "$log"
      echo "2026-11-03T00:00:00Z sweep [$*]: 3 checkouts of the four public repos, 0 installed, 1 failed" >> "$log" ;;
    # The sweep of dispatch://BET-706: at most 40 GitHub visibility lookups a pass; a checkout past that is
    # DEFERRED to a later pass and counted in the summary, and is not known to have the hook.
    complete) echo "2026-11-03T00:00:00Z sweep [$*]: 41 checkouts hooked or public or unreadable, 0 deferred, 40 installed, 0 failed" >> "$log" ;;
    deferred) echo "2026-11-03T00:00:00Z INSTALLED /src/one" >> "$log"
      echo "2026-11-03T00:00:00Z DEFERRED /src/later" >> "$log"
      echo "2026-11-03T00:00:00Z sweep [$*]: 41 checkouts hooked or public or unreadable, 1 deferred, 40 installed, 0 failed" >> "$log" ;;
    deferred_count) echo "2026-11-03T00:00:00Z sweep [$*]: 41 checkouts hooked or public or unreadable, 2 deferred, 39 installed, 0 failed" >> "$log" ;;
    hang) sleep 60 ;;
    broken) echo "sweep: cannot walk a root"; exit 3 ;;
    nosummary) echo "2026-11-03T00:00:00Z INSTALLED /src/one" >> "$log" ;;
  esac
}
i=0
while true; do sweep "${fast_roots[@]}"; i=$((i + 1)); sleep 300; done
"""


class FakeEnvoy:
    def __init__(self, timeout: float = 8.0) -> None:
        FakeEnvoy.instances.append(self)
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return True

    def send(self, session_id: str, message: str) -> bool:
        raise AssertionError("the role answered; nothing goes to a session")


class Harness(unittest.TestCase):
    slow_sweep_interval = 1e9

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.origin = root / "origin.git"
        self.upstream = root / "upstream"
        self.repo = self.home / ".dotfiles"
        self.state = root / "state.json"
        self.fake_log = root / "fake.log"
        fakebin = root / "bin"
        fakebin.mkdir()
        for name, body in (("mise", FAKE_MISE), ("knives", FAKE_KNIVES), ("bun", FAKE_BUN)):
            (fakebin / name).write_text(body)
            (fakebin / name).chmod(0o755)
        self.hooks_dir = root / "hooks"
        self.hooks_dir.mkdir()
        (self.hooks_dir / "sweep-hooks.sh").write_text(FAKE_SWEEP)
        (self.hooks_dir / "sweep-hooks.sh").chmod(0o755)
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "BOX_HEALTH", "ENVOY", "MISE_", "DOTFILES_", "XDG_CONFIG", "XDG_STATE"))
        }
        env.update(
            HOME=str(self.home),
            PATH=f"{fakebin}:{os.environ['PATH']}",
            XDG_CONFIG_HOME=str(root / "config"),
            FAKE_LOG=str(self.fake_log),
            FAKE_MISE_RC="0",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(root / "gitconfig"),
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@example.com",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@example.com",
        )
        (root / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
        self.env_patch = unittest.mock.patch.dict(os.environ, env, clear=True)
        self.env_patch.start()
        self.envoy_patch = unittest.mock.patch.object(FOLLOW, "Envoy", FakeEnvoy)
        self.envoy_patch.start()
        FakeEnvoy.instances = []

        self.sh("git", "init", "-q", "--bare", str(self.origin))
        self.sh("git", "clone", "-q", str(self.origin), str(self.upstream))
        (self.upstream / "devbox").mkdir()
        for name in ("sami-devbox-provision", "sami-devbox-check"):
            shutil.copy2(DOTFILES / "devbox" / name, self.upstream / "devbox" / name)
        (self.upstream / "mise.toml").write_text(f'[tools]\n"github:sjawhar/knives" = "{PIN}"\n')
        (self.upstream / "secrets.env").write_text("COMMITTED=1\n")
        (self.upstream / "scripts").mkdir()
        (self.upstream / "scripts" / "tool").write_text("v1\n")
        linker = self.upstream / "scripts/omp-link-rules"
        linker.write_text("#!/bin/sh\nexit 0\n")
        linker.chmod(linker.stat().st_mode | stat.S_IEXEC)
        (self.upstream / ".gitignore").write_text(".secrets/\nomp/*.local.yml\nomp/plugins/node_modules/\n")
        (self.upstream / "omp" / "plugins").mkdir(parents=True)
        (self.upstream / "omp" / "plugins" / "package.json").write_text(PLUGINS_V1)
        (self.upstream / "omp" / "plugins" / "bun.lock").write_text(LOCK_V1)
        self.commit_upstream("initial")
        self.sh("git", "clone", "-q", str(self.origin), str(self.repo))
        # The copy step 2 makes: this machine's secrets.env differs from its commit.
        (self.repo / "secrets.env").write_text("COMMITTED=1\nWORKING_COPY=1\n")
        # Gitignored step-2 files and the separate .secrets repository are not git's business.
        (self.repo / "omp").mkdir(exist_ok=True)
        (self.repo / "omp" / "models.local.yml").write_text("providers: {}\n")
        (self.repo / ".secrets").mkdir()
        (self.repo / ".secrets" / "secrets.local.env").write_text("X=1\n")
        # A slow-root pass ended before the first run and, with the harness's interval, is never due
        # again: the follow tests see the run that every period but the hourly one makes. The hook
        # sweep classes below set an hour (slow_sweep_interval) and their own stamps.
        self.state.write_text(json.dumps({"slow_sweep_at": 999_000.0}))

    def tearDown(self) -> None:
        self.envoy_patch.stop()
        self.env_patch.stop()
        self.tmp.cleanup()

    def sh(self, *argv: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            argv, cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit_upstream(self, message: str, **files: str) -> str:
        for name, body in files.items():
            path = self.upstream / name.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        self.sh("git", "add", "-A", cwd=self.upstream)
        self.sh("git", "commit", "-q", "-m", message, cwd=self.upstream)
        self.sh("git", "push", "-q", "origin", "HEAD:main", cwd=self.upstream)
        return self.sh("git", "rev-parse", "HEAD", cwd=self.upstream)

    def head(self) -> str:
        return self.sh("git", "rev-parse", "HEAD", cwd=self.repo)

    def snapshot(self) -> dict:
        """HEAD, the index, and every file's bytes: what a refusal must leave as it was."""
        files = {}
        for path in sorted(self.repo.rglob("*")):
            if ".git" in path.relative_to(self.repo).parts or not path.is_file():
                continue
            files[str(path.relative_to(self.repo))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "HEAD": self.head(),
            "index": self.sh("git", "ls-files", "-s", cwd=self.repo),
            "status": self.sh("git", "status", "--porcelain=v1", "--untracked-files=all", cwd=self.repo),
            "files": files,
        }

    def options(self, **overrides) -> argparse.Namespace:
        values = {
            "repo": self.repo,
            "remote": "origin",
            "branch": "main",
            "provision": self.repo / "devbox/sami-devbox-provision",
            "state": self.state,
            "escalate": 7200.0,
            "fetch_grace": 3,
            "fetch_timeout": 60.0,
            "mise_timeout": 60.0,
            "check_timeout": 60.0,
            "http_timeout": 1.0,
            "print_alert": False,
            "hooks_dir": self.hooks_dir,
            "sweep_timeout": 30.0,
            "slow_sweep_interval": self.slow_sweep_interval,
            "slow_sweep_timeout": 30.0,
            "run_limit": 1170.0,
            "no_hook_sweep": False,
            "box_health_role": "",
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_follow(self, now: float = 1_000_000.0, **overrides) -> list[str]:
        with unittest.mock.patch.object(FOLLOW.time, "time", lambda: now):
            self.assertEqual(FOLLOW.run_once(self.options(**overrides)), 0)
        envoy = FakeEnvoy.instances[-1]
        for topic, _ in envoy.published:
            self.assertEqual(topic, "notifications.role.lead-box-health")
        return [message for _, message in envoy.published]

    def state_data(self) -> dict:
        import json

        return json.loads(self.state.read_text())

    def tools_passed(self, **data) -> None:
        """A state whose tools step already passed at HEAD, so a run skips that step (and may make a
        slow-root pass), plus DATA."""
        self.state.write_text(json.dumps({"tools_ok_head": self.head(), **data}))

    def show(self, case: str, messages: list[str], before: dict, after: dict) -> None:
        print(f"\n===== {case}: {len(messages)} message(s) to notifications.role.lead-box-health", file=sys.stderr)
        for text in messages:
            print(text, file=sys.stderr)
            print("-----", file=sys.stderr)
        print(
            f"checkout before: HEAD {before['HEAD'][:12]}, status {before['status']!r}, {len(before['files'])} files\n"
            f"checkout after:  HEAD {after['HEAD'][:12]}, status {after['status']!r}, {len(after['files'])} files;"
            f" unchanged: {before == after}",
            file=sys.stderr,
        )

    def assert_refused(self, case: str, headline: str, contains: str) -> list[str]:
        before = self.snapshot()
        messages = self.run_follow()
        after = self.snapshot()
        self.show(case, messages, before, after)
        self.assertEqual(before, after, "a refusal changed the checkout")
        self.assertEqual(len(messages), 1)
        self.assertIn("DOTFILES NOT FOLLOWING MAIN", messages[0])
        self.assertIn(headline, messages[0])
        self.assertIn(contains, messages[0])
        self.assertIn("It changed nothing in the checkout", messages[0])
        self.assertNotEqual(self.state_data()["last_outcome"], "ok")
        self.assertNotIn("install", self.fake_log.read_text() if self.fake_log.exists() else "")
        # Said once: the next run, ten minutes later, sends nothing and still changes nothing.
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.snapshot(), before)
        return messages


class InstallTools(Harness):
    def test_mise_install_holds_the_shared_data_lock(self) -> None:
        data = self.home / "mise-data"
        lock = data / ".dotfiles-install.lock"
        os.environ["MISE_DATA_DIR"] = str(data)

        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        self.assertTrue(lock.is_file())
        records = [
            line for line in self.fake_log.read_text().splitlines() if line.startswith("install ")
        ]
        self.assertEqual(records, [f"install held {lock.parent}"])

    def test_relative_mise_data_directory_is_absolute_for_the_shared_lock(self) -> None:
        previous = Path.cwd()
        data = self.home / "rel-data"
        try:
            os.chdir(self.home)
            os.environ["MISE_DATA_DIR"] = "rel-data"
            self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        finally:
            os.chdir(previous)

        records = [
            line for line in self.fake_log.read_text().splitlines() if line.startswith("install ")
        ]
        self.assertEqual(records, [f"install held {data}"])

    def test_mise_install_gets_the_default_data_directory_when_none_is_set(self) -> None:
        # Under the timer MISE_DATA_DIR is unset. mise's own default data directory is not
        # ~/.mise, so follow must hand mise the directory its lock is in, or the install lands
        # where no launch reads it.
        self.assertNotIn("MISE_DATA_DIR", os.environ)
        fakebin = Path(self.tmp.name) / "bin"
        (fakebin / "mise").write_text('#!/bin/sh\necho "$* data=${MISE_DATA_DIR-unset}" >> "$FAKE_LOG"\n')

        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        records = [line for line in self.fake_log.read_text().splitlines() if line.startswith("install ")]
        self.assertEqual(records, [f"install data={self.home / '.mise'}"])

    def write_recording_pin_check(self) -> None:
        check = self.repo / "devbox/sami-devbox-check"
        check.write_text('#!/bin/sh\necho "check data=${MISE_DATA_DIR-unset}" >> "$FAKE_LOG"\n')
        check.chmod(0o755)

    def assert_tools_use_data_directory(self, expected: Path) -> None:
        records = self.fake_log.read_text().splitlines()
        mise = next(record for record in records if record.startswith("install held "))
        bun = next(record for record in records if record.startswith("bun install --frozen-lockfile "))
        check = next(record for record in records if record.startswith("check data="))
        self.assertEqual(mise.rsplit(" ", 1)[1], str(expected))
        self.assertEqual(bun.rsplit("data=", 1)[1], mise.rsplit(" ", 1)[1])
        self.assertEqual(check.removeprefix("check data="), mise.rsplit(" ", 1)[1])

    def test_every_tools_command_gets_the_default_mise_data_directory(self) -> None:
        self.assertNotIn("MISE_DATA_DIR", os.environ)
        self.write_recording_pin_check()

        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")

        self.assert_tools_use_data_directory(self.home / ".mise")

    def test_every_tools_command_gets_the_absolute_relative_mise_data_directory(self) -> None:
        previous = Path.cwd()
        data = self.home / "rel-data"
        self.write_recording_pin_check()
        try:
            os.chdir(self.home)
            os.environ["MISE_DATA_DIR"] = "rel-data"
            self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        finally:
            os.chdir(previous)

        self.assert_tools_use_data_directory(data)

    def test_mise_install_timeout_waiting_for_the_shared_data_lock(self) -> None:
        data = self.home / "mise-data"
        data.mkdir()
        lock = data / ".dotfiles-install.lock"
        os.environ["MISE_DATA_DIR"] = str(data)

        def watchdog(_signum, _frame) -> None:
            raise AssertionError("install_tools did not return within 15 seconds")

        with open(lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            previous_handler = signal.signal(signal.SIGALRM, watchdog)
            signal.alarm(15)
            try:
                why = FOLLOW.install_tools(self.repo, self.options(mise_timeout=2))
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous_handler)

        self.assertEqual(
            why,
            f"`mise install` in {FOLLOW.display_path(self.repo)} did not start: another install held "
            f"{FOLLOW.display_path(lock)} for 2 s",
        )
        self.assertFalse(self.fake_log.exists(), "mise install ran while another process held its lock")

    def test_an_unusable_mise_data_directory_is_a_tools_condition_not_a_crash(self) -> None:
        data = self.home / "mise-data"
        data.write_text("a file where the directory should be\n")
        os.environ["MISE_DATA_DIR"] = str(data)

        why = FOLLOW.install_tools(self.repo, self.options())

        self.assertTrue(
            why.startswith(f"`mise install` in {FOLLOW.display_path(self.repo)} did not start: cannot open "),
            why,
        )
        self.assertFalse(self.fake_log.exists(), "mise install ran without its lock")


class Follows(Harness):
    def test_secret_files_come_from_the_provision_script(self) -> None:
        kept, note = FOLLOW.kept_paths(self.repo, self.repo / "devbox/sami-devbox-provision")
        self.assertEqual(note, "")
        # secrets.env is the one SECRET_FILES path git tracks; .secrets/ and omp/*.local.yml are ignored.
        self.assertEqual(kept, {"secrets.env"})
        entries = FOLLOW.secret_files(self.repo / "devbox/sami-devbox-provision")
        self.assertIn(".dotfiles/secrets.env", entries)
        self.assertIn(".dotfiles/.secrets/", entries)

    def test_fast_forwards_keeps_the_secrets_copy_and_installs_tools(self) -> None:
        new = self.commit_upstream("change a tool", scripts__tool="v2\n")
        messages = self.run_follow()
        self.assertEqual(messages, [])
        self.assertEqual(self.head(), new)
        self.assertEqual((self.repo / "scripts/tool").read_text(), "v2\n")
        self.assertEqual((self.repo / "secrets.env").read_text(), "COMMITTED=1\nWORKING_COPY=1\n")
        log = self.fake_log.read_text().split("\n")
        self.assertIn("install", log)
        self.assertIn("ls --missing", log)
        data = self.state_data()
        self.assertEqual(data["last_outcome"], "ok")
        self.assertEqual(data["tools_ok_head"], new)
        # Up to date: no fast-forward, no second mise install.
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.fake_log.read_text().split("\n").count("install"), 1)


class Refuses(Harness):
    def test_uncommitted_change(self) -> None:
        self.commit_upstream("change a tool", scripts__tool="v2\n")
        (self.repo / "scripts/tool").write_text("edited here\n")
        messages = self.assert_refused("uncommitted change", "UNCOMMITTED CHANGES", " M scripts/tool")
        self.assertIn("Kept on purpose and not judged: secrets.env", messages[0])
        # Said again once after --escalate, then not again.
        again = self.run_follow(now=1_000_000.0 + 7200)
        self.assertEqual(len(again), 1)
        self.assertIn("STILL NOT FOLLOWING MAIN", again[0])
        self.assertEqual(self.run_follow(now=1_000_000.0 + 7800), [])
        # The owner puts the file back: the next run fast-forwards and says it cleared.
        (self.repo / "scripts/tool").write_text("v1\n")
        cleared = self.run_follow(now=1_000_000.0 + 8400)
        self.assertEqual(len(cleared), 1)
        self.assertIn("DOTFILES FOLLOWING MAIN AGAIN", cleared[0])
        self.assertIn("UNCOMMITTED CHANGES cleared", cleared[0])
        self.assertEqual((self.repo / "scripts/tool").read_text(), "v2\n")

    def test_staged_change(self) -> None:
        self.commit_upstream("change a tool", scripts__tool="v2\n")
        (self.repo / "new-file").write_text("x\n")
        self.sh("git", "add", "new-file", cwd=self.repo)
        self.assert_refused("staged new file", "UNCOMMITTED CHANGES", "A  new-file")

    def test_untracked_file(self) -> None:
        self.commit_upstream("change a tool", scripts__tool="v2\n")
        (self.repo / "scripts" / "stray").write_text("x\n")
        self.assert_refused("untracked file", "UNCOMMITTED CHANGES", "?? scripts/stray")

    def test_git_status_failure_stops_follow(self) -> None:
        self.commit_upstream("change a tool", scripts__tool="v2\n")
        before = self.snapshot()
        git = FOLLOW.git

        def fail_status(repo, *args, **kwargs):
            if args[:1] == ("status",):
                return subprocess.CompletedProcess([], 1, "", "test git status failed")
            return git(repo, *args, **kwargs)

        with unittest.mock.patch.object(FOLLOW, "git", side_effect=fail_status):
            messages = self.run_follow()

        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(messages), 1)
        self.assertIn("UNCOMMITTED CHANGES", messages[0])
        self.assertIn("git status failed: test git status failed", messages[0])
        self.assertNotIn("install", self.fake_log.read_text() if self.fake_log.exists() else "")

    def test_deleted_kept_file(self) -> None:
        self.commit_upstream("change a tool", scripts__tool="v2\n")
        (self.repo / "secrets.env").unlink()
        self.assert_refused("kept file deleted", "UNCOMMITTED CHANGES", " D secrets.env")

    def test_non_fast_forward(self) -> None:
        self.commit_upstream("change a tool", scripts__tool="v2\n")
        (self.repo / "local").write_text("x\n")
        self.sh("git", "add", "local", cwd=self.repo)
        self.sh("git", "commit", "-q", "-m", "unpublished commit here", cwd=self.repo)
        self.assert_refused("non-fast-forward", "NOT A FAST-FORWARD", "unpublished commit here")

    def test_main_changes_the_kept_file(self) -> None:
        self.commit_upstream("rotate a key", **{"secrets.env": "COMMITTED=2\n"})
        self.assert_refused("merge touching secrets.env", "MAIN CHANGES A KEPT FILE", "changes secrets.env")
        # And git agrees: its own fast-forward refuses the same merge.
        merged = subprocess.run(
            ["git", "-C", str(self.repo), "merge", "--ff-only", "--dry-run", "origin/main"],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(merged.returncode, 0)
        self.assertEqual((self.repo / "secrets.env").read_text(), "COMMITTED=1\nWORKING_COPY=1\n")

    def test_mise_install_failure(self) -> None:
        new = self.commit_upstream("pin a new knives", **{"mise.toml": f'[tools]\n"github:sjawhar/knives" = "{PIN}"\n# new\n'})
        os.environ["FAKE_MISE_RC"] = "1"
        before = self.snapshot()
        messages = self.run_follow()
        after = self.snapshot()
        self.show("mise install failure", messages, before, after)
        self.assertEqual(self.head(), new, "the fast-forward itself succeeded")
        # The fast-forward changed mise.toml alone; the secrets copy is kept.
        changed = {name for name in before["files"].keys() | after["files"].keys() if before["files"].get(name) != after["files"].get(name)}
        self.assertEqual(changed, {"mise.toml"})
        self.assertEqual(len(messages), 1)
        self.assertIn("is on main, but a post-update step failed", messages[0].splitlines()[0])
        self.assertNotIn("NOT FOLLOWING MAIN", messages[0].splitlines()[0])
        self.assertIn(f"It fast-forwarded the checkout to {new[:12]} and changed nothing else", messages[0])
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("`mise install`", messages[0])
        self.assertIn("HTTP 403", messages[0])
        self.assertEqual(self.state_data()["last_outcome"], "tools")
        # The next run tries again without saying it again; once mise passes, it clears.
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.fake_log.read_text().split("\n").count("install"), 2)
        os.environ["FAKE_MISE_RC"] = "0"
        cleared = self.run_follow(now=1_001_200.0)
        self.assertEqual(len(cleared), 1)
        self.assertIn("TOOLS NOT INSTALLED cleared", cleared[0])
        self.assertEqual(self.state_data()["last_outcome"], "ok")

    def test_pin_check_failure(self) -> None:
        self.commit_upstream("pin a newer knives", **{"mise.toml": '[tools]\n"github:sjawhar/knives" = "9.9.9"\n'})
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("is on main, but a post-update step failed", messages[0].splitlines()[0])
        self.assertNotIn("NOT FOLLOWING MAIN", messages[0].splitlines()[0])
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("FAIL knives: want 'knives 9.9.9', got: knives 3.2.2", messages[0])

    def test_fetch_failure_waits_for_grace(self) -> None:
        self.sh("git", "remote", "set-url", "origin", str(self.home / "missing.git"), cwd=self.repo)
        self.assertEqual(self.run_follow(now=1_000_000.0), [])
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        messages = self.run_follow(now=1_001_200.0)
        self.assertEqual(len(messages), 1)
        self.assertIn("CANNOT FETCH origin/main", messages[0])


class PrePushHooks(Harness):
    """Every run makes one pass of the leak check's hook sweep (dispatch://INFRA-993, BET-589)."""

    slow_sweep_interval = 3600.0

    def setUp(self) -> None:
        # The tools step passed and a slow-root pass ended a minute ago: each run here sweeps the
        # fast roots only, unless a test says otherwise.
        super().setUp()
        self.tools_passed(slow_sweep_at=1_000_000.0 - 60)

    def sweeps(self) -> list[str]:
        path = Path(f"{self.fake_log}.sweep")
        return path.read_text().splitlines() if path.exists() else []

    def test_one_pass_over_both_roots_and_never_the_loop(self) -> None:
        # No slow-root pass recorded yet: this run sweeps both kinds of root, and records when it ended.
        self.tools_passed()
        os.environ["FAKE_SWEEP"] = "installed"
        self.assertEqual(self.run_follow(), [])
        self.assertEqual(self.sweeps(), [f"sweep /fast /slow here={self.hooks_dir}"])
        self.assertEqual(self.state_data()["last_outcome"], "ok")
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0)

    def test_a_checkout_it_cannot_hook_alerts_once_and_clears(self) -> None:
        os.environ["FAKE_SWEEP"] = "failed"
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("is on main, but a post-update step failed", messages[0].splitlines()[0])
        self.assertNotIn("NOT FOLLOWING MAIN", messages[0].splitlines()[0])
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED", messages[0])
        self.assertIn("/src/broken", messages[0])
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        os.environ["FAKE_SWEEP"] = "ok"
        cleared = self.run_follow(now=1_001_200.0)
        self.assertEqual(len(cleared), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED cleared", cleared[0])

    def test_a_pass_past_its_bound_is_killed_and_alerts(self) -> None:
        # A fast-root pass is bounded by --sweep-timeout, not --slow-sweep-timeout.
        self.tools_passed(slow_sweep_at=999000.0)
        os.environ["FAKE_SWEEP"] = "hang"
        started = time.monotonic()
        messages = self.run_follow(sweep_timeout=1.0, slow_sweep_timeout=30.0)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(self.sweeps(), [f"sweep /fast here={self.hooks_dir}"])
        self.assertEqual(len(messages), 1)
        self.assertIn("one fast-root pass (fast_roots only) of", messages[0])
        self.assertIn("did not end in 1s and was killed", messages[0])
        self.assertEqual(self.state_data()["slow_sweep_at"], 999000.0)

    def test_a_missing_sweep_alerts(self) -> None:
        (self.hooks_dir / "sweep-hooks.sh").unlink()
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("sweep-hooks.sh is missing", messages[0])
        self.assertIn("--only prepush", messages[0])

    def test_a_pass_with_zero_deferred_is_complete(self) -> None:
        os.environ["FAKE_SWEEP"] = "complete"
        self.assertEqual(self.run_follow(), [])
        self.assertEqual(self.state_data()["last_outcome"], "ok")

    def test_a_deferred_checkout_is_incomplete_coverage_and_clears(self) -> None:
        # dispatch://BET-706, the sweep review's finding 1: a pass that deferred a checkout reported success.
        os.environ["FAKE_SWEEP"] = "deferred"
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED", messages[0])
        self.assertIn("deferred 1", messages[0])
        self.assertIn("/src/later", messages[0])
        os.environ["FAKE_SWEEP"] = "complete"
        cleared = self.run_follow(now=1_000_600.0)
        self.assertEqual(len(cleared), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED cleared", cleared[0])

    def test_a_deferred_count_without_its_lines_is_incomplete_coverage(self) -> None:
        os.environ["FAKE_SWEEP"] = "deferred_count"
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("deferred 2", messages[0])


class SlowRoots(Harness):
    """The slow roots (/mnt/nvme/sami and /mnt/durable/sami on sami-devbox: about 20,000 checkouts)
    are swept at most once per --slow-sweep-interval; every other run sweeps the fast roots only."""

    slow_sweep_interval = 3600.0
    sweeps = PrePushHooks.sweeps

    def stamp(self, at: float) -> None:
        self.tools_passed(slow_sweep_at=at)

    def test_a_fresh_slow_timestamp_sweeps_fast_roots_only(self) -> None:
        self.stamp(1_000_000.0 - 3599)
        self.assertEqual(self.run_follow(), [])
        self.assertEqual(self.sweeps(), [f"sweep /fast here={self.hooks_dir}"])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0 - 3599)
        self.assertEqual(self.state_data()["last_outcome"], "ok")

    def test_an_old_slow_timestamp_sweeps_both_and_records_the_end(self) -> None:
        self.stamp(1_000_000.0 - 3600)
        self.assertEqual(self.run_follow(), [])
        self.assertEqual(self.sweeps(), [f"sweep /fast /slow here={self.hooks_dir}"])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0)
        # The next run, ten minutes later, is a fast-root pass again.
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.sweeps()[-1], f"sweep /fast here={self.hooks_dir}")
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0)

    def test_a_slow_pass_without_its_summary_line_is_not_recorded(self) -> None:
        self.stamp(1_000_000.0 - 7200)
        os.environ["FAKE_SWEEP"] = "broken"
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("one slow-root pass (fast_roots and slow_roots) of", messages[0])
        self.assertIn("exited 3 without its summary line", messages[0])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0 - 7200)

    def test_a_killed_slow_pass_keeps_the_timestamp_reports_and_is_retried(self) -> None:
        # Bounded by --slow-sweep-timeout, not --sweep-timeout.
        self.tools_passed()
        os.environ["FAKE_SWEEP"] = "hang"
        started = time.monotonic()
        messages = self.run_follow(sweep_timeout=30.0, slow_sweep_timeout=1.0)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(self.sweeps(), [f"sweep /fast /slow here={self.hooks_dir}"])
        self.assertEqual(len(messages), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED", messages[0])
        self.assertIn("one slow-root pass (fast_roots and slow_roots) of", messages[0])
        self.assertIn("did not end in 1s and was killed", messages[0])
        self.assertNotIn("slow_sweep_at", self.state_data())
        # The next run tries the slow roots again, and its completed pass clears the condition.
        os.environ["FAKE_SWEEP"] = "ok"
        cleared = self.run_follow(now=1_000_600.0)
        self.assertEqual(self.sweeps()[-1], f"sweep /fast /slow here={self.hooks_dir}")
        self.assertEqual(len(cleared), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED cleared", cleared[0])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_600.0)

    def test_a_slow_pass_with_a_failed_checkout_is_recorded_and_reported(self) -> None:
        # A pass that ends with its summary line is complete: the next slow pass waits its interval,
        # whatever the pass found. A fast-root pass cannot clear what the slow-root pass found.
        self.tools_passed()
        os.environ["FAKE_SWEEP"] = "failed"
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("the hook sweep's slow-root pass (fast_roots and slow_roots) could not install", messages[0])
        self.assertIn("/src/broken", messages[0])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0)
        os.environ["FAKE_SWEEP"] = "ok"
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.sweeps()[-1], f"sweep /fast here={self.hooks_dir}")
        self.assertIn("hooks", self.state_data()["incidents"])
        cleared = self.run_follow(now=1_003_600.0)
        self.assertEqual(self.sweeps()[-1], f"sweep /fast /slow here={self.hooks_dir}")
        self.assertEqual(len(cleared), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED cleared", cleared[0])

    def test_a_fast_pass_reports_a_deferred_checkout_as_a_fast_root_pass(self) -> None:
        self.stamp(1_000_000.0 - 60)
        os.environ["FAKE_SWEEP"] = "deferred"
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("the hook sweep's fast-root pass (fast_roots only) deferred 1", messages[0])
        # A fast-root condition clears on the next clean fast-root pass.
        os.environ["FAKE_SWEEP"] = "ok"
        cleared = self.run_follow(now=1_000_600.0)
        self.assertEqual(len(cleared), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED cleared", cleared[0])

    def test_a_run_whose_tools_step_ran_sweeps_fast_roots_only(self) -> None:
        # A slow-root pass is due (an hour old) but not overdue: the tools step runs, and its own
        # limits (mise 600 s, Bun 600 s, ...) leave no room for the slow pass's 1000 s inside the
        # unit's 1170 s; the next run, with the tools passed, sweeps the slow roots.
        self.state.write_text(json.dumps({"slow_sweep_at": 1_000_000.0 - 3600}))
        self.commit_upstream("new tool", scripts__tool="v2\n")
        self.assertEqual(self.run_follow(), [])
        self.assertEqual((self.repo / "scripts" / "tool").read_text(), "v2\n")
        self.assertEqual(self.fake_log.read_text().split("\n").count("install"), 1)
        self.assertEqual(self.sweeps(), [f"sweep /fast here={self.hooks_dir}"])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0 - 3600)
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.sweeps()[-1], f"sweep /fast /slow here={self.hooks_dir}")
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_600.0)

    # Round 2, the review's finding 1: slow_sweep_at is a finite epoch no later than now, or the
    # slow-root pass is due. NaN and Infinity (which Python's JSON reads and writes) and a time
    # ahead of the clock used to make every later run a fast-root pass.
    def test_only_a_finite_past_epoch_is_a_slow_sweep_time(self) -> None:
        for value in (None, "1000000", [], True, False, float("nan"), float("inf"), float("-inf"), -1.0,
                      1_000_100.0, 1e300):
            with self.subTest(value=value):
                self.assertIsNone(FOLLOW.slow_sweep_age({"slow_sweep_at": value}, 1_000_000.0))
                self.assertTrue(FOLLOW.slow_sweep_due(self.options(), {"slow_sweep_at": value}, 1_000_000.0))
                self.assertTrue(FOLLOW.slow_sweep_overdue(self.options(), {"slow_sweep_at": value}, 1_000_000.0))
        self.assertEqual(FOLLOW.slow_sweep_age({"slow_sweep_at": 999_000}, 1_000_000.0), 1000.0)
        self.assertEqual(FOLLOW.slow_sweep_age({"slow_sweep_at": 1_000_000.0}, 1_000_000.0), 0.0)
        self.assertIsNone(FOLLOW.slow_sweep_age({}, 1_000_000.0))

    def test_a_nan_infinite_or_future_slow_sweep_time_makes_the_run_sweep_both(self) -> None:
        for value in (float("nan"), float("inf"), 1_000_000.0 + 86_400):
            with self.subTest(value=value):
                Path(f"{self.fake_log}.sweep").unlink(missing_ok=True)
                self.tools_passed(slow_sweep_at=value)
                self.assertIn(json.dumps(value), self.state.read_text())
                self.assertEqual(self.run_follow(), [])
                self.assertEqual(self.sweeps(), [f"sweep /fast /slow here={self.hooks_dir}"])
                self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0)
                # Recorded as a real time: the next run is a fast-root pass.
                self.assertEqual(self.run_follow(now=1_000_600.0), [])
                self.assertEqual(self.sweeps()[-1], f"sweep /fast here={self.hooks_dir}")

    def test_a_clock_set_back_makes_the_next_run_sweep_both(self) -> None:
        self.stamp(1_000_000.0 - 3600)
        self.assertEqual(self.run_follow(), [])
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.sweeps(), [f"sweep /fast /slow here={self.hooks_dir}", f"sweep /fast here={self.hooks_dir}"])
        # The clock goes back 10,000 s: the recorded end is now in the future.
        self.assertEqual(self.run_follow(now=990_000.0), [])
        self.assertEqual(self.sweeps()[-1], f"sweep /fast /slow here={self.hooks_dir}")
        self.assertEqual(self.state_data()["slow_sweep_at"], 990_000.0)
        self.assertEqual(self.run_follow(now=990_600.0), [])
        self.assertEqual(self.sweeps()[-1], f"sweep /fast here={self.hooks_dir}")

    # Round 2, the review's finding 2: every pass ends before the run's deadline (--run-limit less
    # the reserve for the state and alerts), and a slow-root pass starts only with its whole budget.
    def timed_run(self, costs: dict[str, float], now: float = 1_000_000.0, **overrides) -> tuple[list, list[str]]:
        """A run whose clock moves by COSTS (seconds) on `git fetch`, on `git merge` (which then
        refuses, as when costs has "merge"), and on the tools step; returns ([(kind, budget) of each
        hook sweep pass], the messages)."""
        clock = [5000.0]
        passes: list = []
        git, sweep, tools = FOLLOW.git, FOLLOW.sweep_hooks, FOLLOW.install_tools

        def timed_git(repo, *args, **kwargs):
            if args[:1] == ("fetch",):
                clock[0] += costs.get("fetch", 0.0)
            if args[:1] == ("merge",) and "merge" in costs:
                clock[0] += costs["merge"]
                return subprocess.CompletedProcess(args, 1, "", "injected merge refusal")
            return git(repo, *args, **kwargs)

        def timed_tools(repo, options):
            clock[0] += costs.get("tools", 0.0)
            return tools(repo, options)

        def recorded_sweep(options, kind, *budget):
            passes.append((kind, budget[0] if budget else options.slow_sweep_timeout if kind == "slow" else options.sweep_timeout))
            return sweep(options, kind, *budget)

        values = {"sweep_timeout": 420.0, "slow_sweep_timeout": 1000.0, "http_timeout": 8.0, **overrides}
        with unittest.mock.patch.object(FOLLOW, "monotonic", lambda: clock[0], create=True), \
                unittest.mock.patch.object(FOLLOW, "git", timed_git), \
                unittest.mock.patch.object(FOLLOW, "install_tools", timed_tools), \
                unittest.mock.patch.object(FOLLOW, "sweep_hooks", recorded_sweep):
            messages = self.run_follow(now=now, **values)
        return passes, messages

    def test_a_slow_pass_does_not_start_after_a_slow_fetch_and_merge(self) -> None:
        # The review's reproducer: fetch 119 s and a merge refusal 119 s, with a slow-root pass due.
        # 1170 - 60 (reserve) - 238 = 872 s left: under 1000, so a fast-root pass of 420 s.
        self.tools_passed()
        self.commit_upstream("new tool", scripts__tool="v2\n")
        passes, messages = self.timed_run({"fetch": 119.0, "merge": 119.0})
        self.assertEqual(passes, [("fast", 420.0)])
        self.assertEqual(self.sweeps(), [f"sweep /fast here={self.hooks_dir}"])
        self.assertNotIn("slow_sweep_at", self.state_data())
        self.assertIn("merge", self.state_data()["incidents"])
        self.assertEqual(len(messages), 1)
        # The next run has its time: the overdue slow-root pass gets its whole 1000 s, not a cut
        # one (the fast-forward's tools step is left to the run after it).
        passes, _ = self.timed_run({}, now=1_000_600.0)
        self.assertEqual(passes, [("slow", 1000.0)])
        self.assertEqual((self.repo / "scripts" / "tool").read_text(), "v2\n")
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_600.0)

    def test_a_fast_pass_is_cut_to_the_time_left(self) -> None:
        # 1170 - 60 (reserve) - 700 = 410 s left, under the fast pass's 420.
        self.stamp(1_000_000.0 - 3600)
        passes, _ = self.timed_run({"fetch": 700.0})
        self.assertEqual(passes, [("fast", 410.0)])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0 - 3600)

    def test_a_long_tools_step_cuts_the_fast_pass(self) -> None:
        self.state.write_text(json.dumps({"slow_sweep_at": 1_000_000.0 - 60}))
        self.commit_upstream("new tool", scripts__tool="v2\n")
        passes, _ = self.timed_run({"tools": 800.0})
        self.assertEqual(passes, [("fast", 310.0)])

    def test_no_pass_runs_without_the_time_for_one_and_that_is_reported(self) -> None:
        self.stamp(1_000_000.0 - 3600)
        passes, messages = self.timed_run({"fetch": 1090.0})
        self.assertEqual(passes, [])
        self.assertEqual(self.sweeps(), [])
        self.assertEqual(len(messages), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED: no hook sweep pass ran", messages[0])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0 - 3600)

    def test_the_reserve_covers_three_messages_over_both_routes_and_the_state_write(self) -> None:
        self.assertEqual(FOLLOW.alert_reserve(self.options(http_timeout=8.0)), 3 * 2 * 8.0 + FOLLOW.STATE_RESERVE)
        self.assertEqual(FOLLOW.alert_reserve(self.options(http_timeout=8.0)), 60.0)

    # Round 2, the review's finding 3: a tools step that fails, or runs after every fast-forward, does
    # not keep the slow roots unswept; a run never runs the tools step and a slow-root pass both.
    def installs(self) -> int:
        return self.fake_log.read_text().split("\n").count("install") if self.fake_log.exists() else 0

    def hourly_runs(self, n: int, update: bool = False) -> list[tuple[str, bool]]:
        """N runs an hour apart from an empty state (with an upstream commit before each when
        UPDATE); returns [(the run's sweep line, whether its tools step ran)]."""
        self.state.write_text("{}")
        runs = []
        for k in range(n):
            if update:
                self.commit_upstream(f"tool v{k + 2}", scripts__tool=f"v{k + 2}\n")
            before = self.installs()
            self.run_follow(now=1_000_000.0 + 3600 * k)
            runs.append((self.sweeps()[-1].split(" here=")[0], self.installs() > before))
            if update:
                self.assertEqual((self.repo / "scripts" / "tool").read_text(), f"v{k + 2}\n")
        return runs

    def test_a_failing_tools_step_does_not_starve_the_slow_roots(self) -> None:
        os.environ["FAKE_MISE_RC"] = "1"
        runs = self.hourly_runs(9)
        # Overdue at the first run (none ever) and two hours after each: the tools step waits that
        # run out and retries on the next.
        self.assertEqual(runs, [("sweep /fast /slow", False), ("sweep /fast", True)] * 4 + [("sweep /fast /slow", False)])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0 + 3600 * 8)
        self.assertIn("tools", self.state_data()["incidents"])

    def test_upstream_updates_every_run_do_not_starve_the_slow_roots(self) -> None:
        runs = self.hourly_runs(6, update=True)
        self.assertEqual(runs, [("sweep /fast /slow", False), ("sweep /fast", True)] * 3)
        self.assertEqual(self.state_data()["tools_ok_head"], self.head())

    # Round 3, the review's R2-1: a slow-root pass that never completes left slow_sweep_at overdue for
    # ever, so every run left its tools step to the next. A run now does that at most once per
    # --slow-sweep-interval (slow_sweep_priority_at, on disk before the pass starts).
    def runs_with_kinds(self, n: int, step: float = 600.0, **overrides) -> list[tuple[str, bool]]:
        """N runs STEP seconds apart from an empty state, the tools step failing every time; returns
        [(the kind of hook sweep pass the run chose, whether its tools step ran)]."""
        os.environ["FAKE_MISE_RC"] = "1"
        self.state.write_text("{}")
        sweep, runs = FOLLOW.sweep_hooks, []
        for k in range(n):
            kinds: list[str] = []

            def recorded(options, kind, *budget, kinds=kinds):
                kinds.append(kind)
                return sweep(options, kind, *budget)

            before = self.installs()
            with unittest.mock.patch.object(FOLLOW, "sweep_hooks", recorded):
                self.run_follow(now=1_000_000.0 + step * k, **overrides)
            runs.append((kinds[0], self.installs() > before))
        return runs

    def assert_uncompleted_slow_passes_leave_tools_running(self, **overrides) -> None:
        # Tools are tried on every run but the first and the one an interval later; the slow-root
        # pass is tried on those two.
        self.assertEqual(self.runs_with_kinds(7, **overrides), [("slow", False)] + [("fast", True)] * 5 + [("slow", False)])
        data = self.state_data()
        self.assertNotIn("slow_sweep_at", data)
        self.assertEqual(data["slow_sweep_priority_at"], 1_000_000.0 + 3600)
        self.assertIn("tools", data["incidents"])
        self.assertIn("hooks", data["incidents"])

    def test_a_slow_pass_broken_every_run_does_not_starve_the_tools_step(self) -> None:
        os.environ["FAKE_SWEEP"] = "broken"
        self.assert_uncompleted_slow_passes_leave_tools_running()

    def test_a_slow_pass_killed_every_run_does_not_starve_the_tools_step(self) -> None:
        os.environ["FAKE_SWEEP"] = "hang"
        self.assert_uncompleted_slow_passes_leave_tools_running(sweep_timeout=1.0, slow_sweep_timeout=1.0)

    def test_a_slow_pass_without_its_summary_every_run_does_not_starve_the_tools_step(self) -> None:
        os.environ["FAKE_SWEEP"] = "nosummary"
        self.assert_uncompleted_slow_passes_leave_tools_running()

    def test_a_missing_sweep_every_run_does_not_starve_the_tools_step(self) -> None:
        (self.hooks_dir / "sweep-hooks.sh").unlink()
        self.assert_uncompleted_slow_passes_leave_tools_running()

    def test_a_run_killed_during_its_slow_pass_still_lets_the_next_run_run_tools(self) -> None:
        # The whole run dies inside the slow-root pass (as when systemd kills the unit): the time it
        # left its tools for that pass is already on disk, so the next run runs them.
        self.state.write_text("{}")
        seen: list[dict] = []

        class Killed(BaseException):
            pass

        def killed(options, kind, *budget):
            seen.append(json.loads(self.state.read_text()))
            raise Killed

        try:
            with unittest.mock.patch.object(FOLLOW, "sweep_hooks", killed):
                self.run_follow()
        except Killed:
            pass
        gc.collect()  # the dead run's lock file closes, as when its process exits
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].get("slow_sweep_priority_at"), 1_000_000.0)
        self.assertNotIn("slow_sweep_at", seen[0])
        self.assertEqual(self.installs(), 0)
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.installs(), 1)
        self.assertEqual(self.sweeps(), [f"sweep /fast here={self.hooks_dir}"])
        self.assertNotIn("slow_sweep_at", self.state_data())

    # Round 3, the review's R2-2: math.isfinite(10**400) raises OverflowError; a JSON integer past a
    # float's range is no time, for either record, and the run goes on.
    def test_huge_json_integers_are_no_time_for_either_record(self) -> None:
        for value in (10**400, -(10**400)):
            with self.subTest(sign="+" if value > 0 else "-"):
                self.assertIsNone(FOLLOW.epoch_age(value, 1_000_000.0))
                data = {"slow_sweep_at": value, "slow_sweep_priority_at": value}
                self.assertTrue(FOLLOW.slow_sweep_due(self.options(), data, 1_000_000.0))
                self.assertTrue(FOLLOW.slow_sweep_overdue(self.options(), data, 1_000_000.0))
                self.assertTrue(FOLLOW.slow_sweep_takes_priority(self.options(), data, 1_000_000.0))

    def test_a_huge_json_integer_slow_sweep_time_makes_the_run_sweep_both(self) -> None:
        for value in (10**400, -(10**400)):
            with self.subTest(sign="+" if value > 0 else "-"):
                Path(f"{self.fake_log}.sweep").unlink(missing_ok=True)
                self.tools_passed(slow_sweep_at=value)
                self.assertIn(str(value), self.state.read_text())
                self.assertEqual(self.run_follow(), [])
                self.assertEqual(self.sweeps(), [f"sweep /fast /slow here={self.hooks_dir}"])
                self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0)

    def test_a_huge_json_integer_priority_time_lets_the_slow_pass_take_the_tools_run(self) -> None:
        for value in (10**400, -(10**400)):
            with self.subTest(sign="+" if value > 0 else "-"):
                Path(f"{self.fake_log}.sweep").unlink(missing_ok=True)
                self.state.write_text(json.dumps({"slow_sweep_priority_at": value}))
                before = self.installs()
                self.assertEqual(self.run_follow(), [])
                self.assertEqual(self.installs(), before)
                self.assertEqual(self.sweeps(), [f"sweep /fast /slow here={self.hooks_dir}"])
                self.assertEqual(self.state_data()["slow_sweep_priority_at"], 1_000_000.0)


# A state with a slow-root timestamp and an open incident: what an interrupted write must not lose.
STATE_PRIOR = {"slow_sweep_at": 990_000.0, "incidents": {"hooks": {"pass": "slow", "why": "open", "opened_at": 1.0}}}


class StateFile(Harness):
    """Round 2, the review's finding 4: the run's lock is a separate, stable file, and the state is
    replaced whole (temp file, fsync, os.replace, directory fsync)."""

    def interrupted_commit(self, where: str) -> None:
        self.state.write_text(json.dumps(STATE_PRIOR))
        state = FOLLOW.State(self.state)
        self.assertTrue(state.locked)
        state.data["slow_sweep_at"] = 1_000_000.0
        state.data["incidents"].pop("hooks")

        def half_dump(data, handle, **kwargs):
            handle.write("{")
            handle.flush()
            raise RuntimeError("interrupted mid-write")

        def failed_replace(src, dst):
            raise RuntimeError("interrupted before the rename")

        target = (FOLLOW.json, "dump", half_dump) if where == "dump" else (FOLLOW.os, "replace", failed_replace)
        try:
            with unittest.mock.patch.object(*target), self.assertRaises(RuntimeError):
                state.commit()
        finally:
            state.close()
        self.assertEqual(json.loads(self.state.read_text()), STATE_PRIOR)
        self.assertEqual(sorted(p.name for p in self.state.parent.glob(f".{self.state.name}.*")), [], "a temp file was left")
        again = FOLLOW.State(self.state)
        try:
            self.assertEqual(again.data, STATE_PRIOR)
        finally:
            again.close()

    def test_a_write_interrupted_mid_serialization_leaves_the_old_state_whole(self) -> None:
        self.interrupted_commit("dump")

    def test_a_write_interrupted_before_the_rename_leaves_the_old_state_whole(self) -> None:
        self.interrupted_commit("replace")

    def test_a_commit_replaces_the_state_whole_and_the_lock_holds_across_it(self) -> None:
        self.state.write_text(json.dumps(STATE_PRIOR))
        inode = self.state.stat().st_ino
        state = FOLLOW.State(self.state)
        try:
            state.data["slow_sweep_at"] = 1_000_000.0
            state.commit()
            self.assertNotEqual(self.state.stat().st_ino, inode, "the state file was rewritten in place")
            self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0)
            self.assertEqual(self.state_data()["incidents"], STATE_PRIOR["incidents"])
            self.assertTrue(self.state.with_name(self.state.name + ".lock").is_file())
            other = FOLLOW.State(self.state)
            try:
                self.assertFalse(other.locked, "a second run took the lock after the state was replaced")
            finally:
                other.close()
        finally:
            state.close()
        third = FOLLOW.State(self.state)
        try:
            self.assertTrue(third.locked)
            self.assertEqual(third.data["slow_sweep_at"], 1_000_000.0)
        finally:
            third.close()


class TargetBytes(Harness):
    """A tracked file edited in place to exactly the bytes main holds: on 2026-11-03 a hand install
    of pi-envoy 15.5.0 on sami-devbox wrote main's new omp/plugins/package.json and bun.lock 69 s
    after the pin was pushed, and the follower stopped until a person discarded the edits."""

    def pin_upstream(self) -> str:
        return self.commit_upstream(
            "omp/plugins: pi-envoy 15.5.0",
            omp__plugins__package_json=PLUGINS_V2,
            omp__plugins__bun_lock=LOCK_V2,
        )

    def commit_upstream(self, message: str, **files: str) -> str:
        # File names with dots: package_json -> package.json, bun_lock -> bun.lock.
        renamed = {
            name.replace("package_json", "package.json").replace("bun_lock", "bun.lock"): body
            for name, body in files.items()
        }
        return super().commit_upstream(message, **renamed)

    def test_an_edit_equal_to_main_is_discarded_and_the_fast_forward_goes_on(self) -> None:
        new = self.pin_upstream()
        plugins = self.repo / "omp/plugins"
        (plugins / "package.json").write_text(PLUGINS_V2)
        (plugins / "bun.lock").write_text(LOCK_V2)
        with unittest.mock.patch.object(FOLLOW, "log", wraps=FOLLOW.log) as logged:
            messages = self.run_follow()
        notes = [call.args[0] for call in logged.call_args_list if call.args[0].startswith("note:")]
        print(f"\n===== edit equal to main: {len(messages)} message(s); log notes:", file=sys.stderr)
        for line in notes:
            print(f"  {line}", file=sys.stderr)
        self.assertEqual(messages, [])
        self.assertEqual(self.head(), new)
        self.assertEqual(len(notes), 1)
        self.assertIn("omp/plugins/bun.lock, omp/plugins/package.json held the bytes of", notes[0])
        self.assertEqual((plugins / "package.json").read_text(), PLUGINS_V2)
        self.assertEqual((plugins / "bun.lock").read_text(), LOCK_V2)
        # Only the kept secrets copy is still modified.
        self.assertEqual(self.sh("git", "status", "--porcelain", cwd=self.repo), "M secrets.env")
        self.assertEqual(self.state_data()["last_outcome"], "ok")
        # And the plugins main pins are installed, frozen, in omp/plugins.
        self.assertIn("bun install --frozen-lockfile in omp/plugins", self.fake_log.read_text())

    def test_an_edit_that_differs_from_main_still_refuses(self) -> None:
        self.pin_upstream()
        (self.repo / "omp/plugins/package.json").write_text(PLUGINS_V2.replace("15.5.0", "15.6.0"))
        self.assert_refused("edit that differs from main", "UNCOMMITTED CHANGES", " M omp/plugins/package.json")

    def test_one_equal_edit_beside_one_that_differs_refuses_and_discards_neither(self) -> None:
        self.pin_upstream()
        (self.repo / "omp/plugins/package.json").write_text(PLUGINS_V2)
        (self.repo / "omp/plugins/bun.lock").write_text("hand edit\n")
        messages = self.assert_refused("equal edit beside a different one", "UNCOMMITTED CHANGES", " M omp/plugins/bun.lock")
        self.assertNotIn(" M omp/plugins/package.json", messages[0])
        self.assertIn("omp/plugins/package.json (their edits are discarded at the fast-forward)", messages[0])
        self.assertEqual((self.repo / "omp/plugins/package.json").read_text(), PLUGINS_V2)

    def test_an_equal_edit_with_another_mode_still_refuses(self) -> None:
        self.pin_upstream()
        path = self.repo / "omp/plugins/package.json"
        path.write_text(PLUGINS_V2)
        path.chmod(0o755)
        self.assert_refused("equal bytes, other mode", "UNCOMMITTED CHANGES", " M omp/plugins/package.json")

    def test_a_frozen_plugin_install_failure_alerts(self) -> None:
        self.pin_upstream()
        os.environ["FAKE_BUN_RC"] = "1"
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("is on main, but a post-update step failed", messages[0].splitlines()[0])
        self.assertNotIn("NOT FOLLOWING MAIN", messages[0].splitlines()[0])
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("bun install --frozen-lockfile", messages[0])
        self.assertIn("lockfile is frozen", messages[0])


if __name__ == "__main__":
    unittest.main()
