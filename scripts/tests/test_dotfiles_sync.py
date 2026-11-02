#!/usr/bin/env python3
"""scripts/dotfiles-sync --here and scripts/dotfiles-publish on a jj checkout: a refused sync or
publish leaves the working copy as it was.

The working tree of ~/.dotfiles is production: every session and user unit reads it, and it always
holds other sessions' uncommitted edits. A sync whose working-copy change conflicts with origin's
main, or a publish whose commit (or the working copy above it) does, must refuse before it touches
a file on disk, so no session ever reads jj's conflict markers. One that does not conflict moves
onto main. On 2026-11-02 at 11:59Z the publish steps run by hand (`jj rebase -s <commit> -d
main@origin`) conflicted and left markers in shims/gh for 20 seconds.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SYNC = Path(__file__).resolve().parents[1] / "dotfiles-sync"


def real_binary(name: str) -> str:
    """The tool itself, not a shim: the test runs under a HOME of its own, where shims break."""
    found = subprocess.run(["mise", "which", name], capture_output=True, text=True)
    if found.returncode == 0 and found.stdout.strip():
        return found.stdout.strip()
    path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep) if "shims" not in p)
    located = shutil.which(name, path=path)
    if not located:
        raise unittest.SkipTest(f"{name} is not installed")
    return located


class DotfilesCheckout(unittest.TestCase):
    """An origin, a plain clone that pushes to it as another machine, and the colocated jj
    checkout under a HOME of its own that the script under test works in."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name in ("jj", "git"):
            (bin_dir / name).symlink_to(real_binary(name))
        self.home = root / "home"
        self.home.mkdir()
        jj_config = root / "jj.toml"
        jj_config.write_text('[user]\nname = "Test"\nemail = "test@example.com"\n[signing]\nbehavior = "drop"\n')
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "JJ_CONFIG": str(jj_config),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        self.origin = root / "origin.git"
        self.run_ok("git", "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.upstream = root / "upstream"
        self.run_ok("git", "clone", "-q", str(self.origin), str(self.upstream))
        (self.upstream / "shared.txt").write_text("base\n")
        (self.upstream / "other.txt").write_text("base\n")
        (self.upstream / "wip.txt").write_text("base\n")
        self.commit_upstream("base")
        self.checkout = self.home / ".dotfiles"
        self.run_ok("jj", "git", "clone", "--quiet", "--colocate", str(self.origin), str(self.checkout))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_ok(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(args, cwd=cwd, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"{args}: {result.stderr}")
        return result.stdout

    def commit_upstream(self, message: str) -> None:
        self.run_ok("git", "add", "-A", cwd=self.upstream)
        self.run_ok("git", "commit", "-q", "-m", message, cwd=self.upstream)
        self.run_ok("git", "push", "-q", "origin", "HEAD:main", cwd=self.upstream)

    def jj(self, *args: str) -> str:
        return self.run_ok("jj", *args, cwd=self.checkout)


class SyncHere(DotfilesCheckout):
    def sync(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(SYNC), "--here", ""], env=self.env, capture_output=True, text=True)

    def test_a_conflicting_change_is_refused_and_no_file_moves(self) -> None:
        local = self.checkout / "shared.txt"
        local.write_text("local edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")
        parent = self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id")

        result = self.sync()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CONFLICT", result.stderr)
        self.assertEqual(local.read_text(), "local edit\n")
        self.assertEqual((self.checkout / "other.txt").read_text(), "base\n")
        self.assertEqual(self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"), parent)
        self.assertEqual(self.jj("log", "-r", "@", "--no-graph", "-T", "conflict"), "false")
        self.assertEqual(self.jj("log", "-r", "conflicts()", "--no-graph", "-T", "change_id"), "")

    def test_a_change_that_does_not_conflict_moves_onto_main(self) -> None:
        (self.checkout / "other.txt").write_text("local edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")

        result = self.sync()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.checkout / "other.txt").read_text(), "local edit\n")
        self.assertEqual((self.checkout / "shared.txt").read_text(), "upstream edit\n")
        self.assertEqual(
            self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "main@origin", "--no-graph", "-T", "commit_id"),
        )


PUBLISH = Path(__file__).resolve().parents[1] / "dotfiles-publish"


class Publish(DotfilesCheckout):
    def origin_main(self) -> str:
        return self.run_ok("git", "rev-parse", "main", cwd=self.origin).strip()

    def my_commit(self, path: str, text: str) -> str:
        """Commit one path of mine, leaving another session's uncommitted edit in wip.txt."""
        (self.checkout / path).write_text(text)
        self.jj("commit", "--quiet", "-m", "mine", path)
        (self.checkout / "wip.txt").write_text("another session's edit\n")
        return self.jj("log", "-r", "@-", "--no-graph", "-T", "change_id")

    def publish(self, rev: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(PUBLISH), rev], env=self.env, capture_output=True, text=True)

    def test_a_conflicting_commit_is_refused_and_no_file_moves(self) -> None:
        change = self.my_commit("shared.txt", "my edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")
        upstream_head = self.origin_main()
        mine = self.jj("log", "-r", change, "--no-graph", "-T", "commit_id")

        result = self.publish(change)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CONFLICT", result.stderr)
        self.assertEqual((self.checkout / "shared.txt").read_text(), "my edit\n")
        self.assertEqual((self.checkout / "wip.txt").read_text(), "another session's edit\n")
        self.assertEqual(self.jj("log", "-r", change, "--no-graph", "-T", "commit_id"), mine)
        self.assertEqual(self.jj("log", "-r", "conflicts()", "--no-graph", "-T", "change_id"), "")
        self.assertEqual(self.origin_main(), upstream_head)

    def test_a_working_copy_that_would_conflict_stays_and_the_commit_goes_alone(self) -> None:
        # The working copy descends from the commit and would move with it, so its conflict would
        # reach disk: it stays on its old base, and a copy of the commit alone goes to main. The
        # first real run met this (2026-11-02): another session's uncommitted line beside one main
        # already had.
        change = self.my_commit("other.txt", "my edit\n")
        (self.upstream / "wip.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")
        upstream_head = self.origin_main()
        mine = self.jj("log", "-r", change, "--no-graph", "-T", "commit_id")
        wc = self.jj("log", "-r", "@", "--no-graph", "-T", "commit_id")

        result = self.publish(change)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("the working copy, above", result.stderr)
        self.assertIn("wip.txt", result.stderr)
        main = self.origin_main()
        self.assertEqual(self.run_ok("git", "rev-parse", f"{main}^", cwd=self.origin).strip(), upstream_head)
        self.assertEqual(self.run_ok("git", "show", f"{main}:other.txt", cwd=self.origin), "my edit\n")
        self.assertEqual(self.run_ok("git", "show", f"{main}:wip.txt", cwd=self.origin), "upstream edit\n")
        # Nothing local moved, and no file on disk changed.
        self.assertEqual(self.jj("log", "-r", change, "--no-graph", "-T", "commit_id"), mine)
        self.assertEqual(self.jj("log", "-r", "@", "--no-graph", "-T", "commit_id"), wc)
        self.assertEqual((self.checkout / "wip.txt").read_text(), "another session's edit\n")
        self.assertEqual(self.jj("log", "-r", "conflicts()", "--no-graph", "-T", "change_id"), "")

    def test_a_commit_that_does_not_conflict_is_rebased_and_pushed(self) -> None:
        change = self.my_commit("other.txt", "my edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")
        upstream_head = self.origin_main()

        result = self.publish(change)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        pushed = self.jj("log", "-r", change, "--no-graph", "-T", "commit_id")
        self.assertEqual(self.origin_main(), pushed)
        self.assertEqual(self.jj("log", "-r", f"{change}-", "--no-graph", "-T", "commit_id"), upstream_head)
        self.assertEqual((self.checkout / "other.txt").read_text(), "my edit\n")
        self.assertEqual((self.checkout / "shared.txt").read_text(), "upstream edit\n")
        self.assertEqual((self.checkout / "wip.txt").read_text(), "another session's edit\n")


if __name__ == "__main__":
    unittest.main()
