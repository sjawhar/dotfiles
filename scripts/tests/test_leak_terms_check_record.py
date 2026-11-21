#!/usr/bin/env python3
"""scripts/leak-terms-check's record (dispatch://INFRA-5140): `record --url URL REV...` writes the note `pass <key>` on
each file version (blob) in REV's tree that holds no listed term, in refs/notes/leak-terms-check, and a push to URL then
reads no content of a blob whose note holds its key, while it reads every other file, every message and every path.

Each case makes a work repository whose `main` holds base.txt, and a bare destination that holds that commit. The
check runs in push mode as the pre-push hook runs it, with git's update line on stdin, and GIT_TRACE records each git
call, so the diff producer's calls (`-U0`) show each diff the push read and which paths it read it for.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from unittest import mock

DOTFILES = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DOTFILES / "shims" / "tests"))
from leak_terms_fixture import (  # noqa: E402 - found through the path above
    ALLOWED_TEST_TOKENS,
    SCOPED_TEST_TERMS,
    TEST_TERMS,
    encrypt_yaml,
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


def fixture_plaintext() -> str:
    """The plaintext of the fixture's list, built as leak_terms_fixture builds it, so that a new encryption of it is the
    same list in other bytes."""
    plaintext = "terms: |\n  # test comment\n" + "".join(f"  {term}\n" for term in TEST_TERMS)
    plaintext += "scoped:\n" + "".join(
        f'  "{pattern}": |\n' + "".join(f"    {term}\n" for term in terms) for pattern, terms in SCOPED_TEST_TERMS.items()
    )
    plaintext += "allowed:\n" + "".join(
        f'  "{pattern}": |\n    # a third-party npm package that holds the scoped term\n'
        + "".join(f"    {token}\n" for token in tokens)
        for pattern, tokens in ALLOWED_TEST_TOKENS.items()
    )
    return plaintext


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
                                   capture_output=True, text=True, timeout=60, check=False)
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

    def run_check(self, *arguments: str, stdin: str = "", env: dict[str, str] | None = None
                  ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        """Run the check in the work repository; return its result and, for each call of the diff producer GIT_TRACE
        saw, the pathspecs it was given (none for a commit read whole)."""
        descriptor, trace = tempfile.mkstemp(prefix="trace-", dir=self.root)
        os.close(descriptor)
        result = subprocess.run(
            [str(CHECK), *arguments],
            input=stdin,
            cwd=self.work,
            env={**self.env, **leak_check_environment("/usr/bin:/bin"), "GIT_TRACE": trace, **(env or {})},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        traced = Path(trace).read_text(encoding="utf-8").splitlines()
        # GIT_TRACE saw the push's other git calls, so it would have seen a diff.
        self.assertTrue(any("built-in: git" in line for line in traced), "GIT_TRACE recorded no git call")
        diffs = [shlex.split(line.split("trace: built-in: ", 1)[1]) for line in traced
                 if "built-in: git log" in line and "-U0" in line]
        return result, [words[words.index("--") + 1:] if "--" in words else [] for words in diffs]

    def record(self, *revisions: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return self.run_check("record", "--url", self.url, *revisions, env=env)[0]

    def push(self, commit: str, env: dict[str, str] | None = None
             ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        """The check as the pre-push hook runs it for a push of COMMIT to the destination's main, which holds base."""
        return self.run_check("push", "--remote", "origin", "--url", self.url,
                              stdin=f"refs/heads/main {commit} refs/heads/main {self.base}\n", env=env)

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

    def assert_covered(self, result: subprocess.CompletedProcess[str], covered: int, seen: int) -> None:
        self.assertIn(f"the record {REF} covered {covered} of the {seen} file versions the outgoing commits add or "
                      f"change; read the other {seen - covered}", result.stderr)

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
        self.assertEqual(diffs, [[":(literal)new.txt"]])

    def test_a_file_named_like_a_wildcard_reads_only_itself(self) -> None:
        # The push names the files it reads as literal pathspecs: as a wildcard, `*.txt` would read kept.txt too.
        line = self.key_line()
        commit = self.commit({"kept.txt": f"a forged line with {TERM}\n", "*.txt": "nothing listed\n"})
        self.forge(self.blob("main", "kept.txt"), line)
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_covered(result, 1, 2)
        self.assertEqual(diffs, [[":(literal)*.txt"]])

    def test_a_file_whose_name_holds_a_line_break_is_read_by_its_pathspec(self) -> None:
        # git takes a pathspec from the command line whole, a line feed included. The covered file holds a forged term,
        # so a read of the whole commit would name it; a pathspec that matched nothing would miss the other's term.
        line = self.key_line()
        commit = self.commit({"kept.txt": f"a forged line with {TERM}\n", "we\nird.txt": f"a line with {OTHER_TERM}\n"})
        self.forge(self.blob("main", "kept.txt"), line)
        result, _ = self.push(commit)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {commit[:12]} we\\nird.txt:1", result.stderr)
        self.assertNotIn("kept.txt", result.stderr)
        self.assert_covered(result, 1, 2)

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
        self.assertEqual([spec for chunk in chunks for spec in chunk], [f":(literal){path}" for path in paths])
        self.assertTrue(all(sum(len(spec) + 1 for spec in chunk) <= module.PATHSPEC_BYTES for chunk in chunks))

    def test_record_keeps_allowed_tokens_and_keys_each_destination(self) -> None:
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

    def test_a_covered_file_s_path_is_still_read(self) -> None:
        self.assertEqual(self.record(self.base).returncode, 0)
        # The file's content is base.txt's, which the record covers; its path holds a term.
        commit = self.commit({f"docs/{TERM}.txt": "the first file\n", "other.txt": "nothing listed\n"})
        result, diffs = self.push(commit)
        self.assertEqual(result.returncode, FOUND_EXIT, result.stderr)
        self.assertIn(f"commit {commit[:12]} docs/{TERM}.txt", result.stderr)
        self.assert_covered(result, 1, 2)
        self.assertEqual(diffs, [[":(literal)other.txt"]])

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
        self.assertEqual(diffs, [[":(literal)sub"]])

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
