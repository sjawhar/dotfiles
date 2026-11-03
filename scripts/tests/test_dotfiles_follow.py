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
import hashlib
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
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
    install) [ "${FAKE_MISE_RC:-0}" = 0 ] || echo "mise ERROR Failed to install github:sjawhar/knives@3.2.2: HTTP 403" >&2; exit "${FAKE_MISE_RC:-0}" ;;
    ls) exit 0 ;;
esac
exit 0
"""
FAKE_KNIVES = f"""#!/bin/sh
echo "knives {PIN}"
"""
FAKE_BUN = """#!/bin/sh
# bun install --frozen-lockfile: logs where it ran, exits FAKE_BUN_RC.
echo "bun $* in ${PWD##*/.dotfiles/}" >> "$FAKE_LOG"
[ "${FAKE_BUN_RC:-0}" = 0 ] || echo "error: lockfile had changes, but lockfile is frozen" >&2
exit "${FAKE_BUN_RC:-0}"
"""
PLUGINS_V1 = '{\n  "dependencies": {\n    "@sjawhar/pi-envoy": "15.1.2"\n  }\n}\n'
PLUGINS_V2 = '{\n  "dependencies": {\n    "@sjawhar/pi-envoy": "15.5.0"\n  }\n}\n'
LOCK_V1 = '{ "@sjawhar/pi-envoy": ["@sjawhar/pi-envoy@15.1.2"] }\n'
LOCK_V2 = '{ "@sjawhar/pi-envoy": ["@sjawhar/pi-envoy@15.5.0"] }\n'


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
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "BOX_HEALTH", "ENVOY", "MISE_", "DOTFILES_", "XDG_STATE"))
        }
        env.update(
            HOME=str(self.home),
            PATH=f"{fakebin}:{os.environ['PATH']}",
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
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("FAIL knives: want 'knives 9.9.9', got: knives 3.2.2", messages[0])

    def test_fetch_failure_waits_for_grace(self) -> None:
        self.sh("git", "remote", "set-url", "origin", str(self.home / "missing.git"), cwd=self.repo)
        self.assertEqual(self.run_follow(now=1_000_000.0), [])
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        messages = self.run_follow(now=1_001_200.0)
        self.assertEqual(len(messages), 1)
        self.assertIn("CANNOT FETCH origin/main", messages[0])


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
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("bun install --frozen-lockfile", messages[0])
        self.assertIn("lockfile is frozen", messages[0])


if __name__ == "__main__":
    unittest.main()
