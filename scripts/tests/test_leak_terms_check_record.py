#!/usr/bin/env python3
"""scripts/leak-terms-check's record (dispatch://INFRA-5140): `record --url URL REV...` writes the note `pass <key>` on
each file version (blob) in REV's tree that holds no listed term, in refs/notes/leak-terms-check, and a push to URL then
reads no content of a blob whose note holds its key, while it reads every other file, every message and every path.

Each case makes a work repository whose `main` holds base.txt, and a bare destination that holds that commit. The
check runs in push mode as the pre-push hook runs it, with git's update line on stdin, and GIT_TRACE2_EVENT records the
arguments of each git call as JSON (a line feed in a path included), so the diff producer's calls (`log … -U0`) show
each diff the push read and which paths it read it for.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Iterator
from unittest import mock

DOTFILES = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DOTFILES / "shims" / "tests"))
from leak_terms_fixture import (  # noqa: E402 - found through the path above
    ALLOWED_TEST_TOKENS,
    SCOPED_TEST_TERMS,
    TEST_TERMS,
    encrypt_yaml,
    fixture_plaintext,
    leak_check_environment,
)

CHECK = DOTFILES / "scripts" / "leak-terms-check"
GIT = "/usr/bin/git"
REF = "refs/notes/leak-terms-check"
TERM = TEST_TERMS[1]
OTHER_TERM = TEST_TERMS[2]
# A term of two words and more, which a soft line break of a prose file can split.
SPLIT_FIRST, SPLIT_REST = TEST_TERMS[0].split(" ", 1)
FOUND_EXIT = 3
RECORD_EXIT = 2
OTHER_KEY = "0" * 64


@lru_cache(maxsize=1)
def load_check() -> ModuleType:
    """scripts/leak-terms-check loaded once as a module, for the cases that run record in this process."""
    loader = importlib.machinery.SourceFileLoader("leak_terms_check_record", str(CHECK))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


class Record(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="leak-terms-record-")
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
        self.work = self.root / "work"
        self.git("init", "-q", "-b", "main", str(self.work), cwd=self.root)
        self.base = self.commit({"base.txt": "the first file\n"})
        self.public = self.root / "public.git"
        self.git("init", "-q", "--bare", "-b", "main", str(self.public), cwd=self.root)
        self.git("push", "-q", str(self.public), "main")
        self.url = f"file://{self.public}"
        self.git("remote", "add", "origin", self.url)

    def git(self, *arguments: str, cwd: Path | None = None, stdin: str | None = None) -> str:
        completed = subprocess.run([GIT, *arguments], cwd=cwd or self.work, env=self.env, input=stdin,
                                   capture_output=True, encoding="utf-8", timeout=60, check=False)
        self.assertEqual(completed.returncode, 0, f"git {' '.join(arguments)}: {completed.stderr}")
        return completed.stdout

    def commit(self, files: dict[str, str], message: str = "write files") -> str:
        for path, content in files.items():
            (self.work / path).parent.mkdir(parents=True, exist_ok=True)
            (self.work / path).write_text(content, encoding="utf-8")
            self.git("add", path)
        self.git("commit", "-q", "-m", message)
        return self.rev("HEAD")

    def rev(self, name: str) -> str:
        return self.git("rev-parse", name).strip()

    def run_check(self, *arguments: str, stdin: str = "", env: dict[str, str] | None = None, cwd: Path | None = None
                  ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        """Run the check in the work repository (in CWD, by default its top); return its result and, for each call of
        the diff producer that GIT_TRACE2_EVENT saw, the pathspecs it was given (none for a commit read whole).
        self.calls keeps the arguments of every git call the check made."""
        descriptor, trace = tempfile.mkstemp(prefix="trace-", dir=self.root)
        os.close(descriptor)
        result = subprocess.run(
            [str(CHECK), *arguments],
            input=stdin,
            cwd=cwd or self.work,
            env={**self.env, **leak_check_environment("/usr/bin:/bin"), "GIT_TRACE2_EVENT": trace, **(env or {})},
            capture_output=True,
            encoding="utf-8",
            timeout=300,
            check=False,
        )
        events = [json.loads(line) for line in Path(trace).read_text(encoding="utf-8").splitlines()]
        self.calls: list[list[str]] = [event["argv"] for event in events if event.get("event") == "start"]
        # The trace saw the check's other git calls, so it would have seen a diff.
        self.assertTrue(self.calls, f"GIT_TRACE2_EVENT recorded no git call; the check exited {result.returncode}: "
                                    f"{result.stderr}")
        diffs = [words for words in self.calls if "log" in words and "-U0" in words]
        return result, [words[words.index("--") + 1:] if "--" in words else [] for words in diffs]

    def record(self, *revisions: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return self.run_check("record", "--url", self.url, *revisions, env=env)[0]

    def push(self, commit: str, env: dict[str, str] | None = None, old: str | None = None, cwd: Path | None = None
             ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        """The check as the pre-push hook runs it for a push of COMMIT to the destination's main, which holds OLD (base
        when not given)."""
        return self.run_check("push", "--remote", "origin", "--url", self.url,
                              stdin=f"refs/heads/main {commit} refs/heads/main {old or self.base}\n", env=env, cwd=cwd)

    def blob(self, revision: str, path: str) -> str:
        return self.rev(f"{revision}:{path}")

    def note(self, object_id: str) -> str | None:
        completed = subprocess.run([GIT, "notes", f"--ref={REF}", "show", object_id], cwd=self.work, env=self.env,
                                   capture_output=True, text=True, timeout=60, check=False)
        return completed.stdout if completed.returncode == 0 else None

    def forge(self, object_id: str, text: str) -> None:
        """Write TEXT as the note of OBJECT_ID, as anyone who can write the ref can."""
        self.git("notes", f"--ref={REF}", "add", "-f", "-m", text.removesuffix("\n"), object_id)

    def key_line(self) -> str:
        """The line `pass <key>` record writes for this destination, read from the note it writes on base.txt."""
        result = self.record(self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        note = self.note(self.blob(self.base, "base.txt"))
        assert note is not None
        (line,) = note.splitlines()
        self.assertRegex(line, r"\Apass [0-9a-f]{64}\Z")
        return line

    def assert_covered(self, result: subprocess.CompletedProcess[str], covered: int, seen: int,
                       read: int | None = None) -> None:
        """The push's summary: the record covered COVERED of the SEEN file versions, and the push read READ of them
        (by default every one it did not cover)."""
        self.assertIn(f"the record {REF} covered {covered} of the {seen} file versions the outgoing commits add or "
                      f"change; the push read {seen - covered if read is None else read} of them", result.stderr)

    def test_a_tag_whose_name_holds_a_no_break_space_or_a_line_separator_is_read(self) -> None:
        # git writes each pre-push update as four fields joined by one space; a ref name holds no ASCII space, but
        # check-ref-format takes a no-break space and a line separator, which str.split() split at, so until 2026-11-22
        # such a push was refused as "malformed pre-push update" (exit 4).
        commit = self.commit({"code.py": "print('nothing listed')\n"})
        for name, character, shown in (("no-break space", "\u00a0", "\\u00a0"),
                                       ("line separator", "\u2028", "\\u2028")):
            with self.subTest(character=name):
                clean = f"refs/tags/v1{character}clean"
                self.git("check-ref-format", clean)
                result, _ = self.run_check("push", "--remote", "origin", "--url", self.url,
                                           stdin=f"{clean} {commit} {clean} {'0' * 40}\n")
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                listed = f"refs/tags/v1{character}{TERM}"
                result, _ = self.run_check("push", "--remote", "origin", "--url", self.url,
                                           stdin=f"{listed} {commit} {listed} {'0' * 40}\n")
                self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
                self.assertIn(f'leak terms: ref v1{shown}{TERM}: "{TERM}" (the global list)', result.stderr)

    def test_no_record_reads_every_file_and_says_nothing_of_it(self) -> None:
        commit = self.commit({"code.py": "print('nothing listed')\n"})
        result, diffs = self.push(commit)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(diffs, [[]])

    def test_a_push_whose_files_the_record_covers_reads_no_diff(self) -> None:
        commit = self.commit({"code.py": "print('nothing listed')\n", "notes.md": "A paragraph\nof prose.\n",
                              "data/values.txt": "1 2 3\n"})
        # Control: before the record, the push reads the commit's diff.
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(diffs, [[]])

        result = self.record("main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("4 file versions in the trees of 1 commits: found 0 covered, read 4, recorded 4, did not record 0",
                      result.stdout)
        for path in ("base.txt", "code.py", "notes.md", "data/values.txt"):
            self.assertRegex(self.note(self.blob("main", path)) or "", r"\Apass [0-9a-f]{64}\n\Z")

        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 3, 3)
        self.assertEqual(diffs, [])

        # A second run finds every file covered and reads none.
        result = self.record("main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("found 4 covered, read 0, recorded 0, did not record 0", result.stdout)

    def test_one_more_commit_with_a_planted_term_is_refused(self) -> None:
        covered = self.commit({"code.py": "print('nothing listed')\n", "notes.md": "A paragraph\nof prose.\n"})
        self.assertEqual(self.record("main").returncode, 0)
        planted = self.commit({"planted.txt": f"a line with {TERM}\n"})
        result, diffs = self.push(planted)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {planted[:12]} planted.txt:1", result.stderr)
        self.assert_covered(result, 2, 3)
        # The planted commit has no covered file, so it is read whole; the covered one is not read.
        self.assertEqual(diffs, [[]])
        self.assertNotIn(covered[:12], result.stderr)

    def test_a_forged_note_is_trusted_as_written(self) -> None:
        line = self.key_line()
        commit = self.commit({"secret.txt": f"a line with {TERM}\n"})
        # Control: the record holds no note on the file, so the push reads it and refuses it.
        result, _ = self.push(commit)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn("secret.txt:1", result.stderr)
        self.assert_covered(result, 0, 1)
        # record does not record it.
        result = self.record("main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("recorded 0, did not record 1", result.stdout)
        # Whoever can write the ref can make the push skip the file: the threat model, with no protection.
        self.forge(self.blob("main", "secret.txt"), line)
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 1, 1)
        self.assertEqual(diffs, [])

    def test_a_note_of_another_key_and_a_changed_term_list_cover_nothing(self) -> None:
        commit = self.commit({"code.py": "print('nothing listed')\n", "notes.md": "A paragraph\nof prose.\n"})
        self.assertEqual(self.record("main").returncode, 0)
        # Control: the record covers both files.
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 2, 2)
        self.assertEqual(diffs, [])

        # A list that holds the same terms in other encrypted bytes is another key.
        other_list = encrypt_yaml(fixture_plaintext())
        other = {"DOTFILES_LEAK_TERMS_FILE": str(other_list)}
        result, diffs = self.push(commit, env=other)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 0, 2)
        self.assertEqual(diffs, [[]])
        # record under the new list reads every file again, and keeps the old key's line before its own.
        result = self.record("main", env=other)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("found 0 covered, read 3, recorded 3", result.stdout)
        self.assertEqual(len((self.note(self.blob("main", "code.py")) or "").splitlines()), 2)

        # A note whose only line names another key covers nothing.
        for path in ("code.py", "notes.md"):
            self.forge(self.blob("main", path), f"pass {OTHER_KEY}")
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 0, 2)
        self.assertEqual(diffs, [[]])

    def test_a_record_ref_that_names_a_tree_covers_nothing_and_says_why(self) -> None:
        commit = self.commit({"secret.txt": f"a line with {TERM}\n"})
        self.git("update-ref", REF, self.rev("HEAD^{tree}"))
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"leak terms: cannot use the record {REF}, so every file version is read: it names a tree, not "
                      "a commit", result.stderr)
        self.assertIn("secret.txt:1", result.stderr)
        self.assertEqual(diffs, [[]])
        result = self.record("main")
        self.assertEqual(result.returncode, RECORD_EXIT, result.stdout)
        self.assertIn("it names a tree, not a commit; nothing was recorded", result.stderr)
        self.assertEqual(self.rev(REF), self.rev("HEAD^{tree}"))

    def test_a_commit_with_a_covered_file_reads_only_its_other_files(self) -> None:
        line = self.key_line()
        commit = self.commit({"kept.txt": f"a forged line with {TERM}\n", "new.txt": f"a line with {OTHER_TERM}\n"})
        # The forged note shows that the covered file's content is not read, whatever it holds.
        self.forge(self.blob("main", "kept.txt"), line)
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {commit[:12]} new.txt:1", result.stderr)
        self.assertNotIn("kept.txt", result.stderr)
        self.assert_covered(result, 1, 2)
        self.assertEqual(diffs, [[":(top,literal)new.txt"]])

    def test_a_file_named_like_a_wildcard_reads_only_itself(self) -> None:
        # The push names the files it reads as literal pathspecs: as a wildcard, `*.txt` would read kept.txt too.
        line = self.key_line()
        commit = self.commit({"kept.txt": f"a forged line with {TERM}\n", "*.txt": "nothing listed\n"})
        self.forge(self.blob("main", "kept.txt"), line)
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 1, 2)
        self.assertEqual(diffs, [[":(top,literal)*.txt"]])

    def test_a_file_whose_name_holds_a_line_break_is_read_by_its_pathspec(self) -> None:
        # git takes a pathspec from the command line whole, a line feed included. The covered file holds a forged term,
        # so a read of the whole commit would name it; a pathspec that matched nothing would miss the other's term.
        line = self.key_line()
        commit = self.commit({"kept.txt": f"a forged line with {TERM}\n", "we\nird.txt": f"a line with {OTHER_TERM}\n"})
        self.forge(self.blob("main", "kept.txt"), line)
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {commit[:12]} we\\nird.txt:1", result.stderr)
        self.assertNotIn("kept.txt", result.stderr)
        self.assert_covered(result, 1, 2)
        self.assertEqual(diffs, [[":(top,literal)we\nird.txt"]])

    def test_a_rename_is_not_followed_under_log_follow(self) -> None:
        # With log.follow on, git follows a rename when one pathspec is given, even under --no-renames: a text file
        # renamed from a binary one the destination holds showed as "Binary files differ", and its term went unread.
        self.assertEqual(self.record(self.base).returncode, 0)
        text = "".join(f"line {number} of the file\n" for number in range(40)) + f"a line with {TERM}\n"
        (self.work / "old.bin").write_bytes(b"\0" + text.encode())
        self.git("add", "old.bin")
        self.git("commit", "-q", "-m", "a binary file")
        held = self.rev("HEAD")
        self.git("push", "-q", str(self.public), "main")
        self.git("rm", "-q", "old.bin")
        commit = self.commit({"new.txt": text, "kept.txt": "the first file\n"})
        follow = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "log.follow", "GIT_CONFIG_VALUE_0": "true"}
        result, diffs = self.push(commit, env=follow, old=held)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {commit[:12]} new.txt:41", result.stderr)
        self.assert_covered(result, 1, 2)
        self.assertEqual(diffs, [[":(top,literal)new.txt"]])

    def test_a_push_under_literal_pathspecs_reads_the_files_it_names(self) -> None:
        # `git --literal-pathspecs push` (Magit's default) exports GIT_LITERAL_PATHSPECS to the hook: git read
        # `:(literal)new.txt` as a file of that name, matched nothing, and a clean push was refused (exit 4).
        literal = {"GIT_LITERAL_PATHSPECS": "1"}
        self.assertEqual(self.record(self.base).returncode, 0)
        clean = self.commit({"kept.txt": "the first file\n", "new.txt": "nothing listed\n"})
        result, diffs = self.push(clean, env=literal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 1, 2)
        self.assertEqual(diffs, [[":(top,literal)new.txt"]])
        planted = self.commit({"copy.txt": "the first file\n", "planted.txt": f"a line with {TERM}\n"})
        result, _ = self.push(planted, env=literal)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {planted[:12]} planted.txt:1", result.stderr)

    def test_a_commit_with_fewer_covered_files_than_others_is_read_whole(self) -> None:
        # A diff call of its own would cost more than the covered file saves; reading it is only stricter.
        self.assertEqual(self.record(self.base).returncode, 0)
        commit = self.commit({"kept.txt": "the first file\n", "a.txt": "nothing listed\n", "b.txt": "nor here\n"})
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 1, 3, read=3)
        self.assertEqual(diffs, [[]])

    def test_many_commits_with_covered_files_are_read_in_one_call(self) -> None:
        # The uncovered files of every commit some of whose files are covered are read in one diff call, and the empty
        # attribute tree and info/attributes once for the push: a call for each commit made a push of 400 such commits
        # take 17 s with the record, against 0.5 s without it.
        self.assertEqual(self.record(self.base).returncode, 0)
        commits = [self.commit({f"copy{number}.txt": "the first file\n", f"new{number}.txt": f"file {number}\n"})
                   for number in range(3)]
        result, diffs = self.push(commits[-1])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 1, 4)
        self.assertEqual([sorted(diff) for diff in diffs], [[f":(top,literal)new{number}.txt" for number in range(3)]])
        self.assertEqual(sum(words[1:2] == ["mktree"] for words in self.calls), 1, self.calls)
        self.assertEqual(sum("info/attributes" in words for words in self.calls), 1, self.calls)

    def test_a_folder_one_commit_names_is_read_in_another(self) -> None:
        # One commit adds the file `d`; another, on a branch of its own, adds the covered file `d/f`. The pathspec of
        # `d` names `d/f` too, as git reads it, so the second commit's `d/f` is read in the same call: only stricter.
        self.assertEqual(self.record(self.base).returncode, 0)
        first = self.commit({"d": "nothing listed\n", "copy.txt": "the first file\n"})
        self.git("checkout", "-q", "-b", "other", self.base)
        second = self.commit({"d/f": "the first file\n", "copy2.txt": "the first file\n"})
        result, diffs = self.run_check("push", "--remote", "origin", "--url", self.url,
                                       stdin=f"refs/heads/main {first} refs/heads/main {self.base}\n"
                                             f"refs/heads/other {second} refs/heads/other {'0' * 40}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 1, 2, read=2)
        self.assertEqual(diffs, [[":(top,literal)d"]])

    def test_a_literal_pathspec_reaches_its_file_and_the_files_in_its_folder(self) -> None:
        module = load_check()
        self.assertTrue(module.pathspec_reaches({"d"}, "d"))
        self.assertTrue(module.pathspec_reaches({"d"}, "d/f"))
        self.assertTrue(module.pathspec_reaches({"d/e"}, "d/e/f/g"))
        self.assertFalse(module.pathspec_reaches({"d"}, "dx"))
        self.assertFalse(module.pathspec_reaches({"d/f"}, "d"))
        self.assertFalse(module.pathspec_reaches({"d/e"}, "d/ef"))

    def test_a_commit_whose_files_are_covered_or_deleted_reads_no_diff(self) -> None:
        self.commit({"gone.txt": "to be deleted\n"})
        self.assertEqual(self.record(self.base).returncode, 0)
        self.git("rm", "-q", "gone.txt")
        commit = self.commit({"copy.txt": "the first file\n"})
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The first commit, which adds gone.txt, is read whole; the second deletes it and adds a covered version.
        self.assert_covered(result, 1, 2)
        self.assertEqual(diffs, [[]])

    def test_pathspecs_are_cut_into_calls_far_under_the_argument_limit(self) -> None:
        module = load_check()
        paths = [f"dir/{index:05d}-{'x' * 200}.txt" for index in range(2000)]
        chunks = list(module.pathspec_chunks(paths))
        self.assertGreater(len(chunks), 1)
        self.assertEqual([path for chunk in chunks for path in chunk], paths)
        self.assertTrue(all(sum(len(f":(top,literal){path}") + 1 for path in chunk) <= module.PATHSPEC_BYTES
                            for chunk in chunks))

    def test_record_keeps_allowed_tokens_and_keys_the_lists_a_destination_reads(self) -> None:
        (pattern, (token, _)), = ALLOWED_TEST_TOKENS.items()
        (scoped_term,), = SCOPED_TEST_TERMS.values()
        self.commit({"package.json": f'{{"dependency": "{token}"}}\n', "scoped.txt": f"a line with {scoped_term}\n"})
        # A file:// URL is no GitHub repository, so it reads no scoped list, and every file is recorded.
        result = self.record("main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("found 0 covered, read 3, recorded 3, did not record 0", result.stdout)
        # The repository the token is allowed in reads the scoped list under another key, so record reads every file
        # again. It records neither the scoped term nor the token, which a push there reads as no match: stricter.
        result, _ = self.run_check("record", "--url", f"https://github.com/{pattern}.git", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("found 0 covered, read 3, recorded 1, did not record 2", result.stdout)

    def test_destinations_that_read_the_same_lists_share_a_key(self) -> None:
        # The key names the scoped lists a destination reads, not the destination: a GitHub repository no scoped list
        # reaches reads what the file:// destination reads, so its record covers a push there.
        commit = self.commit({"code.py": "print('nothing listed')\n"})
        result, _ = self.run_check("record", "--url", "https://github.com/upstream-fixture/elsewhere.git", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 1, 1)
        self.assertEqual(diffs, [])

    def test_a_branch_named_like_the_record_leaves_the_record_used(self) -> None:
        # rev-parse reads refs/notes/leak-terms-check as ambiguous beside refs/heads/refs/notes/leak-terms-check; the
        # record is the ref of exactly that name.
        line = self.key_line()
        commit = self.commit({"kept.txt": f"a forged line with {TERM}\n"})
        self.forge(self.blob("main", "kept.txt"), line)
        self.git("update-ref", f"refs/heads/{REF}", self.base)
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 1, 1)
        self.assertEqual(diffs, [])

    def test_a_tag_or_branch_named_like_the_record_covers_nothing(self) -> None:
        line = self.key_line()
        commit = self.commit({"kept.txt": f"a forged line with {TERM}\n"})
        self.forge(self.blob("main", "kept.txt"), line)
        notes = self.rev(REF)
        self.git("update-ref", "-d", REF)
        for name in (f"refs/tags/{REF}", f"refs/heads/{REF}"):
            with self.subTest(name=name):
                self.git("update-ref", name, notes)
                result, diffs = self.push(commit)
                self.git("update-ref", "-d", name)
                self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
                self.assertIn("kept.txt:1", result.stderr)
                self.assertNotIn("the record", result.stderr)
                self.assertEqual(diffs, [[]])

    def test_a_symbolic_record_ref_covers_nothing_and_says_why(self) -> None:
        # The line names the ref it points to, escaped: a ref name may hold U+009B, which a terminal takes as the start
        # of a command, and until 2026-11-22 it went to the terminal raw.
        line = self.key_line()
        commit = self.commit({"kept.txt": f"a forged line with {TERM}\n"})
        self.forge(self.blob("main", "kept.txt"), line)
        notes = self.rev(REF)
        self.git("update-ref", "-d", REF)
        for target, shown in (("refs/notes/other", "refs/notes/other"),
                              ("refs/notes/x\u009by", "refs/notes/x\\u009by")):
            with self.subTest(target=shown):
                self.git("update-ref", target, notes)
                self.git("symbolic-ref", REF, target)
                result, diffs = self.push(commit)
                self.git("symbolic-ref", "--delete", REF)
                self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
                self.assertIn(f"leak terms: cannot use the record {REF}, so every file version is read: it is a "
                              f"symbolic ref to {shown}, not a ref of its own", result.stderr)
                self.assertNotIn("\u009b", result.stderr)
                self.assertIn("kept.txt:1", result.stderr)
                self.assertEqual(diffs, [[]])

    def test_a_symbolic_record_ref_to_no_ref_covers_nothing_and_says_why(self) -> None:
        # for-each-ref lists no symbolic ref whose target is missing; show-ref --exists finds it.
        commit = self.commit({"code.py": "print('nothing listed')\n"})
        self.git("symbolic-ref", REF, "refs/notes/nothing")
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"leak terms: cannot use the record {REF}, so every file version is read: git finds the ref but "
                      "lists no object for it: it is a symbolic ref to no ref", result.stderr)
        self.assertEqual(diffs, [[]])

    def test_a_broken_record_ref_covers_nothing_and_says_why(self) -> None:
        # A loose ref file whose text names no object: for-each-ref only warns on its standard error and lists nothing,
        # so until 2026-11-22 the push said nothing of it.
        commit = self.commit({"code.py": "print('nothing listed')\n"})
        broken = self.work / ".git" / REF
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("not an object id\n", encoding="utf-8")
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"leak terms: cannot use the record {REF}, so every file version is read: git cannot read the "
                      "ref (git show-ref --exists exit 1: error: ", result.stderr)
        self.assertEqual(diffs, [[]])
        result = self.record("main")
        self.assertEqual(result.returncode, RECORD_EXIT, result.stdout)
        self.assertIn("git cannot read the ref (git show-ref --exists exit 1: error: ", result.stderr)
        self.assertIn("; nothing was recorded", result.stderr)
        self.assertEqual(broken.read_text(encoding="utf-8"), "not an object id\n")

    def test_a_folder_of_refs_named_like_the_record_covers_nothing_and_says_git_s_reason(self) -> None:
        # No ref is named refs/notes/leak-terms-check, but refs/notes/leak-terms-check/inner makes the name a folder, so
        # git cannot read the name (and record could not write it). The push says why in git's words, read with every
        # trace off: under GIT_TRACE=1, or with trace2 targets in git's global config (which trace2 reads from the
        # global and system config only), trace lines went to the same standard error as git's own message, and the
        # line's 200-character bound then cut git's message out.
        commit = self.commit({"code.py": "print('nothing listed')\n"})
        self.git("update-ref", f"{REF}/inner", self.base)
        global_config = self.root / "trace2.gitconfig"
        for key in ("trace2.normalTarget", "trace2.perfTarget", "trace2.eventTarget"):
            self.git("config", "-f", str(global_config), key, "2")
        for name, environment in (("GIT_TRACE=1", {"GIT_TRACE": "1"}),
                                  ("trace2 targets in the global config", {"GIT_CONFIG_GLOBAL": str(global_config)})):
            with self.subTest(name=name):
                result, diffs = self.push(commit, env=environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                (line,) = [line for line in result.stderr.splitlines() if "cannot use the record" in line]
                self.assertEqual(line, f"leak terms: cannot use the record {REF}, so every file version is read: git "
                                       "cannot read the ref (git show-ref --exists exit 1: error: failed to look up "
                                       "reference: Is a directory)")
                self.assertEqual(diffs, [[]])
                result, _ = self.run_check("record", "--url", self.url, "main", env=environment)
                self.assertEqual(result.returncode, RECORD_EXIT, result.stdout)
                (line,) = [line for line in result.stderr.splitlines() if "nothing was recorded" in line]
                self.assertTrue(line.endswith("error: failed to look up reference: Is a directory); nothing was "
                                              "recorded"), line)
        self.assertEqual(self.rev(f"{REF}/inner"), self.base)

    @contextlib.contextmanager
    def worktree(self, name: str) -> Iterator[dict[str, str]]:
        """The environment of one way git learns the working tree when the check runs from its subdirectory `sub`:
        GIT_DIR with GIT_WORK_TREE, as shims/jj passes them on; GIT_DIR with core.worktree in the repository's config;
        and plain discovery."""
        git_dir = {"GIT_DIR": str(self.work / ".git")}
        if name == "core.worktree":
            self.git("config", "core.worktree", str(self.work))
            try:
                yield git_dir
            finally:
                self.git("config", "--unset", "core.worktree")
        else:
            yield {"GIT_WORK_TREE": {**git_dir, "GIT_WORK_TREE": str(self.work)}, "discovery": {}}[name]

    WORKTREES = ("GIT_WORK_TREE", "core.worktree", "discovery")

    def test_a_push_from_a_subdirectory_reads_a_clean_commit_from_the_top(self) -> None:
        # `:(literal)x.txt` read from `sub` named `sub/x.txt`, which no commit holds: git showed no commit, and a clean
        # push was refused as "malformed commit boundaries" (exit 4).
        self.assertEqual(self.record(self.base).returncode, 0)
        clean = self.commit({"x.txt": "nothing listed\n", "c1.txt": "the first file\n", "c2.txt": "the first file\n"})
        (self.work / "sub").mkdir()
        for name in self.WORKTREES:
            with self.subTest(name=name), self.worktree(name) as environment:
                result, diffs = self.push(clean, env=environment, cwd=self.work / "sub")
                self.assertEqual(result.returncode, 0, result.stderr)
                # c1.txt and c2.txt are one version, base.txt's.
                self.assert_covered(result, 1, 2)
                self.assertEqual(diffs, [[":(top,literal)x.txt"]])

    def test_a_push_from_a_subdirectory_reads_the_top_file_not_its_namesake(self) -> None:
        # `:(literal)a.txt` read from `sub` named the covered `sub/a.txt`, and the term in the top's `a.txt` went unread.
        self.assertEqual(self.record(self.base).returncode, 0)
        planted = self.commit({"a.txt": f"a line with {TERM}\n", "sub/a.txt": "the first file\n",
                               "c3.txt": "the first file\n"})
        for name in self.WORKTREES:
            with self.subTest(name=name), self.worktree(name) as environment:
                result, diffs = self.push(planted, env=environment, cwd=self.work / "sub")
                self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
                self.assertIn(f"commit {planted[:12]} a.txt:1", result.stderr)
                self.assert_covered(result, 1, 2)
                self.assertEqual(diffs, [[":(top,literal)a.txt"]])

    def test_a_folder_pushed_by_itself_from_a_subdirectory_is_read_whole(self) -> None:
        # ls-tree of a folder object lists only the part under the directory it runs in, unless --full-tree.
        commit = self.commit({"top.txt": f"a line with {TERM}\n", "sub/inner.txt": "nothing listed\n"})
        tree = self.rev(f"{commit}^{{tree}}")
        for name in self.WORKTREES:
            with self.subTest(name=name), self.worktree(name) as environment:
                result, _ = self.run_check("push", "--remote", "origin", "--url", self.url,
                                           stdin=f"refs/tags/a-folder {tree} refs/tags/a-folder {'0' * 40}\n",
                                           env=environment, cwd=self.work / "sub")
                self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
                self.assertIn("top.txt:1", result.stderr)

    def test_a_covered_file_s_path_is_still_read(self) -> None:
        self.assertEqual(self.record(self.base).returncode, 0)
        # The file's content is base.txt's, which the record covers; its path holds a term.
        commit = self.commit({f"docs/{TERM}.txt": "the first file\n", "other.txt": "nothing listed\n"})
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {commit[:12]} docs/{TERM}.txt", result.stderr)
        self.assert_covered(result, 1, 2)
        self.assertEqual(diffs, [[":(top,literal)other.txt"]])

    def test_a_gitlink_is_read_whatever_its_note(self) -> None:
        line = self.key_line()
        # One commit adds copy.txt, base.txt's version, which the record covers, and a gitlink.
        (self.work / "copy.txt").write_text("the first file\n", encoding="utf-8")
        self.git("add", "copy.txt")
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.base},sub")
        self.git("commit", "-q", "-m", "add a submodule")
        commit = self.rev("HEAD")
        # A note with the key on the commit the gitlink names, which a writer of the ref can add, covers nothing: the
        # diff of the gitlink, its `Subproject commit` line, is still read.
        self.forge(self.base, line)
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The gitlink is no file version, so the summary does not count it.
        self.assert_covered(result, 1, 1)
        self.assertEqual(diffs, [[":(top,literal)sub"]])

    def merge(self, content: str) -> str:
        """A merge of two branches from base that each write shared.txt, whose own version of it is CONTENT."""
        self.git("checkout", "-q", "-b", "left", self.base)
        self.commit({"shared.txt": "left\n"})
        self.git("checkout", "-q", "-b", "right", self.base)
        self.commit({"shared.txt": "right\n"})
        self.git("checkout", "-q", "left")
        self.git("merge", "-q", "--no-commit", "-s", "ours", "right")
        return self.commit({"shared.txt": content}, "merge")

    def test_a_merge_whose_version_is_covered_skips_its_lines(self) -> None:
        line = self.key_line()
        merge = self.merge(f"left\nright\na line with {TERM}\n")
        # Control: the merge's own line is read and refused.
        result, _ = self.push(merge)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {merge[:12]} shared.txt:3", result.stderr)
        self.assert_covered(result, 0, 3)
        self.forge(self.blob(merge, "shared.txt"), line)
        result, _ = self.push(merge)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 1, 3)

    def test_a_merge_still_reads_the_path_of_a_covered_version(self) -> None:
        line = self.key_line()
        merge = self.merge("left\nright\n")
        self.git("mv", "shared.txt", f"{TERM}.txt")
        self.git("commit", "-q", "--amend", "--no-edit")
        merge = self.rev("HEAD")
        self.forge(self.blob(merge, f"{TERM}.txt"), line)
        result, _ = self.push(merge)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {merge[:12]} {TERM}.txt", result.stderr)

    def test_a_term_a_soft_line_break_splits_in_prose_is_not_recorded(self) -> None:
        commit = self.commit({"doc.md": f"A paragraph that ends with {SPLIT_FIRST}\n{SPLIT_REST} on its next line.\n"})
        result = self.record("main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("read 2, recorded 1, did not record 1", result.stdout)
        self.assertIsNone(self.note(self.blob("main", "doc.md")))
        result, _ = self.push(commit)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {commit[:12]} doc.md:1", result.stderr)

    def test_a_note_keeps_its_last_eight_keys(self) -> None:
        blob = self.blob(self.base, "base.txt")
        older = [f"pass {index:064x}" for index in range(1, 9)]
        self.forge(blob, "\n".join(older))
        result = self.record(self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = (self.note(blob) or "").splitlines()
        self.assertEqual(lines[:7], older[1:])
        self.assertEqual(len(lines), 8)
        self.assertRegex(lines[7], r"\Apass [0-9a-f]{64}\Z")
        self.assertNotIn(lines[7], older)

    def record_overtaken(self) -> tuple[int, str]:
        """Run record on main in this process, with another writer that moves the ref after the run read it (the other
        writer forges base.txt's note): return record's exit and the ref's commit after the other writer."""
        module = load_check()
        original = module.read_record
        moved: list[str] = []

        def read_then_overtaken() -> object:
            record = original()
            self.forge(self.blob(self.base, "base.txt"), "pass from another writer")
            moved.append(self.rev(REF))
            return record

        environment = {**self.env, **leak_check_environment("/usr/bin:/bin")}
        previous = Path.cwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, previous)
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(module, "read_record", read_then_overtaken), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as stderr:
            status = module.record_command(self.url, ["main"])
        self.assertIn("another writer moved it since this run read it", stderr.getvalue())
        return status, moved[0]

    def test_a_writer_another_overtook_records_nothing(self) -> None:
        self.assertEqual(self.record(self.base).returncode, 0)
        self.commit({"code.py": "print('nothing listed')\n"})
        status, other = self.record_overtaken()
        self.assertEqual(status, RECORD_EXIT)
        self.assertEqual(self.rev(REF), other)
        self.assertEqual(self.note(self.blob(self.base, "base.txt")), "pass from another writer\n")
        self.assertIsNone(self.note(self.blob("main", "code.py")))

    def test_a_first_writer_another_overtook_records_nothing(self) -> None:
        self.commit({"code.py": "print('nothing listed')\n"})
        status, other = self.record_overtaken()
        self.assertEqual(status, RECORD_EXIT)
        self.assertEqual(self.rev(REF), other)
        self.assertIsNone(self.note(self.blob("main", "code.py")))


if __name__ == "__main__":
    unittest.main()
