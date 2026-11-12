#!/usr/bin/env python3
"""The leak-term scanner against an encrypted, per-process test list."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leak_terms_fixture import TEST_TERMS, encrypt_yaml, fixture_environment, unrelated_key_file


ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts" / "leak-terms-check"


def real_jj() -> str:
    located = subprocess.run(["mise", "which", "jj"], capture_output=True, text=True, check=False)
    if located.returncode == 0 and located.stdout.strip():
        return located.stdout.strip()
    for directory in os.environ["PATH"].split(os.pathsep):
        candidate = Path(directory) / "jj"
        if candidate.is_file() and os.access(candidate, os.X_OK) and candidate.resolve() != ROOT / "shims" / "jj":
            return str(candidate)
    raise RuntimeError("jj is required for scanner tests")


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env = {**os.environ, **fixture_environment()}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def check(self, mode: str, text: str, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(CHECK), mode, *arguments],
            input=text,
            capture_output=True,
            text=True,
            env=self.env if env is None else env,
            cwd=ROOT,
            check=False,
        )

    def assert_terms_hidden(self, result: subprocess.CompletedProcess[str]) -> None:
        output = (result.stdout + result.stderr).casefold()
        for term in TEST_TERMS:
            self.assertNotIn(term.casefold(), output)

    def assert_found(self, result: subprocess.CompletedProcess[str], place: str) -> None:
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn(f"leak terms: {place}: listed leak term", result.stderr)
        self.assertIn("sops edit leak-terms.sops.yaml", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_terms_hidden(result)

    def test_scanner_casefolds_and_accepts_space_or_tab_runs(self) -> None:
        spaced = self.check("message", "quillfeather   LANE 9\n")
        tabbed = self.check("message", "quillfeather\tLANE 9\n")
        self.assert_found(spaced, "commit message line 1")
        self.assert_found(tabbed, "commit message line 1")
        for text in ("Quillfeather Lane\n", "Quillfeather Lane 8\n"):
            result = self.check("message", text)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_terms_hidden(result)

    def test_scanner_reads_git_hunks_and_added_plus_content(self) -> None:
        diff = """diff --git a/note.txt b/note.txt
index 0000000..1111111 100644
--- a/note.txt
+++ b/note.txt
@@ -3,0 +4,2 @@
+before
++ Quillfeather Lane 9
"""
        result = self.check("diff", diff)
        self.assert_found(result, "note.txt:5")

    def test_scanner_treats_only_newlines_as_diff_line_breaks(self) -> None:
        clean = (
            "diff --git a/plain.txt b/plain.txt\n"
            "--- a/plain.txt\n"
            "+++ b/plain.txt\n"
            "@@ -0,0 +1,2 @@\n"
            "+a" + chr(12) + "b\n"
            "+a" + chr(13) + "b\n"
        )
        split_term = (
            "diff --git a/plain.txt b/plain.txt\n"
            "--- a/plain.txt\n"
            "+++ b/plain.txt\n"
            "@@ -0,0 +7 @@\n"
            "+x" + chr(12) + "Quillfeather Lane 9\n"
        )
        result = self.check("diff", clean)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_terms_hidden(result)
        self.assert_found(self.check("diff", split_term), "plain.txt:7")

    def test_scanner_reads_a_real_git_c_quoted_path(self) -> None:
        repository = self.root / "git"
        repository.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q"], cwd=repository, check=True)
        path = repository / "notes"
        path.mkdir()
        (path / 'zorbleplex"file.txt').write_text("clean\n", encoding="utf-8")
        subprocess.run(["/usr/bin/git", "add", "."], cwd=repository, check=True)
        diff = subprocess.run(
            ["/usr/bin/git", "-c", "core.quotePath=false", "diff", "--cached", "-U0", "--no-ext-diff", "--no-textconv", "--no-color"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertIn('"a/notes/', diff)
        result = self.check("diff", diff)
        self.assert_found(result, 'notes/<listed term>"file.txt')

    def test_scanner_reads_real_paths_that_contain_the_header_separator(self) -> None:
        repository = self.root / "ambiguous-path"
        repository.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q"], cwd=repository, check=True)
        subprocess.run([real_jj(), "git", "init", "--colocate"], cwd=repository, check=True, capture_output=True, text=True)
        changed = repository / "dir b" / "notes.txt"
        listed = repository / "x b" / "zorbleplex.txt"
        changed.parent.mkdir()
        listed.parent.mkdir()
        changed.write_text("Quillfeather Lane 9\n", encoding="utf-8")
        listed.write_text("clean\n", encoding="utf-8")
        subprocess.run(["/usr/bin/git", "add", "."], cwd=repository, check=True)
        git_diff = subprocess.run(
            ["/usr/bin/git", "-c", "core.quotePath=false", "diff", "--cached", "-U0", "--no-ext-diff", "--no-textconv", "--no-color"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        jj_diff = subprocess.run(
            [real_jj(), "--no-pager", "--color=never", "diff", "--git", "-r", "@"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for diff in (git_diff, jj_diff):
            self.assertIn("diff --git a/dir b/notes.txt b/dir b/notes.txt", diff)
            result = self.check("diff", diff)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("leak terms: dir b/notes.txt:1: listed leak term", result.stderr)
            self.assertIn("leak terms: x b/<listed term>.txt: listed leak term", result.stderr)
            self.assert_terms_hidden(result)

    def test_scanner_reads_result_paths_but_not_deleted_or_renamed_away_paths(self) -> None:
        deleted = """diff --git a/zorbleplex.txt b/zorbleplex.txt
deleted file mode 100644
index 1111111..0000000
--- a/zorbleplex.txt
+++ /dev/null
@@ -1 +0,0 @@
-clean
"""
        renamed_away = """diff --git a/zorbleplex.txt b/clean.txt
similarity index 100%
rename from zorbleplex.txt
rename to clean.txt
"""
        renamed_to = """diff --git a/clean.txt b/zorbleplex.txt
similarity index 100%
rename from clean.txt
rename to zorbleplex.txt
"""
        for diff in (deleted, renamed_away):
            result = self.check("diff", diff)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_terms_hidden(result)
        self.assert_found(self.check("diff", renamed_to), "<listed term>.txt")

    def test_scanner_reads_binary_paths_not_binary_payloads(self) -> None:
        binary_path = """diff --git a/zorbleplex.bin b/zorbleplex.bin
Binary files a/zorbleplex.bin and b/zorbleplex.bin differ
"""
        binary_payload = """diff --git a/plain.bin b/plain.bin
GIT binary patch
literal 3
Quillfeather Lane 9
"""
        self.assert_found(self.check("diff", binary_path), "<listed term>.bin")
        result = self.check("diff", binary_payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_terms_hidden(result)

    def test_scanner_reads_a_real_jj_raw_path_with_controls(self) -> None:
        repository = self.root / "jj"
        repository.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q"], cwd=repository, check=True)
        subprocess.run([real_jj(), "git", "init", "--colocate"], cwd=repository, check=True, capture_output=True, text=True)
        filename = 'raw-"\t\\\nzorbleplex.txt'
        (repository / filename).write_text("clean\n", encoding="utf-8")
        generated = subprocess.run(
            [real_jj(), "--no-pager", "--color=never", "diff", "--git", "-r", "@"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("diff --git", generated.stdout)
        result = self.check("diff", generated.stdout)
        self.assert_found(result, 'raw-"\\t\\\\\\n<listed term>.txt')

    def test_scanner_reads_combined_diffs_and_ignores_lines_from_one_parent(self) -> None:
        added = """diff --cc merge.txt
index 0000000,0000000..1111111
--- a/merge.txt
+++ b/merge.txt
@@@ -1,0 -1,0 +1,1 @@@
++Quillfeather Lane 9
"""
        from_one_parent = """diff --cc merge.txt
index 0000000,0000000..1111111
--- a/merge.txt
+++ b/merge.txt
@@@ -1,1 -1,0 +1,1 @@@
 +Quillfeather Lane 9
"""
        octopus = """diff --cc octopus.txt
index 0000000,0000000,0000000..1111111
--- a/octopus.txt
+++ b/octopus.txt
@@@@ -1,0 -1,0 -1,0 +1,1 @@@@
+++Quillfeather Lane 9
"""
        self.assert_found(self.check("diff", added), "merge.txt:1")
        clean = self.check("diff", from_one_parent)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assert_terms_hidden(clean)
        self.assert_found(self.check("diff", octopus), "octopus.txt:1")

    def test_scanner_refuses_a_malformed_hunk_count(self) -> None:
        malformed = """diff --git a/plain.txt b/plain.txt
--- a/plain.txt
+++ b/plain.txt
@@ -1,2 +1,1 @@
+clean
"""
        result = self.check("diff", malformed)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("malformed unified hunk count", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_terms_hidden(result)

    def test_scanner_applies_message_cleanups_and_final_line_numbers(self) -> None:
        strip_known = self.check("message", "# Quillfeather Lane 9\n", "--cleanup", "strip", "--comment-char", "#")
        strip_unknown = self.check("message", "# Quillfeather Lane 9\n", "--cleanup", "strip")
        whitespace = self.check("message", "# Quillfeather Lane 9\n", "--cleanup", "whitespace")
        verbatim = self.check("message", "# Quillfeather Lane 9\n", "--cleanup", "verbatim")
        scissors = self.check(
            "message",
            "keep\n# ------------------------ >8 ------------------------\nQuillfeather Lane 9\n",
            "--cleanup",
            "scissors",
            "--comment-char",
            "#",
        )
        cut = self.check(
            "message",
            "keep\n# ------------------------ >8 ------------------------\nQuillfeather Lane 9\n",
            "--cut-at-scissors",
            "--comment-char",
            "#",
        )
        unknown_scissors = self.check(
            "message",
            "keep\n# ------------------------ >8 ------------------------\nQuillfeather Lane 9\n",
            "--cleanup",
            "scissors",
        )
        unknown_cut = self.check(
            "message",
            "keep\n# ------------------------ >8 ------------------------\nQuillfeather Lane 9\n",
            "--cut-at-scissors",
        )
        git_whitespace = self.check(
            "message",
            "keep" + chr(11) + chr(12) + chr(13) + "\nQuillfeather Lane 9\n",
            "--cleanup",
            "whitespace",
        )
        lines = self.check(
            "message",
            "\n# removed\n\nkeep\n\nQuillfeather Lane 9  \n\n",
            "--cleanup",
            "strip",
            "--comment-char",
            "#",
        )
        for result in (strip_known, scissors, cut):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_terms_hidden(result)
        for result in (strip_unknown, whitespace, verbatim):
            self.assert_found(result, "commit message line 1")
        for result in (unknown_scissors, unknown_cut):
            self.assert_found(result, "commit message line 3")
        self.assert_found(git_whitespace, "commit message line 2")
        self.assert_found(lines, "commit message line 3")

    def test_scanner_refuses_each_unreadable_list_cause_without_disclosing_terms(self) -> None:
        python_bin = self.root / "no-sops-bin"
        python_bin.mkdir()
        (python_bin / "python3").symlink_to(sys.executable)
        missing_sops = {**self.env, "PATH": str(python_bin)}
        unrelated = {**self.env, "SOPS_AGE_KEY_FILE": str(unrelated_key_file())}
        missing = {**self.env, "DOTFILES_LEAK_TERMS_FILE": str(self.root / "missing.sops.yaml")}
        invalid_file = self.root / "not-sops.yaml"
        invalid_file.write_text("terms: not encrypted\n", encoding="utf-8")
        invalid = {**self.env, "DOTFILES_LEAK_TERMS_FILE": str(invalid_file)}
        empty = {**self.env, "DOTFILES_LEAK_TERMS_FILE": str(encrypt_yaml("terms: |\n  # only comments\n\n"))}
        cases = (
            (missing_sops, "install sops with mise"),
            (unrelated, "add this machine's age key to .sops.yaml above the YubiKey line and run sops updatekeys leak-terms.sops.yaml"),
            (missing, "run scripts/dotfiles-sync local"),
            (invalid, "fix it with sops edit leak-terms.sops.yaml"),
            (empty, "add a term with sops edit leak-terms.sops.yaml"),
        )
        for environment, correction in cases:
            result = self.check("message", "clean\n", env=environment)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(result.stdout, "")
            output_lines = result.stderr.strip().splitlines()
            self.assertEqual(len(output_lines), 1, result.stderr)
            self.assertIn(correction, output_lines[0])
            self.assert_terms_hidden(result)

    def test_scanner_uses_passwd_key_fallback_when_the_callers_home_moved(self) -> None:
        loader = importlib.machinery.SourceFileLoader("leak_terms_check_test", str(CHECK))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        loader.exec_module(module)

        passwd_home = self.root / "passwd-home"
        key_dir = passwd_home / ".config" / "sops" / "age"
        key_dir.mkdir(parents=True)
        shutil.copyfile(self.env["SOPS_AGE_KEY_FILE"], key_dir / "keys.txt")
        moved_home = self.root / "moved-home"
        moved_home.mkdir()
        environment = {**self.env, "HOME": str(moved_home), "XDG_CONFIG_HOME": str(moved_home / "config")}
        for name in ("SOPS_AGE_KEY", "SOPS_AGE_KEY_FILE", "SOPS_AGE_KEY_CMD", "DOTFILES_LEAK_TERMS_SOPS_HOME"):
            environment.pop(name, None)
        with mock.patch.object(module, "passwd_home", return_value=passwd_home):
            matcher = module.load_terms(environment)
        self.assertTrue(matcher.spans("Quillfeather Lane 9"))


if __name__ == "__main__":
    unittest.main()
