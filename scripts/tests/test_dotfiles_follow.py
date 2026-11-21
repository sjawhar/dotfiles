#!/usr/bin/env python3
"""Tests for dotfiles-follow: it fast-forwards a clean checkout and installs its tools, keeps the
SECRET_FILES copies, and refuses, changing nothing, on every other change.

Each case builds a throwaway origin and a clone of it at $HOME/.dotfiles in a temp dir, with the
real devbox/sami-devbox-provision (so the kept files are read from its SECRET_FILES) and the real
devbox/sami-devbox-check (so the pin lines that run are the check's own), and stand-in `mise` and
`knives` on PATH. A run's reports are what run_once returns (it writes them to its log too). A
refusal case prints the report and asserts that HEAD, the index and every file of the checkout are
as they were.
"""

import argparse
import fcntl
import gc
import hashlib
import http.server
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
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DOTFILES / "shims" / "tests"))
from leak_terms_fixture import fixture_environment  # noqa: E402 - found through the path above

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
    hang_logged) echo "2026-11-03T00:00:00Z FAILED /src/stuck" >> "$log"; echo "still walking"; sleep 60 ;;
    broken) echo "sweep: cannot walk a root"; exit 3 ;;
    nosummary) echo "2026-11-03T00:00:00Z INSTALLED /src/one" >> "$log" ;;
  esac
}
i=0
while true; do sweep "${fast_roots[@]}"; i=$((i + 1)); sleep 300; done
"""


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
            if not key.startswith(("GIT_", "ENVOY", "MISE_", "DOTFILES_", "XDG_CONFIG", "XDG_STATE"))
        }
        env.update(fixture_environment())
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
        # The cases' shared state file is ~/.dotfiles's file, DEFAULT_STATE: a run of another
        # checkout on it records nothing there (the one rule, B, in dotfiles-follow's docstring).
        self.default_state_patch = unittest.mock.patch.object(FOLLOW, "DEFAULT_STATE", self.state)
        self.default_state_patch.start()

    def tearDown(self) -> None:
        self.default_state_patch.stop()
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
            "fetch_grace": 3,
            "fetch_timeout": 60.0,
            "mise_timeout": 60.0,
            "check_timeout": 60.0,
            "hooks_dir": self.hooks_dir,
            "sweep_timeout": 30.0,
            "slow_sweep_interval": self.slow_sweep_interval,
            "slow_sweep_timeout": 30.0,
            "run_limit": 1170.0,
            "no_hook_sweep": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_follow(self, now: float = 1_000_000.0, **overrides) -> list[str]:
        with unittest.mock.patch.object(FOLLOW.time, "time", lambda: now):
            return FOLLOW.run_once(self.options(**overrides))

    def logged_run(self, now: float, **overrides) -> list[str]:
        """The log lines of one run_follow that reports nothing."""
        with unittest.mock.patch.object(FOLLOW, "log", wraps=FOLLOW.log) as logged:
            self.assertEqual(self.run_follow(now=now, **overrides), [])
        return [call.args[0] for call in logged.call_args_list]

    def other_checkout(self, **files: str) -> Path:
        """A clone that is not ~/.dotfiles, at the main of an origin of its own, one commit ahead of
        ~/.dotfiles' origin: the commit of FILES (named as commit_upstream names them, a new file
        included; a new scripts/tool when none). A run in it leaves ~/.dotfiles where it is, and its
        HEAD is never ~/.dotfiles'."""
        root = Path(self.tmp.name)
        origin, other = root / "other-origin.git", root / "other"
        self.sh("git", "clone", "-q", "--bare", str(self.origin), str(origin))
        self.sh("git", "clone", "-q", str(origin), str(other))
        for name, body in (files or {"scripts/tool": "another checkout's tool\n"}).items():
            path = other / name.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        self.sh("git", "add", "-A", cwd=other)
        self.sh("git", "commit", "-q", "-m", "another checkout's commit", cwd=other)
        self.sh("git", "push", "-q", "origin", "HEAD:main", cwd=other)
        return other

    def state_data(self) -> dict:
        import json

        return json.loads(self.state.read_text())

    def tools_passed(self, **data) -> None:
        """A state whose tools step already passed at HEAD, so a run skips that step (and may make a
        slow-root pass), plus DATA."""
        self.state.write_text(json.dumps({"tools_ok_head": self.head(), **data}))

    def show(self, case: str, messages: list[str], before: dict, after: dict) -> None:
        print(f"\n===== {case}: {len(messages)} report(s) in the run's log", file=sys.stderr)
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
        # Reported once: the next run, ten minutes later, reports nothing and still changes nothing.
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


# scripts/omp-link-rules as a stand-in: it records the checkout it runs for (DOTFILES_DIR, which
# run_step sets to the checkout) and exits FAKE_LINK_RC.
RECORDING_LINKER = """#!/bin/sh
echo "link $DOTFILES_DIR" >> "$FAKE_LOG"
[ "${FAKE_LINK_RC:-0}" = 0 ] || echo "omp-link-rules: ~/.omp/agent/rules/new-rule.md is a file that differs" >&2
exit "${FAKE_LINK_RC:-0}"
"""
# What install_tools logs in ~/.dotfiles once mise, Bun, the marketplace pins and the pin check all passed.
TOOLS_PASSED = "tools: mise install, frozen Bun install, marketplace pins and sami-devbox-check --pins passed"


def tools_passed_in(other: Path) -> str:
    """What install_tools logs in OTHER, a checkout that is not ~/.dotfiles, once mise, Bun and the
    pin check passed: it names only what ran, and the steps it skipped there."""
    return (
        "tools: mise install, frozen Bun install and sami-devbox-check --pins passed;"
        f" rules and marketplace plugins skipped: {other} is not ~/.dotfiles"
    )


class RuleLinks(Harness):
    """Only ~/.dotfiles links omp/rules/*.md into ~/.omp/agent/rules/, as dotfiles-sync's tools
    step does (review 19, rule A): git mode follows any --repo, and a link made from another
    checkout would point every session's rules at it. The cases' state file is ~/.dotfiles's
    (Harness), so a run of another checkout here records nothing (rule B): its tools step, which
    links nothing, clears no condition there and stops no ~/.dotfiles retry."""

    def setUp(self) -> None:
        super().setUp()
        self.new = self.commit_upstream("a linker that records its runs", **{"scripts/omp-link-rules": RECORDING_LINKER})

    def links(self) -> list[str]:
        """The checkouts the linker ran for, in order."""
        lines = self.fake_log.read_text().splitlines() if self.fake_log.exists() else []
        return [line.removeprefix("link ") for line in lines if line.startswith("link ")]

    def test_only_home_dotfiles_links_the_omp_rules(self) -> None:
        # Review 19 (main's code): install_tools ran scripts/omp-link-rules for any --repo. A run of
        # another clone now logs that it links nothing and goes on to mise, Bun and the rest as
        # before; a run of ~/.dotfiles links.
        other = self.other_checkout()
        with self.subTest(checkout="another clone"):
            lines = self.logged_run(1_000_000.0, repo=other, provision=other / "devbox/sami-devbox-provision")
            self.assertEqual(self.links(), [])
            self.assertIn(f"rules: {other} is not ~/.dotfiles; omp rules not linked", lines)
            self.assertNotIn("rules: omp/rules/*.md linked into ~/.omp/agent/rules/", lines)
            self.assertIn(tools_passed_in(other), lines)
            self.assertNotIn(TOOLS_PASSED, lines)
        with self.subTest(checkout="~/.dotfiles"):
            before = len(self.links())
            lines = self.logged_run(1_000_600.0)
            self.assertEqual(self.head(), self.new)
            self.assertEqual(self.links()[before:], [str(self.repo)])
            self.assertIn("rules: omp/rules/*.md linked into ~/.omp/agent/rules/", lines)
            self.assertFalse([line for line in lines if "omp rules not linked" in line], lines)
            self.assertIn(TOOLS_PASSED, lines)

    def test_another_checkout_leaves_an_open_tools_condition_open(self) -> None:
        # ~/.dotfiles' failed rule link opens TOOLS NOT INSTALLED. A run of another clone on
        # ~/.dotfiles' state file links no rules and records nothing, so its pass, with mise and Bun
        # passing, leaves the condition open: the rules are still not linked. Nor does that pass
        # stop ~/.dotfiles retrying: the next ~/.dotfiles run, whose link passes, runs its tools
        # step (the linker once) and clears the condition, the control. One row's clone is at
        # ~/.dotfiles' HEAD, where a pass recorded by the clone (tools_ok_head) would make that run
        # skip its tools step; the other row's clone is at a HEAD of its own.
        def at_home_head() -> Path:
            clone = Path(self.tmp.name) / "clone"
            self.sh("git", "clone", "-q", str(self.origin), str(clone))
            return clone

        rows = (("~/.dotfiles' HEAD", at_home_head, True), ("a HEAD of its own", self.other_checkout, False))
        for k, (at, make, same_head) in enumerate(rows):
            with self.subTest(other_checkout_at=at):
                now = 1_000_000.0 + 1800 * k
                # Each row starts from the state setUp writes: no tools pass recorded, nothing open.
                self.state.write_text(json.dumps({"slow_sweep_at": 999_000.0}))
                os.environ["FAKE_LINK_RC"] = "1"
                opened = self.run_follow(now=now)
                self.assertEqual(self.head(), self.new)
                self.assertEqual(len(opened), 1, opened)
                self.assertIn("TOOLS NOT INSTALLED: `scripts/omp-link-rules` in ~/.dotfiles exited 1:", opened[0])
                incident = self.state_data()["incidents"]["tools"]
                other = make()
                self.assertEqual(self.sh("git", "rev-parse", "HEAD", cwd=other) == self.new, same_head)
                lines = self.logged_run(now + 600, repo=other, provision=other / "devbox/sami-devbox-provision")
                self.assertIn(f"rules: {other} is not ~/.dotfiles; omp rules not linked", lines)
                self.assertIn(tools_passed_in(other), lines)
                self.assertEqual(self.state_data()["incidents"].get("tools"), incident)
                # The control.
                os.environ["FAKE_LINK_RC"] = "0"
                before = len(self.links())
                cleared = self.run_follow(now=now + 1200)
                self.assertEqual(self.links()[before:], [str(self.repo)])
                self.assertEqual(len(cleared), 1, cleared)
                self.assertIn("TOOLS NOT INSTALLED cleared", cleared[0])
                self.assertNotIn("tools", self.state_data()["incidents"])


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
        # Not reported again while it holds, even two hours later.
        self.assertEqual(self.run_follow(now=1_000_000.0 + 7200), [])
        # The owner puts the file back: the next run fast-forwards and reports that it cleared.
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

    def test_a_checkout_it_cannot_hook_is_reported_once_and_clears(self) -> None:
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

    def test_a_pass_past_its_bound_is_killed_and_reported(self) -> None:
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

    def test_a_killed_pass_keeps_its_log_and_only_the_newest_few(self) -> None:
        # The pass's log lives in a temporary directory removed when the pass ends; a killed pass's
        # log, and what it printed, are kept beside the state file, and only the newest
        # KILLED_SWEEP_LOGS of them stay. Control: before this change the report named no log and
        # nothing was kept.
        self.tools_passed(slow_sweep_at=999000.0)
        os.environ["FAKE_SWEEP"] = "hang_logged"
        keep = self.state.parent / "killed-sweeps"
        with unittest.mock.patch.object(FOLLOW, "KILLED_SWEEP_LOGS", 2):
            messages = self.run_follow(sweep_timeout=1.0, slow_sweep_timeout=30.0)
            first = sorted(keep.glob("*Z-fast.log"))
            self.assertEqual(len(first), 1)
            self.assertEqual(len(messages), 1)
            self.assertIn(f"Its log is kept at {FOLLOW.display_path(first[0])}.", messages[0])
            self.assertIn("FAILED /src/stuck", first[0].read_text())
            self.assertIn("still walking", first[0].read_text())
            for now in (1_000_600.0, 1_001_200.0):
                self.run_follow(now=now, sweep_timeout=1.0, slow_sweep_timeout=30.0)
        left = sorted(keep.glob("*Z-*.log"))
        self.assertEqual(len(left), 2)
        self.assertNotIn(first[0], left)
        self.assertEqual(list(keep.glob("*.new")), [])

    def test_a_missing_sweep_is_reported(self) -> None:
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

    def test_a_run_of_another_checkout_whose_tools_step_ran_sweeps_fast_roots_only(self) -> None:
        # The case above in another clone, on ~/.dotfiles' state file (Harness). Its tools step ran,
        # and another checkout makes fast-root passes only (rule C), so it makes no slow-root pass.
        self.state.write_text(json.dumps({"slow_sweep_at": 1_000_000.0 - 3600}))
        other = self.other_checkout()
        self.assertEqual(self.run_follow(repo=other, provision=other / "devbox/sami-devbox-provision"), [])
        self.assertEqual(self.installs(), 1)
        self.assertEqual(self.sweeps(), [f"sweep /fast here={self.hooks_dir}"])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0 - 3600)

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
    # the reserve for the state write), and a slow-root pass starts only with its whole budget.
    def timed_run(self, costs: dict[str, float], now: float = 1_000_000.0, **overrides) -> tuple[list, list[str]]:
        """A run whose clock moves by COSTS (seconds) on `git fetch`, on `git merge` (which then
        refuses, as when costs has "merge"), and on the tools step; returns ([(kind, budget) of each
        hook sweep pass], the reports)."""
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

        values = {"sweep_timeout": 420.0, "slow_sweep_timeout": 1000.0, **overrides}
        with unittest.mock.patch.object(FOLLOW, "monotonic", lambda: clock[0], create=True), \
                unittest.mock.patch.object(FOLLOW, "git", timed_git), \
                unittest.mock.patch.object(FOLLOW, "install_tools", timed_tools), \
                unittest.mock.patch.object(FOLLOW, "sweep_hooks", recorded_sweep):
            messages = self.run_follow(now=now, **values)
        return passes, messages

    def test_a_slow_pass_does_not_start_after_a_slow_fetch_and_merge(self) -> None:
        # The review's reproducer: fetch 119 s and a merge refusal 119 s, with a slow-root pass due.
        # 1170 - 12 (reserve) - 238 = 920 s left: under 1000, so a fast-root pass of 420 s.
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
        # 1170 - 12 (reserve) - 748 = 410 s left, under the fast pass's 420.
        self.stamp(1_000_000.0 - 3600)
        passes, _ = self.timed_run({"fetch": 748.0})
        self.assertEqual(passes, [("fast", 410.0)])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0 - 3600)

    def test_a_long_tools_step_cuts_the_fast_pass(self) -> None:
        self.state.write_text(json.dumps({"slow_sweep_at": 1_000_000.0 - 60}))
        self.commit_upstream("new tool", scripts__tool="v2\n")
        passes, _ = self.timed_run({"tools": 800.0})
        self.assertEqual(passes, [("fast", 358.0)])

    def test_no_pass_runs_without_the_time_for_one_and_that_is_reported(self) -> None:
        self.stamp(1_000_000.0 - 3600)
        passes, messages = self.timed_run({"fetch": 1138.0})
        self.assertEqual(passes, [])
        self.assertEqual(self.sweeps(), [])
        self.assertEqual(len(messages), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED: no hook sweep pass ran", messages[0])
        self.assertEqual(self.state_data()["slow_sweep_at"], 1_000_000.0 - 3600)

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


class DefaultStateFile(Harness):
    """The state file of a run with no --state is per checkout (dispatch://INFRA-3365): ~/.dotfiles
    keeps DEFAULT_STATE, the timer's file, and any other checkout gets a file of its own beside it.
    Before, the default did not depend on --repo, and --repo defaults to $DOTFILES_DIR, so a run of
    another checkout on the default opened and cleared ~/.dotfiles's conditions, overwrote its head,
    target, last outcome, fetch failures and last run, and held its lock. Each run here is main()
    with no --state and DEFAULT_STATE at a scratch path."""

    def setUp(self) -> None:
        super().setUp()
        self.default = Path(self.tmp.name) / "default-state" / "dotfiles-follow" / "state.json"

    def main_on_the_default(self, *argv: str, now: float, default: Path | None = None) -> None:
        """One run of main() at NOW, started with ARGV and no --state, with DEFAULT_STATE at
        DEFAULT (this case's scratch path when none) and no hook sweep."""
        args = ["dotfiles-follow", "--hooks-dir", str(self.hooks_dir), "--no-hook-sweep", *argv]
        with (
            unittest.mock.patch.object(sys, "argv", args),
            unittest.mock.patch.object(FOLLOW, "DEFAULT_STATE", default or self.default),
            unittest.mock.patch.object(FOLLOW.time, "time", lambda: now),
        ):
            self.assertEqual(FOLLOW.main(), 0)

    def state_files(self) -> dict[str, bytes]:
        """Every state file in DEFAULT_STATE's directory, by name, with its bytes (a lock file or a
        temporary file is not one)."""
        directory = self.default.parent
        return {path.name: path.read_bytes() for path in sorted(directory.glob("*.json"))} if directory.is_dir() else {}

    def test_another_checkout_leaves_the_home_dotfiles_state_file_byte_identical(self) -> None:
        # ~/.dotfiles opens UNCOMMITTED CHANGES on the default. Git mode judges that condition in
        # every checkout, so a run of another, clean clone on the same file cleared it and wrote its
        # own head there. Now that run, named by --repo or by DOTFILES_DIR, leaves the default
        # file's bytes as they were and writes a file of its own beside it.
        self.commit_upstream("change a tool", scripts__tool="v2\n")
        (self.repo / "scripts/tool").write_text("edited here\n")
        self.main_on_the_default(now=1_000_000.0)
        self.assertIn("dirty", json.loads(self.default.read_text())["incidents"])
        held = self.default.read_bytes()
        other = self.other_checkout()
        other_head = self.sh("git", "rev-parse", "HEAD", cwd=other)
        rows = (("--repo", ("--repo", str(other)), {}, 1_000_600.0), ("DOTFILES_DIR", (), {"DOTFILES_DIR": str(other)}, 1_001_200.0))
        for named, argv, environ, now in rows:
            with self.subTest(named=named):
                with unittest.mock.patch.dict(os.environ, environ):
                    self.main_on_the_default(*argv, now=now)
                self.assertEqual(self.default.read_bytes(), held, "a run of another checkout wrote ~/.dotfiles's state file")
                own = {name: json.loads(data) for name, data in self.state_files().items() if name != self.default.name}
                self.assertEqual(len(own), 1, f"another checkout's own state file, beside the default: {sorted(self.state_files())}")
                data = next(iter(own.values()))
                self.assertEqual((data["head"], data["last_run"]), (other_head, now))

    def test_home_dotfiles_writes_the_default_state_file_and_no_other(self) -> None:
        # The control of the case above: ~/.dotfiles, followed by default, as --repo ~/.dotfiles or
        # as $HOME/../<home's name>/.dotfiles (a spelling pathlib keeps, where it collapses /./),
        # writes DEFAULT_STATE (and its lock), and no other file.
        spelled = f"{self.home}/../{self.home.name}/.dotfiles"
        self.assertNotEqual(Path(spelled), self.repo)
        for index, argv in enumerate(((), ("--repo", str(self.repo)), ("--repo", spelled))):
            with self.subTest(argv=argv):
                default = Path(self.tmp.name) / f"default-{index}" / "dotfiles-follow" / "state.json"
                self.main_on_the_default(*argv, now=1_000_000.0 + 600 * index, default=default)
                self.assertEqual(sorted(path.name for path in default.parent.iterdir()), ["state.json", "state.json.lock"])
                data = json.loads(default.read_text())
                self.assertEqual((data["head"], data["last_run"]), (self.head(), 1_000_000.0 + 600 * index))

    def test_each_other_checkout_keeps_one_state_file_of_its_own(self) -> None:
        # One checkout, run twice and then by another spelling of its path, uses one file; a second
        # clone at the same HEAD uses another, and the first keeps its bytes. ~/.dotfiles's file is
        # never written.
        other = self.other_checkout()
        second = Path(self.tmp.name) / "other-2"
        self.sh("git", "clone", "-q", str(Path(self.tmp.name) / "other-origin.git"), str(second))
        runs = (
            ("first run", other, 1_000_000.0),
            ("second run", other, 1_000_600.0),
            ("another spelling", Path(f"{other}/../{other.name}"), 1_001_200.0),
        )
        names = set()
        for run, repo, now in runs:
            with self.subTest(run=run):
                self.main_on_the_default("--repo", str(repo), now=now)
                files = self.state_files()
                self.assertEqual(len(files), 1, sorted(files))
                names |= files.keys()
                self.assertEqual(len(names), 1, f"one checkout used more than one state file: {sorted(names)}")
                self.assertEqual(json.loads(next(iter(files.values())))["last_run"], now)
        self.assertNotIn(self.default.name, names)
        first = self.state_files()
        with self.subTest(run="a second checkout"):
            self.main_on_the_default("--repo", str(second), now=1_001_800.0)
            both = self.state_files()
            self.assertEqual(len(both), 2, sorted(both))
            self.assertEqual({name: both[name] for name in first}, first)
            (new,) = both.keys() - first.keys()
            self.assertEqual(json.loads(both[new])["last_run"], 1_001_800.0)
        self.assertFalse(self.default.exists(), "a run of another checkout wrote ~/.dotfiles's state file")

    def test_a_checkout_path_that_is_not_utf8_keeps_a_state_file_of_its_own(self) -> None:
        # Review 20: default_state hashed str(path).encode(), which raises UnicodeEncodeError for a
        # path that is not UTF-8, so main() stopped before the run. The key is the sha256 of the
        # path's bytes, as the command in dotfiles-follow.md makes it; the UTF-8 row is the control.
        # Each row runs main() in a child whose stdout refuses what is not UTF-8, as a unit's
        # journal can under a UTF-8 locale.
        driver = (
            "import sys; from pathlib import Path; from scripts.tests.test_dotfiles_follow import FOLLOW;"
            " FOLLOW.DEFAULT_STATE = Path(sys.argv.pop(1)); raise SystemExit(FOLLOW.main())"
        )
        for name in (b"clone-\xc3\xa9", b"clone-\xe9\xff"):
            with self.subTest(name=name):
                clone = Path(self.tmp.name) / os.fsdecode(name)
                self.sh("git", "clone", "-q", str(self.origin), str(clone))
                printed = subprocess.run(
                    ["sh", "-c", 'printf %s "$(realpath "$1")" | sha256sum | cut -c1-16', "_", clone],
                    check=True, capture_output=True,
                ).stdout.decode().strip()
                own = self.default.with_name(f"state-{printed}.json")
                with unittest.mock.patch.object(FOLLOW, "DEFAULT_STATE", self.default):
                    self.assertEqual(FOLLOW.default_state(clone), own)
                run = subprocess.run(
                    [sys.executable, "-c", driver, str(self.default), "--repo", str(clone),
                     "--hooks-dir", str(self.hooks_dir), "--no-hook-sweep"],
                    cwd=DOTFILES, capture_output=True, timeout=300,
                    env={**os.environ, "PYTHONPATH": str(DOTFILES), "PYTHONIOENCODING": "utf-8:strict"},
                )
                output = (run.stdout + run.stderr).decode(errors="backslashreplace")
                self.assertEqual(run.returncode, 0, output)
                self.assertIn(os.fsencode(clone), run.stdout)
                self.assertEqual(json.loads(own.read_text())["head"], self.sh("git", "rev-parse", "HEAD", cwd=clone))


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

    def test_a_frozen_plugin_install_failure_is_reported(self) -> None:
        self.pin_upstream()
        os.environ["FAKE_BUN_RC"] = "1"
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("is on main, but a post-update step failed", messages[0].splitlines()[0])
        self.assertNotIn("NOT FOLLOWING MAIN", messages[0].splitlines()[0])
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("bun install --frozen-lockfile", messages[0])
        self.assertIn("lockfile is frozen", messages[0])


# A small knives registry. The render rewrites the oh-my-pi table's workspaces line to this
# machine's path and copies every other line as it is, the hawk table's workspaces included.
OMP_WORKSPACES = 'workspaces = "~/.worktrees/oh-my-pi"'
REGISTRY_V1 = f"""[repos.hawk]
upstream = "https://example.com/hawk"
workspaces = "~/.worktrees/hawk"

[repos.oh-my-pi]
upstream = "https://example.com/oh-my-pi"
release_branch = "sami"
{OMP_WORKSPACES}
"""
REGISTRY_V2 = REGISTRY_V1.replace('release_branch = "sami"', 'release_branch = "sami-2"')
# The machine's copy of the registry and the render's record of what it depends on, under a HOME.
REGISTRY_COPY = Path(".config/knives/repos.toml")
REGISTRY_RECORD = Path(".config/knives/repos.toml.source-sha256")


def add_registry_render(upstream: Path) -> None:
    """Put the real scripts/knives-registry-render (its mode kept) and the installers/lib.sh it
    sources into UPSTREAM; the caller commits them."""
    for name in ("scripts/knives-registry-render", "installers/lib.sh"):
        (upstream / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DOTFILES / name, upstream / name)


class RegistryCopy:
    """$HOME/.config/knives as the render must leave it, worked out here, never by running the
    render: the one oracle of the render, which test_dotfiles_follow_jj, test_dotfiles_tools and
    test_knives_installer use too. Mixed into a test case whose `home` is the HOME the render runs
    with; a method given HOME works it out for that home instead. `nvme_root` is where the render
    looks for the big local disk's directory: /mnt/nvme, as when KNIVES_REGISTRY_NVME_ROOT is not
    set; a case that sets that variable sets `nvme_root` to the same path. `render_script` is the
    render the case ran, whose bytes are the render's rule: the oracle hashes them and never runs
    them."""

    home: Path
    nvme_root = Path("/mnt/nvme")

    @property
    def copy(self) -> Path:
        return self.home / REGISTRY_COPY

    @property
    def render_record(self) -> Path:
        return self.home / REGISTRY_RECORD

    def workspaces(self, home: Path | None = None) -> str:
        """The oh-my-pi workspaces path the render writes for HOME: the big disk's directory, named
        for the home, where there is one, else ~/.worktrees/oh-my-pi in full."""
        home = home or self.home
        nvme = self.nvme_root / home.name
        return f"{nvme}/omp-worktrees/branches" if nvme.is_dir() else f"{home}/.worktrees/oh-my-pi"

    def render_script(self, home: Path | None = None) -> Path:
        """The render the case ran, in HOME's checkout: ~/.dotfiles/scripts/knives-registry-render.
        A case whose render is elsewhere overrides this."""
        return (home or self.home) / ".dotfiles/scripts/knives-registry-render"

    def rendered(self, source: str, home: Path | None = None) -> str:
        """SOURCE with the oh-my-pi workspaces line set to HOME's path."""
        where = self.workspaces(home)
        return "".join(
            f'workspaces = "{where}"\n' if line == OMP_WORKSPACES + "\n" else line
            for line in source.splitlines(keepends=True)
        )

    @staticmethod
    def identity(path: Path) -> tuple[int, int]:
        """(inode, mtime in ns): a file replaced or rewritten gets another."""
        st = path.stat()
        return st.st_ino, st.st_mtime_ns

    def assert_copy_renders(self, source: str, home: Path | None = None) -> None:
        """HOME's copy holds the render of SOURCE, and the record beside it what that render
        depends on: SOURCE's sha256, the workspaces path and the sha256 of the render script."""
        home = home or self.home
        # SOURCE holds the line the render rewrites, and the oracle rewrote it.
        self.assertIn(OMP_WORKSPACES + "\n", source)
        self.assertNotIn(OMP_WORKSPACES, self.rendered(source, home))
        self.assertEqual((home / REGISTRY_COPY).read_text(), self.rendered(source, home))
        sha = hashlib.sha256(source.encode()).hexdigest()
        rule = hashlib.sha256(self.render_script(home).read_bytes()).hexdigest()
        self.assertEqual(
            (home / REGISTRY_RECORD).read_text(),
            f"sha256 {sha}\nworkspaces {self.workspaces(home)}\nrender {rule}\n",
        )


class KnivesRegistry(RegistryCopy, Harness):
    """A dev box's copy of the knives registry, ~/.config/knives/repos.toml, follows the committed
    knives/repos.toml with no person's command (dispatch://INFRA-3365, R5): every run at HEAD runs
    the real scripts/knives-registry-render --if-changed after any fast-forward."""

    def setUp(self) -> None:
        super().setUp()
        add_registry_render(self.upstream)
        self.commit_upstream("knives registry", **{"knives/repos.toml": REGISTRY_V1})
        self.sh("git", "pull", "-q", "--ff-only", cwd=self.repo)

    def installs(self) -> int:
        """How many times the tools step ran `mise install`."""
        return self.fake_log.read_text().split("\n").count("install") if self.fake_log.exists() else 0

    def first_render(self) -> None:
        """A run that renders the registry the clone holds, with no sha256 record yet."""
        self.assertFalse(self.copy.exists())
        self.assertEqual(self.run_follow(), [])
        self.assert_copy_renders(REGISTRY_V1)

    def pull_registry_v2(self) -> str:
        return self.commit_upstream("knives: a new oh-my-pi release branch", **{"knives/repos.toml": REGISTRY_V2})

    def test_a_pull_that_changes_the_registry_writes_the_copy_in_the_fast_forward_run(self) -> None:
        self.first_render()
        before = self.identity(self.copy)
        new = self.pull_registry_v2()
        with unittest.mock.patch.object(FOLLOW, "log", wraps=FOLLOW.log) as logged:
            messages = self.run_follow(now=1_000_600.0)
        lines = [call.args[0] for call in logged.call_args_list]
        self.assertEqual(messages, [])
        self.assertEqual(self.head(), new)
        self.assert_copy_renders(REGISTRY_V2)
        self.assertNotEqual(self.identity(self.copy), before)
        sha = hashlib.sha256(REGISTRY_V2.encode()).hexdigest()
        wrote = f"registry: knives-registry-render: wrote ~/.config/knives/repos.toml from knives/repos.toml (sha256 {sha[:12]})"
        self.assertIn(wrote, lines)
        # The fast-forward comes first, then the render, then the tools step.
        fast_forward = next(i for i, line in enumerate(lines) if line.startswith("fast-forwarded "))
        tools = next(i for i, line in enumerate(lines) if line.startswith("tools: "))
        self.assertLess(fast_forward, lines.index(wrote))
        self.assertLess(lines.index(wrote), tools)
        self.assertEqual(self.installs(), 2)

    def test_the_next_run_writes_nothing(self) -> None:
        self.first_render()
        self.pull_registry_v2()
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assert_copy_renders(REGISTRY_V2)
        written = (self.identity(self.copy), self.identity(self.render_record))
        with unittest.mock.patch.object(FOLLOW, "log", wraps=FOLLOW.log) as logged:
            self.assertEqual(self.run_follow(now=1_001_200.0), [])
        self.assertEqual((self.identity(self.copy), self.identity(self.render_record)), written)
        rendered = [call.args[0] for call in logged.call_args_list if call.args[0].startswith("registry: ")]
        self.assertEqual(len(rendered), 1)
        self.assertIn("~/.config/knives/repos.toml unchanged:", rendered[0])
        self.assertTrue(rendered[0].endswith("; wrote nothing"), rendered[0])

    def test_a_copy_rendered_without_the_big_disk_is_rendered_again_once_it_is_there(self) -> None:
        # The render depends on the workspaces path it writes as well as on knives/repos.toml: a
        # copy rendered while the big disk's directory was absent is written again once it is
        # there, with the file unchanged, and the run after that writes nothing.
        self.nvme_root = Path(self.tmp.name) / "nvme"
        os.environ["KNIVES_REGISTRY_NVME_ROOT"] = str(self.nvme_root)
        self.first_render()
        self.assertIn(f'workspaces = "{self.home}/.worktrees/oh-my-pi"\n', self.copy.read_text())
        before = (self.identity(self.copy), self.identity(self.render_record))
        (self.nvme_root / self.home.name).mkdir(parents=True)
        with unittest.mock.patch.object(FOLLOW, "log", wraps=FOLLOW.log) as logged:
            self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assert_copy_renders(REGISTRY_V1)
        big_disk = self.nvme_root / self.home.name / "omp-worktrees/branches"
        self.assertIn(f'workspaces = "{big_disk}"\n', self.copy.read_text())
        self.assertNotEqual(self.identity(self.copy), before[0])
        self.assertNotEqual(self.identity(self.render_record), before[1])
        sha = hashlib.sha256(REGISTRY_V1.encode()).hexdigest()
        self.assertIn(
            f"registry: knives-registry-render: wrote ~/.config/knives/repos.toml from knives/repos.toml (sha256 {sha[:12]})",
            [call.args[0] for call in logged.call_args_list],
        )
        written = (self.identity(self.copy), self.identity(self.render_record))
        self.assertEqual(self.run_follow(now=1_001_200.0), [])
        self.assertEqual((self.identity(self.copy), self.identity(self.render_record)), written)

    def test_a_pull_that_changes_only_the_render_script_renders_again(self) -> None:
        # The render depends on its own rule, scripts/knives-registry-render, as well: a pull that
        # changes that script by one comment line, with knives/repos.toml and the workspaces path
        # as they were, renders again, and the run after that writes nothing. The render's bytes
        # are the copy's bytes, so only the record is written.
        self.first_render()
        before = (self.identity(self.copy), self.identity(self.render_record))
        script = self.upstream / "scripts/knives-registry-render"
        new = self.commit_upstream(
            "knives-registry-render: one more comment line",
            **{"scripts/knives-registry-render": script.read_text() + "# one more comment line\n"},
        )
        with unittest.mock.patch.object(FOLLOW, "log", wraps=FOLLOW.log) as logged:
            self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.head(), new)
        self.assertTrue(os.access(self.render_script(), os.X_OK))
        self.assert_copy_renders(REGISTRY_V1)
        self.assertEqual(self.identity(self.copy), before[0])
        self.assertNotEqual(self.identity(self.render_record), before[1])
        sha = hashlib.sha256(REGISTRY_V1.encode()).hexdigest()
        self.assertIn(
            f"registry: knives-registry-render: ~/.config/knives/repos.toml already held the render of"
            f" knives/repos.toml (sha256 {sha[:12]}); wrote nothing to it",
            [call.args[0] for call in logged.call_args_list],
        )
        written = (self.identity(self.copy), self.identity(self.render_record))
        with unittest.mock.patch.object(FOLLOW, "log", wraps=FOLLOW.log) as logged:
            self.assertEqual(self.run_follow(now=1_001_200.0), [])
        self.assertEqual((self.identity(self.copy), self.identity(self.render_record)), written)
        rendered = [call.args[0] for call in logged.call_args_list if call.args[0].startswith("registry: ")]
        self.assertEqual(len(rendered), 1)
        self.assertIn("~/.config/knives/repos.toml unchanged:", rendered[0])

    def test_a_pull_that_changes_another_file_leaves_a_hand_edited_copy_as_it_is(self) -> None:
        self.first_render()
        self.copy.write_text(self.rendered(REGISTRY_V1) + "# a hand edit\n")
        edited = (self.copy.read_text(), self.identity(self.copy))
        new = self.commit_upstream("change a tool", scripts__tool="v2\n")
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.head(), new)
        self.assertEqual((self.copy.read_text(), self.identity(self.copy)), edited)

    def test_a_pull_that_changes_the_registry_replaces_a_hand_edited_copy(self) -> None:
        self.first_render()
        self.copy.write_text(self.rendered(REGISTRY_V1) + "# a hand edit\n")
        new = self.pull_registry_v2()
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.head(), new)
        self.assert_copy_renders(REGISTRY_V2)

    def test_the_first_run_of_this_code_at_head_renders_once_and_skips_the_tools_step(self) -> None:
        # The timer run that pulled this change was the old dotfiles-follow: the checkout is at
        # HEAD, its tools passed there, and the copy holds what install.sh wrote, with no record.
        self.tools_passed(slow_sweep_at=999_000.0)
        self.copy.parent.mkdir(parents=True)
        self.copy.write_text("# what install.sh wrote from an older knives/repos.toml\n")
        self.assertFalse(self.render_record.exists())
        self.assertEqual(self.run_follow(), [])
        self.assert_copy_renders(REGISTRY_V1)
        self.assertEqual(self.installs(), 0)
        written = (self.identity(self.copy), self.identity(self.render_record))
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual((self.identity(self.copy), self.identity(self.render_record)), written)
        self.assertEqual(self.installs(), 0)

    @unittest.skipIf(os.geteuid() == 0, "root writes into a read-only directory")
    def test_a_render_that_cannot_write_stops_nothing_is_said_once_and_clears(self) -> None:
        self.first_render()
        old = self.copy.read_text()
        new = self.pull_registry_v2()
        knives = self.copy.parent
        knives.chmod(0o555)
        try:
            messages = self.run_follow(now=1_000_600.0)
        finally:
            knives.chmod(0o755)
        print(f"\n===== read-only ~/.config/knives: {len(messages)} message(s)", file=sys.stderr)
        for text in messages:
            print(text, file=sys.stderr)
        self.assertEqual(self.head(), new, "the fast-forward went on")
        self.assertEqual(self.installs(), 2, "the tools step ran after the fast-forward")
        self.assertEqual(self.copy.read_text(), old)
        self.assertEqual(len(messages), 1)
        self.assertIn("is on main, but a post-update step failed", messages[0].splitlines()[0])
        self.assertIn(
            "KNIVES REGISTRY NOT WRITTEN: `scripts/knives-registry-render --if-changed` in ~/.dotfiles exited 1:",
            messages[0],
        )
        # mktemp quotes the template as the locale does ('...' or ‘...’): only the words are checked.
        failure = next((line for line in messages[0].splitlines() if "mktemp: failed to create file via template" in line), "")
        self.assertIn(f"{self.copy}.XXXXXX", failure)
        self.assertTrue(failure.endswith(": Permission denied"), failure)
        self.assertIn("~/.config/knives/repos.toml keeps what it held. The next run tries again.", messages[0])
        self.assertNotIn("TOOLS NOT INSTALLED", messages[0])
        # Nothing was written, and the footer says so.
        self.assertIn(f"It fast-forwarded the checkout to {new[:12]} and changed nothing else:", messages[0])
        self.assertEqual(self.state_data()["last_outcome"], "registry")
        cleared = self.run_follow(now=1_001_200.0)
        self.assert_copy_renders(REGISTRY_V2)
        self.assertEqual(len(cleared), 1)
        self.assertIn("DOTFILES FOLLOWING MAIN AGAIN", cleared[0])
        self.assertIn("KNIVES REGISTRY NOT WRITTEN cleared", cleared[0])
        self.assertEqual(self.state_data()["last_outcome"], "ok")
        self.assertEqual(self.installs(), 2)

    def test_a_copy_that_is_a_directory_is_a_condition_and_nothing_goes_inside_it(self) -> None:
        # A directory at the copy's path is refused (mv -T), not given the render as a file inside it.
        self.copy.mkdir(parents=True)
        (self.copy / "kept.txt").write_text("a person's file\n")
        messages = self.run_follow()
        self.assertEqual(sorted(path.name for path in self.copy.iterdir()), ["kept.txt"])
        self.assertEqual(len(messages), 1)
        self.assertIn("is on main, but a post-update step failed", messages[0].splitlines()[0])
        self.assertIn("KNIVES REGISTRY NOT WRITTEN", messages[0])
        self.assertIn("cannot overwrite directory", messages[0])
        self.assertIn("~/.config/knives/repos.toml keeps what it held.", messages[0])
        self.assertFalse(self.render_record.exists())

    def test_a_copy_that_links_to_a_directory_is_replaced_by_the_render(self) -> None:
        # The link itself is replaced by a plain file, and the directory it named is left as it was.
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        self.copy.parent.mkdir(parents=True)
        self.copy.symlink_to(elsewhere)
        self.assertEqual(self.run_follow(), [])
        self.assertFalse(self.copy.is_symlink())
        self.assert_copy_renders(REGISTRY_V1)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_the_footer_names_the_write_in_a_fast_forward_run(self) -> None:
        # The render writes the copy and the tools step fails in the same run: the one message says both.
        self.first_render()
        new = self.pull_registry_v2()
        os.environ["FAKE_MISE_RC"] = "1"
        messages = self.run_follow(now=1_000_600.0)
        self.assert_copy_renders(REGISTRY_V2)
        self.assertEqual(len(messages), 1)
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn(
            f"It fast-forwarded the checkout to {new[:12]}, wrote ~/.config/knives/repos.toml from knives/repos.toml"
            " and changed nothing else:",
            messages[0],
        )

    def test_the_footer_names_the_write_in_a_run_that_does_not_move(self) -> None:
        # The first run of this code renders, and its hook sweep fails in the same run.
        self.tools_passed(slow_sweep_at=999_000.0)
        os.environ["FAKE_SWEEP"] = "failed"
        messages = self.run_follow()
        self.assert_copy_renders(REGISTRY_V1)
        self.assertEqual(len(messages), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED", messages[0])
        self.assertIn(
            "It changed nothing in the checkout and wrote ~/.config/knives/repos.toml from knives/repos.toml:",
            messages[0],
        )

    def other_checkout(self, **files: str) -> Path:
        """Harness.other_checkout, whose knives/repos.toml is REGISTRY_V2 unless FILES names it, while
        ~/.dotfiles and its origin hold REGISTRY_V1: a copy rendered from it would show."""
        return super().other_checkout(**{"knives/repos.toml": REGISTRY_V2, **files})

    def logged_main(self, *argv: str) -> list[str]:
        """The log lines of one run of main(), started with ARGV as the unit starts it, but on this
        case's state and with no hook sweep."""
        args = ["dotfiles-follow", "--state", str(self.state), "--hooks-dir", str(self.hooks_dir), "--no-hook-sweep", *argv]
        with unittest.mock.patch.object(sys, "argv", args), unittest.mock.patch.object(FOLLOW, "log", wraps=FOLLOW.log) as logged:
            self.assertEqual(FOLLOW.main(), 0)
        return [call.args[0] for call in logged.call_args_list]

    def test_a_checkout_that_is_not_home_dotfiles_writes_no_copy(self) -> None:
        # The copy is what scripts/leak-terms-check reads, so only ~/.dotfiles writes it: git mode
        # follows any --repo, and DOTFILES_DIR names the default one. A run in another clone at its
        # main writes no copy when there is none yet, leaves the one ~/.dotfiles wrote as it was,
        # named by --repo or by DOTFILES_DIR, and writes none in a HOME with no ~/.dotfiles. Each
        # run logs why.
        other = self.other_checkout()
        skipped = f"registry: {other} is not ~/.dotfiles; knives registry copy not written"
        provision = other / "devbox/sami-devbox-provision"
        with self.subTest(copy="none yet"):
            lines = self.logged_run(1_000_000.0, repo=other, provision=provision)
            self.assertFalse(self.copy.exists(), lines)
            self.assertIn(skipped, lines)
        # ~/.dotfiles writes the copy, over any copy a failed row above left.
        self.assertEqual(self.run_follow(now=1_000_300.0), [])
        self.assert_copy_renders(REGISTRY_V1)
        written = (self.copy.read_text(), self.identity(self.copy), self.identity(self.render_record))
        with self.subTest(copy="written by ~/.dotfiles", named="--repo"):
            lines = self.logged_run(1_000_600.0, repo=other, provision=provision)
            self.assertEqual((self.copy.read_text(), self.identity(self.copy), self.identity(self.render_record)), written)
            self.assertIn(skipped, lines)
        with self.subTest(copy="written by ~/.dotfiles", named="DOTFILES_DIR"):
            with unittest.mock.patch.dict(os.environ, {"DOTFILES_DIR": str(other)}):
                lines = self.logged_main()
            self.assertEqual((self.copy.read_text(), self.identity(self.copy), self.identity(self.render_record)), written)
            self.assertIn(skipped, lines)
        with self.subTest(home="no ~/.dotfiles"):
            bare = Path(self.tmp.name) / "bare-home"
            bare.mkdir()
            with unittest.mock.patch.dict(os.environ, {"HOME": str(bare)}):
                lines = self.logged_run(1_001_200.0, repo=other, provision=provision)
            self.assertFalse((bare / REGISTRY_COPY).exists(), lines)
            self.assertIn(skipped, lines)

    def test_home_dotfiles_writes_a_stale_copy_by_any_spelling_of_its_path(self) -> None:
        # The control of the case above: ~/.dotfiles writes the copy, followed by default (no --repo
        # and no DOTFILES_DIR), as --repo ~/.dotfiles, $HOME/../<home's name>/.dotfiles (which
        # pathlib keeps, where it collapses /./) or $HOME//.dotfiles/, and, with ~/.dotfiles a
        # symlink, by its real path or as ~/.dotfiles. A copy that holds the render's bytes already
        # gets the record alone, so each row first gives the copy other bytes and removes the record.
        self.first_render()
        sha = hashlib.sha256(REGISTRY_V1.encode()).hexdigest()
        wrote = f"registry: knives-registry-render: wrote ~/.config/knives/repos.toml from knives/repos.toml (sha256 {sha[:12]})"

        def writes(*argv: str) -> None:
            self.copy.write_text("# stale\n")
            self.render_record.unlink(missing_ok=True)
            stale = self.identity(self.copy)
            lines = self.logged_main(*argv)
            self.assertIn(wrote, lines)
            self.assertFalse([line for line in lines if "knives registry copy not written" in line], lines)
            self.assert_copy_renders(REGISTRY_V1)
            self.assertNotEqual(self.identity(self.copy), stale)

        spelled = f"{self.home}/../{self.home.name}/.dotfiles"
        for argv in ((), ("--repo", str(self.repo)), ("--repo", spelled), ("--repo", f"{self.home}//.dotfiles/")):
            with self.subTest(argv=argv):
                writes(*argv)
        real = Path(self.tmp.name) / "real-dotfiles"
        self.repo.rename(real)
        self.repo.symlink_to(real)
        for named in (real, self.repo):
            with self.subTest(symlinked_home_dotfiles=str(named)):
                writes("--repo", str(named))

    @unittest.skipIf(os.geteuid() == 0, "root writes into a read-only directory")
    def test_another_checkout_leaves_an_open_registry_condition_open(self) -> None:
        # Review 19: a run of another clone on the state ~/.dotfiles uses cleared KNIVES REGISTRY NOT
        # WRITTEN and said no action was needed, though it rendered nothing. That state file is
        # ~/.dotfiles's (Harness), so the run records nothing there (rule B). ~/.dotfiles opens the
        # condition with a read-only ~/.config/knives, which is writable again before the other
        # clone's run. Two things keep the condition open there: rule B leaves the file unchanged,
        # and the other clone's registry step never renders, so it does not judge the condition.
        # The control: the next ~/.dotfiles run clears it.
        self.first_render()
        self.pull_registry_v2()
        knives = self.copy.parent
        knives.chmod(0o555)
        try:
            opened = self.run_follow(now=1_000_600.0)
        finally:
            knives.chmod(0o755)
        self.assertEqual(len(opened), 1)
        self.assertIn(
            "KNIVES REGISTRY NOT WRITTEN: `scripts/knives-registry-render --if-changed` in ~/.dotfiles exited 1:",
            opened[0],
        )
        incident = self.state_data()["incidents"]["registry"]
        held = (self.copy.read_text(), self.identity(self.copy), self.identity(self.render_record))
        # ~/.dotfiles holds REGISTRY_V2 now, so the other clone's own commit goes back to REGISTRY_V1.
        other = self.other_checkout(**{"knives/repos.toml": REGISTRY_V1})
        with self.subTest(run="another checkout"):
            lines = self.logged_run(1_001_200.0, repo=other, provision=other / "devbox/sami-devbox-provision")
            self.assertIn(f"registry: {other} is not ~/.dotfiles; knives registry copy not written", lines)
            self.assertEqual(self.state_data()["incidents"].get("registry"), incident)
            self.assertEqual((self.copy.read_text(), self.identity(self.copy), self.identity(self.render_record)), held)
        with self.subTest(run="~/.dotfiles, the control"):
            cleared = self.run_follow(now=1_001_800.0)
            self.assertEqual(len(cleared), 1, cleared)
            self.assertIn("KNIVES REGISTRY NOT WRITTEN cleared", cleared[0])
            self.assertNotIn("registry", self.state_data()["incidents"])
            self.assert_copy_renders(REGISTRY_V2)

    def test_a_home_dotfiles_symlinked_out_of_home_keeps_secrets_env_moves_and_renders(self) -> None:
        # SECRET_FILES names .dotfiles/secrets.env relative to HOME. With ~/.dotfiles a symlink to a
        # directory outside HOME, kept_paths took the checkout's resolved path, ../real-dotfiles/,
        # as the prefix, kept nothing, and every run refused the step-2 secrets.env as UNCOMMITTED
        # CHANGES. Now the run keeps it, fast-forwards and renders, whether it names the checkout
        # ~/.dotfiles or by its real path. The plain ~/.dotfiles row is the control: as before.
        registries = iter((REGISTRY_V2, REGISTRY_V1, REGISTRY_V2))

        def moves_and_renders(repo: Path, now: float) -> None:
            source = next(registries)
            new = self.commit_upstream("knives: another registry", **{"knives/repos.toml": source})
            with unittest.mock.patch.object(FOLLOW, "log", wraps=FOLLOW.log) as logged:
                messages = self.run_follow(now=now, repo=repo, provision=repo / "devbox/sami-devbox-provision")
            lines = [call.args[0] for call in logged.call_args_list]
            self.assertEqual(messages, [])
            self.assertEqual(self.head(), new)
            self.assertTrue([line for line in lines if "; kept: secrets.env;" in line], lines)
            self.assertEqual((repo / "secrets.env").read_text(), "COMMITTED=1\nWORKING_COPY=1\n")
            self.assert_copy_renders(source)

        with self.subTest(home_dotfiles="plain"):
            moves_and_renders(self.repo, 1_000_000.0)
        real = Path(self.tmp.name) / "real-dotfiles"
        self.repo.rename(real)
        self.repo.symlink_to(real)
        with self.subTest(home_dotfiles="a symlink out of HOME", named="~/.dotfiles"):
            moves_and_renders(self.repo, 1_000_600.0)
        with self.subTest(home_dotfiles="a symlink out of HOME", named="its real path"):
            moves_and_renders(real, 1_001_200.0)


# shims/omp as a stand-in for the one-rule table: it records each call with the checkout it runs in,
# and an upgrade sets the installed record to the version the lock asks for.
RECORDING_OMP = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
repo = Path(__file__).resolve().parents[1]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("omp " + " ".join(sys.argv[1:]) + " in " + str(repo) + "\\n")
if sys.argv[1:3] == ["plugin", "upgrade"]:
    plugins = repo / "omp/plugins"
    want = json.loads((plugins / "omp-plugins.lock.json").read_text())["plugins"][sys.argv[3].rpartition("@")[0]]
    installed = json.loads((plugins / "installed_plugins.json").read_text())
    installed["plugins"][sys.argv[3]][0]["version"] = want["version"]
    (plugins / "installed_plugins.json").write_text(json.dumps(installed))
"""
# One registered marketplace plugin whose lock entry is above its installed record: the tools step
# of a run that upgrades marketplace plugins calls shims/omp for it.
MATRIX_LOCK = json.dumps({"plugins": {"dev-infra": {"version": "2.0.0"}}}) + "\n"
MATRIX_INSTALLED = json.dumps({"plugins": {"dev-infra@verification-skills": [{"version": "1.0.0"}]}})


class OneRule(RegistryCopy, Harness):
    """The one rule for a run of a checkout other than ~/.dotfiles (dotfiles-follow's docstring), as
    one table. Its rows are ~/.dotfiles and another checkout, each on its own state file and on
    DEFAULT_STATE named by --state (through `..`, since the rule compares resolved paths). Each
    cell is what the row's runs of main() did, with the hook sweep on:
      A: whether a run wrote the knives registry copy, linked the omp rules or called shims/omp;
      B: which of UNCOMMITTED CHANGES (a git-mode kind) and TOOLS NOT INSTALLED its reports opened
         and cleared, which it logged as not recorded, whether the state file it uses recorded its
         tools_ok_head and last_run, whether that file kept its bytes through every run of the row's
         checkout, which of ~/.dotfiles's conditions, open through those runs, its own next pass
         cleared, and how many of two runs at one HEAD ran the tools step after the checkout's own
         pass and then after ~/.dotfiles's pass there;
      C: the kind of hook sweep pass of the first run, on a fresh state file, and whether that run's
         tools step ran, and how many slow-root passes the row's checkout made.
    The other checkout is a clone at ~/.dotfiles' HEAD, so ~/.dotfiles's pass is a pass at its HEAD.
    Each row has a DEFAULT_STATE of its own and starts with the plugin below its lock entry."""

    slow_sweep_interval = 3600.0
    sweeps = PrePushHooks.sweeps
    maxDiff = None  # a failure names each cell that differs

    HOME = {
        "A: registry copy written": True,
        "A: omp rules linked": True,
        "A: shims/omp called": True,
        "B: opened": ["dirty", "tools"],
        "B: cleared": ["dirty", "tools"],
        "B: logged as not recorded": [],
        "B: tools_ok_head recorded": True,
        "B: last_run recorded": True,
        "B: state file kept its bytes": False,
        "B: ~/.dotfiles's open conditions its next pass cleared": [],
        "B: tools-step runs after its own pass, then after ~/.dotfiles's": (0, 0),
        "C: first run on a fresh state file": ("slow", "tools deferred"),
        "C: slow-root passes": 1,
    }
    OTHER = {
        **HOME,
        "A: registry copy written": False,
        "A: omp rules linked": False,
        "A: shims/omp called": False,
        "C: first run on a fresh state file": ("fast", "tools ran"),
        "C: slow-root passes": 0,
    }
    TABLE = (
        ("~/.dotfiles", "its own state file", HOME),
        ("~/.dotfiles", "DEFAULT_STATE by --state", HOME),
        ("another checkout", "its own state file", OTHER),
        ("another checkout", "DEFAULT_STATE by --state", {
            **OTHER,
            "B: opened": [],
            "B: cleared": [],
            "B: logged as not recorded": ["dirty", "tools"],
            "B: tools_ok_head recorded": False,
            "B: last_run recorded": False,
            "B: state file kept its bytes": True,
            "B: ~/.dotfiles's open conditions its next pass cleared": ["dirty", "tools"],
            "B: tools-step runs after its own pass, then after ~/.dotfiles's": (2, 0),
        }),
    )

    def setUp(self) -> None:
        super().setUp()
        add_registry_render(self.upstream)
        omp = self.upstream / "shims/omp"
        omp.parent.mkdir()
        omp.write_text(RECORDING_OMP)
        omp.chmod(0o755)
        ignored = (self.upstream / ".gitignore").read_text()
        self.commit_upstream("the one rule's fixtures", **{
            "knives/repos.toml": REGISTRY_V1,
            "scripts/omp-link-rules": RECORDING_LINKER,
            "omp/plugins/omp-plugins.lock.json": MATRIX_LOCK,
            ".gitignore": ignored + "omp/plugins/installed_plugins.json\n",
        })
        self.sh("git", "pull", "-q", "--ff-only", cwd=self.repo)
        self.other = Path(self.tmp.name) / "other"
        self.sh("git", "clone", "-q", str(self.origin), str(self.other))

    def fake_lines(self) -> list[str]:
        return self.fake_log.read_text().splitlines() if self.fake_log.exists() else []

    def main_run(self, repo: Path, state: Path | None, now: float) -> tuple[list[str], list[str]]:
        """One run of main() for REPO at NOW with the hook sweep on, by --state STATE (no --state
        when None): (its reports, its log lines)."""
        argv = ["dotfiles-follow", "--repo", str(repo), "--hooks-dir", str(self.hooks_dir)]
        if state is not None:
            argv += ["--state", str(state)]
        reports: list[str] = []
        run_once = FOLLOW.run_once

        def recorded(options: argparse.Namespace) -> list[str]:
            reports.extend(run_once(options))
            return reports

        with (
            unittest.mock.patch.object(sys, "argv", argv),
            unittest.mock.patch.object(FOLLOW.time, "time", lambda: now),
            unittest.mock.patch.object(FOLLOW, "run_once", recorded),
            unittest.mock.patch.object(FOLLOW, "log", wraps=FOLLOW.log) as logged,
        ):
            self.assertEqual(FOLLOW.main(), 0)
        return reports, [call.args[0] for call in logged.call_args_list]

    def row(self, k: int, checkout: str, state_file: str) -> dict:
        """The cells of one row: the runs of its checkout (and, where it shares ~/.dotfiles's file,
        of ~/.dotfiles), each five minutes apart."""
        home_file = Path(self.tmp.name) / f"row-{k}" / "dotfiles-follow" / "state.json"
        repo = self.repo if checkout == "~/.dotfiles" else self.other
        named = home_file.parent / ".." / home_file.parent.name / home_file.name if state_file.endswith("--state") else None
        clock = iter(1_000_000.0 + 100_000 * k + 300 * step for step in range(100))
        opened, cleared, not_recorded, kinds, installs, kept = set(), set(), set(), [], [], []
        cells = {"A: registry copy written": False, "A: omp rules linked": False, "A: shims/omp called": False}
        for checkout_path in (self.repo, self.other):
            (checkout_path / "omp/plugins/installed_plugins.json").write_text(MATRIX_INSTALLED)

        def stray(path: Path, dirty: bool) -> None:
            if dirty:
                (path / "stray").write_text("an uncommitted file\n")
            else:
                (path / "stray").unlink(missing_ok=True)

        def checkout_run(dirty: bool = False, mise: str = "0") -> float:
            """One run of the row's checkout; returns its time."""
            stray(repo, dirty)
            os.environ["FAKE_MISE_RC"] = mise
            # No copy before the run, so any render writes one.
            self.copy.unlink(missing_ok=True)
            self.render_record.unlink(missing_ok=True)
            logged, swept = len(self.fake_lines()), len(self.sweeps())
            before = used.read_bytes() if used.exists() else None
            now = next(clock)
            reports, lines = self.main_run(repo, named, now)
            kept.append((before is not None, before == (used.read_bytes() if used.exists() else None)))
            new = self.fake_lines()[logged:]
            kinds.extend("slow" if " /slow " in line else "fast" for line in self.sweeps()[swept:])
            installs.append(new.count("install"))
            cells["A: registry copy written"] |= self.copy.exists()
            cells["A: omp rules linked"] |= f"link {repo}" in new
            cells["A: shims/omp called"] |= any(line.startswith("omp ") and line.endswith(f" in {repo.resolve()}") for line in new)
            for kind in ("dirty", "tools"):
                headline = FOLLOW.HEADLINE[kind]
                if any(f"{headline}: " in report for report in reports):
                    opened.add(kind)
                if any(f"{headline} cleared" in report for report in reports):
                    cleared.add(kind)
                if any(line.startswith("not recorded: ") and f"This run found {headline}: " in line for line in lines):
                    not_recorded.add(kind)
            return now

        def home_run(dirty: bool = False, mise: str = "0") -> list[str]:
            """One run of ~/.dotfiles on its own file, DEFAULT_STATE; returns its reports."""
            stray(self.repo, dirty)
            os.environ["FAKE_MISE_RC"] = mise
            return self.main_run(self.repo, None, next(clock))[0]

        def recorded(path: Path) -> dict:
            return json.loads(path.read_text()) if path.exists() else {}

        with unittest.mock.patch.object(FOLLOW, "DEFAULT_STATE", home_file):
            used = home_file if named else FOLLOW.default_state(repo)
            # A failing tools step on a fresh state file, then a dirty checkout, then a failing tools
            # step again: what opens, and what clears.
            checkout_run(mise="1")
            first = (kinds[0], "tools ran" if installs[0] else "tools deferred")
            checkout_run(dirty=True)
            checkout_run(mise="1")
            if repo != self.repo and named:
                # ~/.dotfiles opens both conditions on its file: its first run on the fresh file
                # makes the slow-root pass, the next fails its tools step, the next is dirty.
                home_run()
                home_run(mise="1")
                home_run(dirty=True)
                self.assertEqual(sorted(recorded(home_file)["incidents"]), ["dirty", "tools"])
            # A passing tools step, and two more runs at that HEAD.
            passed = checkout_run()
            data = recorded(used)
            checkout_run()
            checkout_run()
            # ~/.dotfiles passes at that HEAD on its file (its first run on a fresh file defers its
            # tools step), then two more runs of the row's checkout.
            home_cleared = set()
            for _ in range(2):
                if recorded(home_file).get("tools_ok_head") == self.head():
                    break
                for report in home_run():
                    home_cleared |= {kind for kind in ("dirty", "tools") if f"{FOLLOW.HEADLINE[kind]} cleared" in report}
            self.assertEqual(recorded(home_file).get("tools_ok_head"), self.head())
            checkout_run()
            checkout_run()
        stray(self.repo, False)
        return {
            **cells,
            "B: opened": sorted(opened),
            "B: cleared": sorted(cleared),
            "B: logged as not recorded": sorted(not_recorded),
            "B: tools_ok_head recorded": data.get("tools_ok_head") == self.head(),
            "B: last_run recorded": data.get("last_run") == passed,
            "B: state file kept its bytes": all(same for _, same in kept) and any(existed for existed, _ in kept),
            "B: ~/.dotfiles's open conditions its next pass cleared": sorted(home_cleared),
            "B: tools-step runs after its own pass, then after ~/.dotfiles's": (sum(installs[4:6]), sum(installs[6:8])),
            "C: first run on a fresh state file": first,
            "C: slow-root passes": kinds.count("slow"),
        }

    def test_the_one_rule(self) -> None:
        # Review 20's two measured cases are cells here. On its own default state file another
        # checkout's failing tools run opened TOOLS NOT INSTALLED and no run cleared it (B: cleared,
        # tools_ok_head recorded). Its first run on a fresh state file left its tools step for a
        # slow-root pass (C: first run). ~/.dotfiles's rows are the controls of every cell.
        for k, (checkout, state_file, want) in enumerate(self.TABLE):
            with self.subTest(checkout=checkout, state_file=state_file):
                got = self.row(k, checkout, state_file)
                print(f"\n===== {checkout} on {state_file}:", file=sys.stderr)
                for cell, value in got.items():
                    print(f"  {cell}: {value}", file=sys.stderr)
                self.assertEqual(got, want)

    @unittest.skipIf(os.geteuid() == 0, "root writes into a read-only directory")
    def test_another_checkout_on_a_shared_state_file_leaves_the_registry_condition_open(self) -> None:
        # Review 21: on a --state file that is not DEFAULT_STATE, which another checkout's run owns
        # (rule B), that run cleared the KNIVES REGISTRY NOT WRITTEN ~/.dotfiles opened there, though
        # it never renders. A condition is judged only by a run that does its work, so the run of
        # another checkout leaves it open; the control, a ~/.dotfiles run that renders, clears it.
        shared = Path(self.tmp.name) / "shared" / "state.json"
        self.assertNotEqual(shared.resolve(), FOLLOW.DEFAULT_STATE.resolve())
        headline = FOLLOW.HEADLINE["registry"]
        self.assertEqual(self.main_run(self.repo, shared, 1_000_000.0)[0], [])
        self.assert_copy_renders(REGISTRY_V1)
        self.commit_upstream("knives: a new oh-my-pi release branch", **{"knives/repos.toml": REGISTRY_V2})
        knives = self.copy.parent
        knives.chmod(0o555)
        try:
            opened, _ = self.main_run(self.repo, shared, 1_000_300.0)
        finally:
            knives.chmod(0o755)
        self.assertEqual(len(opened), 1, opened)
        self.assertIn(f"{headline}: `scripts/knives-registry-render --if-changed` in ~/.dotfiles exited 1:", opened[0])
        incident = json.loads(shared.read_text())["incidents"]["registry"]
        with self.subTest(run="another checkout, on the shared file"):
            reports, lines = self.main_run(self.other, shared, 1_000_600.0)
            data = json.loads(shared.read_text())
            # The run owns the file (rule B): it recorded its run there, and cleared nothing.
            self.assertEqual(data["last_run"], 1_000_600.0)
            self.assertIn(f"registry: {self.other} is not ~/.dotfiles; knives registry copy not written", lines)
            self.assertEqual(reports, [])
            self.assertEqual(data["incidents"].get("registry"), incident)
        with self.subTest(run="~/.dotfiles renders, the control"):
            cleared, _ = self.main_run(self.repo, shared, 1_000_900.0)
            self.assertEqual(len(cleared), 1, cleared)
            self.assertIn(f"{headline} cleared", cleared[0])
            self.assertNotIn("registry", json.loads(shared.read_text())["incidents"])
            self.assert_copy_renders(REGISTRY_V2)


class SendsNothing(Harness):
    """dotfiles-follow sends no alert and no message
    (dispatch://INFRA-2022/comment/0f96263e-1a85-4f29-85b3-ec713e6acf7b): a run that opens a
    condition, started as each unit starts it, makes no request to an Envoy listener, and its report
    is in its own output, which journald keeps."""

    # Each command line a unit runs, with the routes the removed alert code took from it: the
    # dev box's unit with a BOX_HEALTH_OWNER drop-in, and oryx's drop-in as its user manager loaded
    # it before this change (which still passes --box-health-role until oryx reloads it).
    UNITS = (
        ("devbox/dotfiles-follow.service", [], {"BOX_HEALTH_OWNER": "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"}),
        ("oryx's loaded drop-in", ["--no-hook-sweep", "--box-health-role", "legion-home-controller"], {}),
    )

    def test_a_refusal_reaches_no_envoy_listener(self) -> None:
        requests: list[str] = []

        class Listener(http.server.BaseHTTPRequestHandler):
            def answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                requests.append(f"{self.command} {self.path} {self.rfile.read(length).decode(errors='replace')}")
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = answer

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Listener)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.commit_upstream("change a tool", scripts__tool="v2\n")
        (self.repo / "scripts/tool").write_text("edited here\n")
        driver = "from scripts.tests.test_dotfiles_follow import FOLLOW; raise SystemExit(FOLLOW.main())"
        for unit, argv, routes in self.UNITS:
            with self.subTest(unit=unit):
                requests.clear()
                # A fresh state for each, so the condition opens in each run.
                state = self.state.with_name(f"state-{len(argv)}.json")
                env = {
                    **os.environ,
                    "ENVOY_URL": f"http://127.0.0.1:{server.server_address[1]}",
                    "ENVOY_TOKEN": "test-token",
                    "PYTHONPATH": str(DOTFILES),
                    **routes,
                }
                run = subprocess.run(
                    [sys.executable, "-c", driver, "--repo", str(self.repo), "--state", str(state),
                     "--hooks-dir", str(self.hooks_dir), *argv],
                    cwd=DOTFILES, env=env, capture_output=True, text=True, timeout=300,
                )
                output = run.stdout + run.stderr
                print(f"\n===== {unit}: the run's output, as journald keeps it:\n{output}", file=sys.stderr)
                self.assertEqual(run.returncode, 0, output)
                # The condition opened, so the removed code would have sent here.
                self.assertIn("dirty", json.loads(state.read_text())["incidents"])
                self.assertEqual(requests, [], "dotfiles-follow sent a request to Envoy")
                self.assertIn("DOTFILES NOT FOLLOWING MAIN", run.stdout)
                self.assertIn("UNCOMMITTED CHANGES", run.stdout)


if __name__ == "__main__":
    unittest.main()
