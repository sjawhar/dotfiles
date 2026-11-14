#!/usr/bin/env python3
"""scripts/dotfiles-sync --here and scripts/dotfiles-publish on a jj checkout: a refused sync or
publish leaves the working copy as it was, and a publish pushes the commit as it is after its
rebase, whether it is named by change id or commit id.

The working tree of ~/.dotfiles is production: every session and user unit reads it, and it always
holds other sessions' uncommitted edits. A sync whose working-copy change conflicts with origin's
main, or a publish whose commit (or the working copy above it) does, must refuse before it touches
a file on disk, so no session ever reads jj's conflict markers. One that does not conflict moves
onto main. On 2026-11-02 at 11:59Z the publish steps run by hand (`jj rebase -s <commit> -d
main@origin`) conflicted and left markers in shims/gh for 20 seconds.
"""

from __future__ import annotations

import fcntl
import os
import re
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
        for name in ("mise", "bun"):
            stand_in = bin_dir / name
            stand_in.write_text("#!/bin/sh\nexit 0\n")
            stand_in.chmod(0o755)
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

    def test_an_unpublished_commit_under_the_working_copy_is_refused_and_no_file_moves(self) -> None:
        # Moving the working copy alone onto main would drop this commit's change from disk.
        (self.checkout / "other.txt").write_text("their edit\n")
        self.jj("commit", "--quiet", "-m", "theirs", "other.txt")
        (self.checkout / "wip.txt").write_text("local edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")
        parent = self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id")

        result = self.sync()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("UNPUBLISHED", result.stderr)
        self.assertIn("theirs", result.stderr)
        self.assertEqual((self.checkout / "other.txt").read_text(), "their edit\n")
        self.assertEqual((self.checkout / "wip.txt").read_text(), "local edit\n")
        self.assertEqual((self.checkout / "shared.txt").read_text(), "base\n")
        self.assertEqual(self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"), parent)

    def test_a_commit_under_the_working_copy_that_main_already_has_is_dropped(self) -> None:
        # dotfiles-publish pushes a copy and leaves the original under the working copy.
        (self.checkout / "other.txt").write_text("published edit\n")
        self.jj("commit", "--quiet", "-m", "published as a copy", "other.txt")
        (self.checkout / "wip.txt").write_text("local edit\n")
        (self.upstream / "other.txt").write_text("published edit\n")
        self.commit_upstream("the copy")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")

        result = self.sync()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.checkout / "other.txt").read_text(), "published edit\n")
        self.assertEqual((self.checkout / "shared.txt").read_text(), "upstream edit\n")
        self.assertEqual((self.checkout / "wip.txt").read_text(), "local edit\n")
        self.assertEqual(
            self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "main@origin", "--no-graph", "-T", "commit_id"),
        )
        self.assertEqual(
            self.jj("log", "-r", "all() ~ ::main@origin", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "@", "--no-graph", "-T", "commit_id"),
        )

    def published_then_edited_on_main(self, also: dict[str, str]) -> str:
        """A commit under the working copy changes line 2 of other.txt (and each path in `also`).
        Main gets a copy of the line-2 change on a base whose line 1 differs, then changes line 2
        again: the copy's full patch differs from the original's, and the original no longer applies."""
        (self.upstream / "other.txt").write_text("1\n2\n3\n")
        self.commit_upstream("three lines")
        self.jj("git", "fetch", "--quiet")
        self.jj("new", "--quiet", "main@origin")
        (self.checkout / "other.txt").write_text("1\nX\n3\n")
        for path, text in also.items():
            (self.checkout / path).write_text(text)
        self.jj("commit", "--quiet", "-m", "published as a copy", "other.txt", *also)
        (self.checkout / "wip.txt").write_text("local edit\n")
        (self.upstream / "other.txt").write_text("one\n2\n3\n")
        self.commit_upstream("main changes line 1")
        (self.upstream / "other.txt").write_text("one\nX\n3\n")
        self.commit_upstream("the copy")
        (self.upstream / "other.txt").write_text("one\nY\n3\n")
        self.commit_upstream("main changes line 2 again")
        return self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id")

    def test_a_published_commit_whose_lines_main_changed_again_is_dropped(self) -> None:
        # 2026-11-03: four such originals under sami-rocqperf's working copy stopped every sync
        # there as UNPUBLISHED although their copies were on main.
        self.published_then_edited_on_main({})

        result = self.sync()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.checkout / "other.txt").read_text(), "one\nY\n3\n")
        self.assertEqual((self.checkout / "wip.txt").read_text(), "local edit\n")
        self.assertEqual(
            self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "main@origin", "--no-graph", "-T", "commit_id"),
        )
        self.assertEqual(
            self.jj("log", "-r", "all() ~ ::main@origin", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "@", "--no-graph", "-T", "commit_id"),
        )

    def test_a_published_commit_with_one_hunk_main_never_made_is_refused_and_no_file_moves(self) -> None:
        parent = self.published_then_edited_on_main({"shared.txt": "only here\n"})

        result = self.sync()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("UNPUBLISHED", result.stderr)
        self.assertEqual((self.checkout / "other.txt").read_text(), "1\nX\n3\n")
        self.assertEqual((self.checkout / "shared.txt").read_text(), "only here\n")
        self.assertEqual((self.checkout / "wip.txt").read_text(), "local edit\n")
        self.assertEqual(self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"), parent)

    def test_a_published_commit_another_commit_builds_on_stays_and_that_commit_is_not_rewritten(self) -> None:
        # On 2026-11-02 at 20:29Z the abandon rebased eight commits that sat on published
        # originals off the working copy's line, three of them into conflicts.
        (self.checkout / "other.txt").write_text("published edit\n")
        self.jj("commit", "--quiet", "-m", "published as a copy", "other.txt")
        published = self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id")
        self.jj("new", "--quiet", "--no-edit", published, "-m", "a side branch")
        side = self.jj("log", "-r", f"children({published}) ~ @", "--no-graph", "-T", "commit_id")
        (self.checkout / "wip.txt").write_text("local edit\n")
        (self.upstream / "other.txt").write_text("published edit\n")
        self.commit_upstream("the copy")

        result = self.sync()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "main@origin", "--no-graph", "-T", "commit_id"),
        )
        self.assertEqual(self.jj("log", "-r", f"{side}-", "--no-graph", "-T", "commit_id"), published)
        self.assertEqual(self.jj("log", "-r", "visible_heads() & description(exact:'a side branch\n')", "--no-graph", "-T", "commit_id"), side)

    def test_after_a_squash_an_old_commit_a_side_branch_builds_on_stays_and_the_side_branch_is_not_rewritten(self) -> None:
        # The shape of 2026-11-02 05:04:54Z: `--old` abandoned the old week (524 commits) and
        # rebased the 165 commits built on them, most into conflicts.
        base = self.run_ok("git", "rev-parse", "HEAD", cwd=self.upstream).strip()
        for path, text in (("shared.txt", "a1\n"), ("other.txt", "a2\n"), ("wip.txt", "a3\n")):
            (self.upstream / path).write_text(text)
            self.commit_upstream(f"old week: {path}")
        old = self.run_ok("git", "rev-parse", "HEAD", cwd=self.upstream).strip()
        a2 = self.run_ok("git", "rev-parse", "HEAD^", cwd=self.upstream).strip()
        self.jj("git", "fetch", "--quiet")
        self.jj("new", "--quiet", a2, "-m", "a side branch")
        (self.checkout / "other.txt").write_text("side\n")
        self.jj("new", "--quiet", old)
        side = self.jj("log", "-r", f"children({a2}) & description(exact:'a side branch\n')", "--no-graph", "-T", "commit_id")
        self.run_ok("git", "reset", "-q", "--soft", base, cwd=self.upstream)
        self.run_ok("git", "commit", "-q", "-m", "the squashed week", cwd=self.upstream)
        self.run_ok("git", "push", "-q", "--force", "origin", "HEAD:main", cwd=self.upstream)

        result = subprocess.run(["bash", str(SYNC), "--here", old], env=self.env, capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "main@origin", "--no-graph", "-T", "commit_id"),
        )
        self.assertEqual(self.jj("log", "-r", "visible_heads() & description(exact:'a side branch\n')", "--no-graph", "-T", "commit_id"), side)
        self.assertEqual(self.jj("log", "-r", "conflicts()", "--no-graph", "-T", "commit_id"), "")
        # The old commit nothing builds on is gone; the ones under the side branch stay.
        self.assertEqual(self.jj("log", "-r", f"{old} & ::visible_heads()", "--no-graph", "-T", "commit_id"), "")
        self.assertEqual(self.jj("log", "-r", f"{a2} & ::visible_heads()", "--no-graph", "-T", "commit_id"), a2)

    def test_the_common_directory_lock_refuses_without_moving_then_allows_the_move(self) -> None:
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")
        parent = self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id")
        common = self.run_ok("git", "rev-parse", "--git-common-dir", cwd=self.checkout).strip()
        lock = self.checkout / common / "dotfiles-sync.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(
                ["bash", str(SYNC), "--here", ""],
                env={**self.env, "DOTFILES_SYNC_LOCK_WAIT": "1"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(f"another dotfiles-sync holds {common}/dotfiles-sync.lock; not moved", result.stderr)
            self.assertEqual(self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"), parent)
            self.assertEqual((self.checkout / "shared.txt").read_text(), "base\n")
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.checkout / "shared.txt").read_text(), "upstream edit\n")
        self.assertEqual(
            self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"),
            self.jj("log", "-r", "main@origin", "--no-graph", "-T", "commit_id"),
        )


class Machines(unittest.TestCase):
    def test_oryx_is_not_a_sync_machine(self) -> None:
        match = re.search(r"^MACHINES=\(\n(?P<entries>.*?)^\)", SYNC.read_text(), re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match)
        entries = re.findall(r"^\s*(\S+?)=(\S+)\s*$", match.group("entries"), re.MULTILINE)
        self.assertTrue(entries)
        for name, destination in entries:
            self.assertNotEqual(name, "oryx")
            self.assertNotEqual(destination.rsplit("@", 1)[-1], "oryx")


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
        self.assertIn("the working copy, which would conflict", result.stderr)
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

    def test_a_commit_named_by_its_commit_id_is_rebased_and_the_rebased_commit_pushed(self) -> None:
        # The rebase gives the change a new commit id, and the old id still names the commit from
        # before it. On 2026-11-02 at 16:19Z a run given a commit id pushed that old commit, was
        # refused 3 times, and left a duplicate of the change.
        change = self.my_commit("other.txt", "my edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")
        upstream_head = self.origin_main()
        old = self.jj("log", "-r", change, "--no-graph", "-T", "commit_id")

        result = subprocess.run(["bash", str(PUBLISH), old], env=self.env, capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("refused", result.stderr)
        pushed = self.jj("log", "-r", change, "--no-graph", "-T", "commit_id")
        self.assertNotEqual(pushed, old)
        self.assertEqual(self.origin_main(), pushed)
        self.assertEqual(self.jj("log", "-r", f"{change}-", "--no-graph", "-T", "commit_id"), upstream_head)
        self.assertEqual(self.jj("log", "-r", 'description(glob:"mine\n*")', "--no-graph", "-T", "commit_id ++ ' '"), f"{pushed} ")
        self.assertEqual((self.checkout / "other.txt").read_text(), "my edit\n")
        self.assertEqual((self.checkout / "wip.txt").read_text(), "another session's edit\n")

    def test_a_rebased_commit_whose_push_loses_a_race_is_kept_and_pushed_on_the_next_try(self) -> None:
        change = self.my_commit("other.txt", "my edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")
        upstream_head = self.origin_main()
        # Origin refuses the first push, as when another session pushes first.
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text('#!/bin/sh\n[ -e refused-once ] && exit 0\ntouch refused-once\necho "refused once" >&2\nexit 1\n')
        hook.chmod(0o755)

        result = self.publish(change)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("push refused", result.stderr)
        pushed = self.jj("log", "-r", change, "--no-graph", "-T", "commit_id")
        self.assertEqual(self.origin_main(), pushed)
        self.assertEqual(self.jj("log", "-r", f"{change}-", "--no-graph", "-T", "commit_id"), upstream_head)
        self.assertEqual((self.checkout / "other.txt").read_text(), "my edit\n")
        self.assertEqual((self.checkout / "wip.txt").read_text(), "another session's edit\n")


class PublishByCommitId(Publish):
    """Every publish case again, the commit named by its commit id instead of its change id."""

    def publish(self, rev: str) -> subprocess.CompletedProcess[str]:
        return super().publish(self.jj("log", "-r", rev, "--no-graph", "-T", "commit_id"))


ME = "01a1aaaa-0000-7000-8000-000000000001"
THEM = "01a1bbbb-0000-7000-8000-000000000002"


def session_config(session: str) -> str:
    """What omp gives each session's jj (~/.cache/omp/jj/omp-attribution-<id>.toml): its own
    trailer, and every described commit near a head that another session made is immutable."""
    return (
        f"[templates]\ncommit_trailers = '\"Omp-Session: {session}\"'\n"
        "[revset-aliases]\n"
        "\"immutable_heads()\" = 'builtin_immutable_heads() | (ancestors(visible_heads(), 4) ~ ::builtin_immutable_heads()"
        f" ~ description(glob:\"*Omp-Session: {session}*\") ~ empty() ~ present(@) ~ (description(exact:\"\") ~ working_copies()))'\n"
    )


class PublishUnderSessionGuard(Publish):
    """The publishing session's jj refuses to rewrite or abandon another session's commit, as omp
    sets it up. On 2026-11-02 at 19:41-19:42Z three runs by one session each copied another
    session's two commits above its own, could not abandon the copies, stopped there, and left 14
    commits behind; the chief-dashboard commit showed five times in `jj log`."""

    def setUp(self) -> None:
        super().setUp()
        root = Path(self.temp_dir.name)
        base = self.env["JJ_CONFIG"]
        self.configs = {}
        for session in (ME, THEM):
            path = root / f"omp-attribution-{session}.toml"
            path.write_text(session_config(session))
            self.configs[session] = f"{base}{os.pathsep}{path}"
        self.env["JJ_CONFIG"] = self.configs[ME]

    def their_commit(self, path: str, text: str) -> str:
        """Another session commits one path of its own on top of the working copy's parent."""
        (self.checkout / path).write_text(text)
        env = {**self.env, "JJ_CONFIG": self.configs[THEM]}
        result = subprocess.run(["jj", "commit", "--quiet", "-m", "theirs", path], cwd=self.checkout, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.jj("log", "-r", "@-", "--no-graph", "-T", "change_id")

    def commits(self) -> set[str]:
        """Every visible commit except those on origin's main."""
        return set(self.jj("log", "-r", "all() ~ ::main@origin", "--no-graph", "-T", 'commit_id ++ "\\n"').split())

    def test_their_commit_above_mine_stays_and_mine_goes_alone_leaving_no_copy(self) -> None:
        change = self.my_commit("other.txt", "my edit\n")
        theirs = self.their_commit("shared.txt", "their edit\n")
        (self.checkout / "wip.txt").write_text("another session's edit\n")
        (self.upstream / "new.txt").write_text("upstream\n")
        self.commit_upstream("upstream")
        before = self.commits()

        result = self.publish(change)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        main = self.origin_main()
        self.assertEqual(self.run_ok("git", "show", f"{main}:other.txt", cwd=self.origin), "my edit\n")
        self.assertEqual(self.run_ok("git", "show", f"{main}:shared.txt", cwd=self.origin), "base\n")
        # Their commit and the working copy did not move, and no copy of anything is left.
        self.assertEqual(self.commits(), before)
        self.assertEqual(self.jj("log", "-r", f"{theirs}-", "--no-graph", "-T", "change_id"), change)
        self.assertEqual((self.checkout / "shared.txt").read_text(), "their edit\n")
        self.assertEqual((self.checkout / "wip.txt").read_text(), "another session's edit\n")

    def test_a_refused_publish_with_their_commit_above_leaves_no_copy(self) -> None:
        change = self.my_commit("shared.txt", "my edit\n")
        self.their_commit("other.txt", "their edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("upstream")
        upstream_head = self.origin_main()
        before = self.commits()

        result = self.publish(change)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CONFLICT", result.stderr)
        self.assertEqual(self.commits(), before)
        self.assertEqual(self.origin_main(), upstream_head)

    def test_a_copy_that_keeps_losing_the_race_leaves_only_my_commit(self) -> None:
        # Their unpublished commit sits under mine, so a copy of mine goes alone; every push is
        # refused. Each try's copy is abandoned, so no try adds a commit.
        self.their_commit("shared.txt", "their edit\n")
        change = self.my_commit("other.txt", "my edit\n")
        (self.upstream / "new.txt").write_text("upstream\n")
        self.commit_upstream("upstream")
        upstream_head = self.origin_main()
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text('#!/bin/sh\necho "refused" >&2\nexit 1\n')
        hook.chmod(0o755)
        before = self.commits()

        result = self.publish(change)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("push refused 3 times", result.stderr)
        self.assertEqual(self.commits(), before)
        self.assertEqual(self.jj("log", "-r", 'description(exact:"mine\\n\\nOmp-Session: ' + ME + '\\n")', "--no-graph", "-T", "change_id"), change)
        self.assertEqual(self.origin_main(), upstream_head)

    def test_a_copy_that_loses_one_race_is_pushed_and_leaves_no_copy(self) -> None:
        self.their_commit("shared.txt", "their edit\n")
        change = self.my_commit("other.txt", "my edit\n")
        (self.upstream / "new.txt").write_text("upstream\n")
        self.commit_upstream("upstream")
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text('#!/bin/sh\n[ -e refused-once ] && exit 0\ntouch refused-once\necho "refused once" >&2\nexit 1\n')
        hook.chmod(0o755)
        before = self.commits()

        result = self.publish(change)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("push refused", result.stderr)
        main = self.origin_main()
        self.assertEqual(self.run_ok("git", "show", f"{main}:other.txt", cwd=self.origin), "my edit\n")
        self.assertEqual(self.run_ok("git", "show", f"{main}:shared.txt", cwd=self.origin), "base\n")
        self.assertEqual(self.commits(), before)

    def test_another_sessions_commit_published_for_it_leaves_no_copy_when_every_push_is_refused(self) -> None:
        # This session's jj may not rewrite their commit, so a copy of it goes; the copy carries
        # their trailer, which once made its abandon after a lost race fail.
        change = self.their_commit("other.txt", "their edit\n")
        (self.upstream / "new.txt").write_text("upstream\n")
        self.commit_upstream("upstream")
        upstream_head = self.origin_main()
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text('#!/bin/sh\necho "refused" >&2\nexit 1\n')
        hook.chmod(0o755)
        before = self.commits()

        result = self.publish(change)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("push refused 3 times", result.stderr)
        self.assertEqual(self.commits(), before)
        self.assertEqual(self.origin_main(), upstream_head)

    def test_commits_below_that_main_already_has_do_not_read_as_a_sync_blocker(self) -> None:
        # On 2026-11-02 a publish called 16 commits under its own "unpublished" and said their
        # owners must publish them first; most were originals of copies main already had.
        self.their_commit("shared.txt", "their edit\n")
        (self.upstream / "shared.txt").write_text("their edit\n")
        self.commit_upstream("their copy")
        change = self.my_commit("other.txt", "my edit\n")

        result = self.publish(change)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("commits below it whose changes main already has", result.stderr)
        self.assertNotIn("unpublished", result.stderr)
        self.assertIn("`dotfiles-sync local` moves the working copy onto main", result.stderr)
        self.assertEqual(self.run_ok("git", "show", f"{self.origin_main()}:other.txt", cwd=self.origin), "my edit\n")


class PublishDivergent(DotfilesCheckout):
    """A change held by two commits. On 2026-11-03 two `jj commit` runs raced and gave two
    sessions' different work one change id. A copy may be dropped only when a copy on main@origin
    has its tree byte for byte, it has no children, and the working copy is not built on it; any
    other case is refused, and nothing is dropped or pushed."""

    def origin_main(self) -> str:
        return self.run_ok("git", "rev-parse", "main", cwd=self.origin).strip()

    def publish(self, rev: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(PUBLISH), rev], env=self.env, capture_output=True, text=True)

    def git(self, *args: str, **env: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.checkout, env={**self.env, **env}, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"git {args}: {result.stderr}")
        return result.stdout.strip()

    def copy_of(self, commit: str, other_txt: str | None = None) -> str:
        """A commit with <commit>'s parent and change-id header, another committer time, and, when
        other_txt is given, a tree whose other.txt holds it: the second commit of the change."""
        tree = self.git("rev-parse", f"{commit}^{{tree}}")
        if other_txt is not None:
            index = str(Path(self.temp_dir.name) / "copy-index")
            self.git("read-tree", commit, GIT_INDEX_FILE=index)
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"], cwd=self.checkout, env=self.env,
                input=other_txt, capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.git("update-index", "--cacheinfo", f"100644,{blob},other.txt", GIT_INDEX_FILE=index)
            tree = self.git("write-tree", GIT_INDEX_FILE=index)
        lines = self.git("cat-file", "commit", commit).split("\n")
        out = []
        for line in lines:
            if line.startswith("tree "):
                line = f"tree {tree}"
            elif line.startswith("committer "):
                line = line.rsplit(" ", 2)[0] + " 1700000000 +0000"
            out.append(line)
        return subprocess.run(
            ["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=self.checkout, env=self.env,
            input="\n".join(out) + "\n", capture_output=True, text=True, check=True,
        ).stdout.strip()

    def visible(self, change: str) -> set[str]:
        return set(self.jj("log", "-r", f"change_id({change})", "--no-graph", "-T", 'commit_id ++ " "').split())

    def test_two_copies_with_different_work_are_both_kept_and_nothing_is_pushed(self) -> None:
        (self.checkout / "other.txt").write_text("my edit\n")
        self.jj("commit", "--quiet", "-m", "mine", "other.txt")
        mine = self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id")
        change = self.jj("log", "-r", "@-", "--no-graph", "-T", "change_id")
        theirs = self.copy_of(mine, other_txt="their edit\n")
        self.git("update-ref", "refs/heads/race", theirs)
        self.jj("git", "import", "--quiet")
        self.assertEqual(self.visible(change), {mine, theirs})
        upstream_head = self.origin_main()

        result = self.publish(change)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("is divergent: 2 commits hold it", result.stderr)
        self.assertIn(mine[:12], result.stderr)
        self.assertIn(theirs[:12], result.stderr)
        self.assertIn("abandons none of them", result.stderr)
        self.assertIn("Publish each", result.stderr)
        self.assertEqual(self.visible(change), {mine, theirs})
        self.assertEqual(self.origin_main(), upstream_head)
        self.assertEqual((self.checkout / "other.txt").read_text(), "my edit\n")

    def main_copy(self) -> tuple[str, str, str]:
        """My commit, and a copy of it with the same tree pushed to main by another machine."""
        (self.checkout / "other.txt").write_text("my edit\n")
        self.jj("commit", "--quiet", "-m", "mine", "other.txt")
        mine = self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id")
        change = self.jj("log", "-r", "@-", "--no-graph", "-T", "change_id")
        on_main = self.copy_of(mine)
        self.git("push", "-q", "origin", f"{on_main}:refs/heads/main")
        self.jj("git", "fetch", "--quiet")
        self.assertEqual(self.visible(change), {mine, on_main})
        return mine, on_main, change

    def test_a_copy_main_holds_byte_for_byte_with_no_children_is_dropped(self) -> None:
        mine, on_main, change = self.main_copy()
        self.jj("new", "--quiet", on_main)
        self.assertEqual(self.jj("log", "-r", f"children({mine})", "--no-graph", "-T", "commit_id"), "")

        result = self.publish(change)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("dropped the copies main@origin already holds byte for byte", result.stderr)
        self.assertIn("already has", result.stdout)
        self.assertEqual(self.visible(change), {on_main})
        self.assertEqual(self.origin_main(), on_main)
        self.assertEqual((self.checkout / "other.txt").read_text(), "my edit\n")

    def test_a_copy_main_holds_byte_for_byte_but_the_working_copy_is_built_on_is_refused(self) -> None:
        mine, on_main, change = self.main_copy()
        (self.checkout / "wip.txt").write_text("another session's edit\n")

        result = self.publish(change)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("is divergent", result.stderr)
        self.assertIn("it has children", result.stderr)
        self.assertEqual(self.visible(change), {mine, on_main})
        self.assertEqual(self.origin_main(), on_main)
        self.assertEqual((self.checkout / "wip.txt").read_text(), "another session's edit\n")


if __name__ == "__main__":
    unittest.main()
