#!/usr/bin/env python3
"""scripts/leak-terms-check's push mode, run in a partial clone, gives its verdict without fetching from the promisor,
and still refuses a listed term in what the push sends.

With lazy fetch on, each advertised ID the clone lacked started its own fetch when the check asked whether the clone
holds it, so on 2026-11-21 a push through the shims from a blobless clone stalled for many minutes, as the shared leak
check did before dispatch://INFRA-4682. A fetched ID also counted as held where a full clone holds nothing.

Each case makes a bare repository whose `main` has two commits, so a clone of it lacks the first commit's `old.txt`,
and whose `side` branch a single-branch clone never fetches. The clone is `--filter=blob:none --single-branch` of
`main` over file://, and the bare repository serves lazy fetches. The check runs in push mode as the pre-push hook runs
it, with git's update lines on stdin, and GIT_TRACE records every git process it starts, so a fetch shows as
`built-in: git fetch`.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DOTFILES / "shims" / "tests"))
from leak_terms_fixture import TEST_TERMS, leak_check_environment  # noqa: E402 - found through the path above

CHECK = DOTFILES / "scripts" / "leak-terms-check"
GIT = "/usr/bin/git"
ZERO = "0" * 40
TERM = TEST_TERMS[1]
FOUND_EXIT = 3


class PartialClonePush(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="leak-terms-partial-clone-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {
            "HOME": str(self.root),
            "PATH": "/usr/bin:/bin",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        source = self.root / "source"
        self.git("init", "-q", "-b", "main", str(source))
        self.commit(source, "old.txt", "old\n")
        self.git("checkout", "-q", "-b", "side", cwd=source)
        self.commit(source, "side.txt", "side\n")
        self.git("checkout", "-q", "main", cwd=source)
        self.commit(source, "old.txt", "new\n")
        self.origin = self.root / "origin.git"
        self.git("clone", "-q", "--bare", str(source), str(self.origin))
        # A lazy fetch asks for one object by its ID, filtered.
        self.git("config", "uploadpack.allowFilter", "true", cwd=self.origin)
        self.git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=self.origin)
        self.url = f"file://{self.origin}"
        self.clone = self.root / "clone"
        self.git("clone", "-q", "--filter=blob:none", "--single-branch", "--branch", "main", self.url, str(self.clone))
        self.git("checkout", "-q", "-b", "feature", cwd=self.clone)
        self.side = self.rev(self.origin, "side")
        self.old_file = self.rev(self.origin, "main~1:old.txt")
        self.assertFalse(self.holds(self.clone, self.side), "the clone holds side, so no case could fetch it")
        self.assertFalse(self.holds(self.clone, self.old_file), "the clone holds the old file, so no case could fetch it")

    def git(self, *arguments: str, cwd: Path | None = None, stdin: str | None = None) -> str:
        completed = subprocess.run([GIT, *arguments], cwd=cwd, env=self.env, input=stdin, capture_output=True,
                                   text=True, timeout=60, check=False)
        self.assertEqual(completed.returncode, 0, f"git {' '.join(arguments)}: {completed.stderr}")
        return completed.stdout

    def commit(self, repository: Path, path: str, content: str) -> str:
        (repository / path).write_text(content, encoding="utf-8")
        self.git("add", path, cwd=repository)
        self.git("commit", "-q", "-m", f"write {path}", cwd=repository)
        return self.rev(repository, "HEAD")

    def rev(self, repository: Path, name: str) -> str:
        return self.git("rev-parse", name, cwd=repository).strip()

    def holds(self, repository: Path, object_id: str) -> bool:
        """Whether REPOSITORY holds OBJECT_ID, asked with lazy fetch off so the question fetches nothing."""
        return subprocess.run([GIT, "cat-file", "-e", object_id], cwd=repository,
                              env={**self.env, "GIT_NO_LAZY_FETCH": "1"}, capture_output=True, timeout=60,
                              check=False).returncode == 0

    def push(self, local_ref: str, local: str, repository: Path | None = None
             ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the check in REPOSITORY (the blobless clone) as the pre-push hook runs it, for a push of LOCAL to a ref
        of the same name that the destination lacks; return its result and the fetches GIT_TRACE saw."""
        trace = Path(tempfile.mkstemp(prefix="trace-", dir=self.root)[1])
        result = subprocess.run(
            [str(CHECK), "push", "--remote", "origin", "--url", self.url],
            input=f"{local_ref} {local} {local_ref} {ZERO}\n",
            cwd=repository or self.clone,
            env={**self.env, **leak_check_environment("/usr/bin:/bin"), "GIT_TRACE": str(trace)},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        traced = trace.read_text(encoding="utf-8").splitlines()
        # GIT_TRACE saw the read of what the clone holds, so it would have seen a fetch that read started.
        self.assertTrue(any("built-in: git cat-file" in line for line in traced), "GIT_TRACE recorded no git call")
        return result, [line for line in traced if "built-in: git fetch" in line]

    def test_a_clean_push_fetches_nothing(self) -> None:
        commit = self.commit(self.clone, "clean.txt", "nothing listed here\n")
        result, fetches = self.push("refs/heads/feature", commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fetches, [])
        self.assertFalse(self.holds(self.clone, self.side), "the check fetched the destination's side branch")

    def test_a_listed_term_in_a_new_commit_is_refused_without_a_fetch(self) -> None:
        commit = self.commit(self.clone, "planted.txt", f"a line with {TERM}\n")
        result, fetches = self.push("refs/heads/feature", commit)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn("planted.txt:1", result.stderr)
        self.assertEqual(fetches, [])

    def test_a_commit_only_an_unfetched_branch_holds_is_judged_as_a_full_clone_judges_it(self) -> None:
        # The clone's commit with a listed term reaches the destination's `shared` another way, and `shared` then moves
        # on where the clone never fetches it. A full clone with the same refs does not hold `shared`, so the commit is
        # new to it and refused. With lazy fetch on, the blobless clone fetched `shared`, held the commit and passed it.
        commit = self.commit(self.clone, "planted.txt", f"a line with {TERM}\n")
        self.git("push", "-q", self.url, f"{commit}:refs/heads/shared", cwd=self.clone)
        work = self.root / "work"
        self.git("clone", "-q", "--branch", "shared", self.url, str(work))
        later = self.commit(work, "later.txt", "later\n")
        self.git("push", "-q", "origin", "shared", cwd=work)
        full = self.root / "full"
        self.git("clone", "-q", "--single-branch", "--branch", "main", self.url, str(full))
        self.git("fetch", "-q", str(self.clone), "refs/heads/feature:refs/heads/feature", cwd=full)
        self.assertFalse(self.holds(full, later))
        verdicts, fetched = {}, {}
        for name, repository in (("blobless", self.clone), ("full", full)):
            result, fetches = self.push("refs/heads/feature", commit, repository)
            verdicts[name], fetched[name] = result.returncode, fetches
        self.assertEqual(verdicts, {"blobless": FOUND_EXIT, "full": FOUND_EXIT})
        self.assertEqual(fetched, {"blobless": [], "full": []})
        self.assertFalse(self.holds(self.clone, later), "the check fetched the destination's shared branch")

    def test_a_destination_ref_to_a_file_the_clone_lacks_is_read_past(self) -> None:
        # A destination ref names the first version of old.txt, which a folder the clone received names and the clone
        # never downloaded: with lazy fetch off, Ubuntu's git 2.43.0 stops the read there (exit 128). The ref after it
        # holds the parent of the pushed commit, a commit with a listed term, so it counts only when the read goes on.
        self.git("update-ref", "refs/tags/a-old-file", self.old_file, cwd=self.origin)
        parent = self.commit(self.clone, "planted.txt", f"a line with {TERM}\n")
        self.git("push", "-q", self.url, f"{parent}:refs/tags/b-parent", cwd=self.clone)
        commit = self.commit(self.clone, "clean.txt", "nothing listed here\n")
        result, fetches = self.push("refs/heads/feature", commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fetches, [])
        self.assertFalse(self.holds(self.clone, self.old_file), "the check fetched the old file")

    def test_a_folder_pushed_by_itself_is_read_without_fetching_the_destinations_files(self) -> None:
        # A folder object pushed by itself is read unless the destination holds it, which the check lists from every
        # folder of the destination's history, the first commit's included, whose old.txt the clone lacks.
        blob = self.git("hash-object", "-w", "--stdin", cwd=self.clone, stdin=f"a line with {TERM}\n").strip()
        folder = self.git("mktree", cwd=self.clone, stdin=f"100644 blob {blob}\tplanted.txt\n").strip()
        result, fetches = self.push("refs/tags/folder", folder)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn("planted.txt:1", result.stderr)
        self.assertEqual(fetches, [])
        self.assertFalse(self.holds(self.clone, self.old_file), "the check fetched the old file")


if __name__ == "__main__":
    unittest.main()
