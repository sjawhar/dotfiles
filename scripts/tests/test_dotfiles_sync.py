#!/usr/bin/env python3
"""scripts/dotfiles-sync --here on a jj checkout: a refused sync leaves the working copy as it was.

The working tree of ~/.dotfiles is production: every session and user unit reads it. A sync whose
working-copy change conflicts with origin's main must refuse before it touches a file on disk, so
no session ever reads jj's conflict markers. One that does not conflict moves onto main and keeps
the change.
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


class SyncHere(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
