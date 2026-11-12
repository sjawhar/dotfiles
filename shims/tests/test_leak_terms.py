#!/usr/bin/env python3
"""The leak-term scanner against an encrypted, per-process test list."""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shlex
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

    def test_scanner_selects_gits_auto_comment_character(self) -> None:
        selected = self.check(
            "git-comment-char",
            "# keep\n; status\n; details\nOmp-Session: 01a0fa64-b3a5-7415-b143-c2a338b5da8b\n",
            "--editor-ran",
            "1",
            "--status-shown",
            "1",
        )
        scissors = self.check(
            "git-comment-char",
            "@ ------------------------ >8 ------------------------\n",
            "--editor-ran",
            "0",
            "--status-shown",
            "0",
        )
        self.assertEqual((selected.returncode, selected.stdout, selected.stderr), (0, ";\n", ""))
        self.assertEqual((scissors.returncode, scissors.stdout, scissors.stderr), (0, "@\n", ""))

    def test_scanner_jj_editor_cleans_each_description_section(self) -> None:
        edited = self.root / "editor.jjdescription"
        edited.write_text(
            "JJ: describe first123456 -------\n"
            "JJ: ignore-rest\n"
            "Quillfeather Lane 9\n"
            "JJ: describe second12345 -------\n"
            "Quillfeather Lane 9\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(CHECK), "jj-editor", str(edited)],
            env={**self.env, "DOTFILES_JJ_EDITOR": json.dumps(["/bin/true"])},
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("leak terms: description of second12345 line 1: listed leak term", result.stderr)
        self.assertIn("sops edit leak-terms.sops.yaml", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_terms_hidden(result)

    def test_jj_header_marker_is_first_in_the_first_512_bytes(self) -> None:
        header = (ROOT / "shims" / "jj").read_bytes()[:512].decode("utf-8")
        self.assertTrue(header.startswith("#!/bin/bash\n# jj-lock-shim:"))

    def test_scanner_jj_editor_reads_the_list_before_running_the_editor(self) -> None:
        edited = self.root / "editor.jjdescription"
        edited.write_text("clean\n", encoding="utf-8")
        ran = self.root / "editor-ran"
        editor = self.root / "editor.sh"
        editor.write_text("#!/bin/sh\n" f": > {shlex.quote(str(ran))}\n", encoding="utf-8")
        editor.chmod(0o755)
        result = subprocess.run(
            [str(CHECK), "jj-editor", str(edited)],
            env={
                **self.env,
                "DOTFILES_JJ_EDITOR": json.dumps([str(editor)]),
                "DOTFILES_LEAK_TERMS_FILE": str(self.root / "missing.sops.yaml"),
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "leak terms: the term list is missing; run scripts/dotfiles-sync local\n")
        self.assertFalse(ran.exists())


class GitLeaks(unittest.TestCase):
    session = "01a0fa64-b3a5-7415-b143-c2a338b5da8b"
    marker_names = (
        "OMP_SESSION_ID",
        "PI_SESSION_FILE",
        "PI_TOOL_BRIDGE_URL",
        "AGENTBOX_SESSION",
        "CLAUDECODE",
        "ENVOY_SESSION_ID",
        "AGENT_SHIM_AGENT",
        "SSH_CONNECTION",
        "SSH_TTY",
        "TMUX",
        "AGENT_SHIM_PERSON",
    )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.xdg = self.root / "xdg"
        self.tmpdir = self.root / "tmp"
        for directory in (self.home, self.xdg, self.tmpdir):
            directory.mkdir()
        self.config = self.root / "gitconfig"
        self.config.write_text(
            "[user]\n\tname = Leak Test\n\temail = leak-test@example.invalid\n"
            "[init]\n\tdefaultBranch = main\n",
            encoding="utf-8",
        )
        base = {
            name: value
            for name, value in os.environ.items()
            if name not in self.marker_names
            and not name.startswith(("PI_", "LEGION_", "GIT_CONFIG_"))
            and name not in {"ENVOY_SESSION_ID", "CLAUDECODE", "AGENTBOX_SESSION", "AGENT_SHIM_AGENT"}
        }
        self.person = {
            **base,
            **fixture_environment(),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "TMPDIR": str(self.tmpdir),
            "PATH": f"{ROOT / 'shims'}{os.pathsep}{os.environ['PATH']}",
            "GIT_CONFIG_GLOBAL": str(self.config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        self.agent = {**self.person, "OMP_SESSION_ID": self.session}
        self.repositories = 0
        self.editors = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def environment(self, agent: bool, **extra: str) -> dict[str, str]:
        return {**(self.agent if agent else self.person), **extra}

    def git_run(self, repository: Path, *arguments: str, agent: bool, real: bool = False,
            extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        command_arguments = list(arguments)
        subcommand = 0
        while subcommand < len(command_arguments):
            option = command_arguments[subcommand]
            if option in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--exec-path"}:
                subcommand += 2
            elif option.startswith("-"):
                subcommand += 1
            else:
                break
        if subcommand < len(command_arguments) and command_arguments[subcommand] == "commit" and "--allow-empty" not in command_arguments:
            command_arguments.insert(subcommand + 1, "--allow-empty")
        command = ["/usr/bin/git" if real else "git", *command_arguments]
        return subprocess.run(
            command,
            cwd=repository,
            env=self.environment(agent, **(extra or {})),
            capture_output=True,
            text=True,
            check=False,
        )

    def hook_run(self, repository: Path, name: str, *arguments: str, agent: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ROOT / "shims" / "git-hooks" / name), *arguments],
            cwd=repository,
            env=self.environment(agent, DOTFILES_GIT_COMMIT_SCAN="cleanup=default verbose=config status=config"),
            capture_output=True,
            text=True,
            check=False,
        )

    def repository(self, agent: bool, remote: bool = False) -> Path:
        self.repositories += 1
        repository = self.root / f"repo-{self.repositories}"
        repository.mkdir()
        initialized = self.git_run(repository, "init", "-q", agent=agent)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        if remote:
            added = self.git_run(repository, "remote", "add", "origin", "https://example.invalid/leak-test.git", agent=agent)
            self.assertEqual(added.returncode, 0, added.stderr)
        return repository

    def stage(self, repository: Path, name: str, text: str, agent: bool) -> None:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        added = self.git_run(repository, "add", name, agent=agent)
        self.assertEqual(added.returncode, 0, added.stderr)

    def head(self, repository: Path, agent: bool) -> tuple[int, str]:
        result = self.git_run(repository, "rev-parse", "HEAD", agent=agent, real=True)
        return result.returncode, result.stdout.strip()

    def assert_hidden(self, result: subprocess.CompletedProcess[str]) -> None:
        output = (result.stdout + result.stderr).casefold()
        for term in TEST_TERMS:
            self.assertNotIn(term.casefold(), output)

    def assert_refused(self, repository: Path, agent: bool, place: str, *arguments: str,
                       extra: dict[str, str] | None = None) -> None:
        before = self.head(repository, agent)
        result = self.git_run(repository, *arguments, agent=agent, extra=extra)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"leak terms: {place}: listed leak term", result.stderr)
        self.assertIn("sops edit leak-terms.sops.yaml", result.stderr)
        self.assertEqual(self.head(repository, agent), before)
        self.assert_hidden(result)

    def assert_clean(self, repository: Path, agent: bool, *arguments: str,
                     extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        result = self.git_run(repository, *arguments, agent=agent, extra=extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_hidden(result)
        return result

    def editor(self, text: str) -> str:
        self.editors += 1
        path = self.root / f"editor-{self.editors}.sh"
        import shlex

        path.write_text(f"#!/bin/sh\nprintf %s {shlex.quote(text)} > \"$1\"\n", encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    def test_git_term_refuses_staged_content_paths_amends_and_controls(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="content"):
                repository = self.repository(agent)
                self.stage(repository, "note.txt", "quillfeather   LANE 9\n", agent)
                self.assert_refused(repository, agent, "note.txt:1", "commit", "-m", "blocked")
                self.stage(repository, "note.txt", "clean\n", agent)
                self.assert_clean(repository, agent, "commit", "-m", "clean")

            with self.subTest(agent=agent, case="path"):
                repository = self.repository(agent)
                self.stage(repository, "notes/zorbleplex.txt", "clean\n", agent)
                self.assert_refused(repository, agent, "notes/<listed term>.txt", "commit", "-m", "blocked")
                self.stage(repository, "notes/clean.txt", "clean\n", agent)
                removed = self.git_run(repository, "rm", "--cached", "-q", "notes/zorbleplex.txt", agent=agent)
                self.assertEqual(removed.returncode, 0, removed.stderr)
                self.assert_clean(repository, agent, "commit", "-m", "clean")

            with self.subTest(agent=agent, case="pre-commit"):
                repository = self.repository(agent)
                hook = repository / ".git" / "hooks" / "pre-commit"
                hook.write_text(
                    "#!/bin/sh\nprintf '%s\\n' 'Quillfeather Lane 9' > injected.txt\ngit add injected.txt\n",
                    encoding="utf-8",
                )
                hook.chmod(0o755)
                self.stage(repository, "clean.txt", "clean\n", agent)
                self.assert_refused(repository, agent, "injected.txt:1", "commit", "-m", "blocked")

            with self.subTest(agent=agent, case="amend"):
                repository = self.repository(agent)
                self.stage(repository, "base.txt", "clean\n", agent)
                self.assert_clean(repository, agent, "commit", "-m", "base")
                self.stage(repository, "amend.txt", "Quillfeather Lane 9\n", agent)
                self.assert_refused(repository, agent, "amend.txt:1", "commit", "--amend", "--no-edit")
                self.stage(repository, "amend.txt", "clean\n", agent)
                self.assert_clean(repository, agent, "commit", "--amend", "--no-edit")

            with self.subTest(agent=agent, case="negative"):
                repository = self.repository(agent)
                self.stage(repository, "staged.txt", "Quillfeather Lane 8\n", agent)
                (repository / "unstaged.txt").write_text("Quillfeather Lane 9\n", encoding="utf-8")
                self.assert_clean(repository, agent, "commit", "-m", "clean")

            with self.subTest(agent=agent, case="partial"):
                repository = self.repository(agent)
                self.stage(repository, "a.txt", "old\n", agent)
                self.assert_clean(repository, agent, "commit", "-m", "base")
                self.stage(repository, "b.txt", "Quillfeather Lane 9\n", agent)
                (repository / "a.txt").write_text("clean\n", encoding="utf-8")
                self.assert_clean(repository, agent, "commit", "a.txt", "-m", "partial")

            with self.subTest(agent=agent, case="all"):
                repository = self.repository(agent)
                self.stage(repository, "tracked.txt", "clean\n", agent)
                self.assert_clean(repository, agent, "commit", "-m", "base")
                (repository / "tracked.txt").write_text("Quillfeather Lane 9\n", encoding="utf-8")
                self.assert_refused(repository, agent, "tracked.txt:1", "commit", "-a", "-m", "blocked")

    def test_git_term_session_trailer_matches_existing_conditions(self) -> None:
        remote = self.repository(True, remote=True)
        self.stage(remote, "clean.txt", "clean\n", True)
        self.assert_clean(remote, True, "commit", "-m", "clean")
        body = self.git_run(remote, "log", "-1", "--format=%B", agent=False, real=True)
        self.assertIn(f"Omp-Session: {self.session}", body.stdout)

        throwaway = self.repository(True)
        self.stage(throwaway, "clean.txt", "clean\n", True)
        self.assert_clean(throwaway, True, "commit", "-m", "clean")
        body = self.git_run(throwaway, "log", "-1", "--format=%B", agent=False, real=True)
        self.assertNotIn("Omp-Session:", body.stdout)

    def test_git_message_refuses_cleanup_modes_and_forms(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="negative"):
                repository = self.repository(agent)
                self.assert_clean(repository, agent, "commit", "-m", "Quillfeather Lane")

            with self.subTest(agent=agent, case="forms"):
                cases = (
                    (("-m", "subject\nQuillfeather Lane 9"), "commit message line 2"),
                    (("--message=Quillfeather Lane 9",), "commit message line 1"),
                    (("-mQuillfeather Lane 9",), "commit message line 1"),
                )
                for arguments, place in cases:
                    repository = self.repository(agent)
                    self.assert_refused(repository, agent, place, "commit", *arguments)

            with self.subTest(agent=agent, case="file"):
                repository = self.repository(agent)
                message = repository / "message"
                message.write_text("subject\nQuillfeather Lane 9\n", encoding="utf-8")
                self.assert_refused(repository, agent, "commit message line 2", "commit", "-F", str(message))

            with self.subTest(agent=agent, case="editor"):
                repository = self.repository(agent)
                self.assert_refused(
                    repository,
                    agent,
                    "commit message line 1",
                    "commit",
                    extra={"GIT_EDITOR": self.editor("Quillfeather Lane 9\n")},
                )

            with self.subTest(agent=agent, case="message-comment"):
                repository = self.repository(agent)
                self.assert_refused(repository, agent, "commit message line 1", "commit", "-m", "# Quillfeather Lane 9")

            with self.subTest(agent=agent, case="configured-cleanup"):
                strip = self.repository(agent)
                set_strip = self.git_run(strip, "config", "commit.cleanup", "strip", agent=agent)
                self.assertEqual(set_strip.returncode, 0, set_strip.stderr)
                self.assert_clean(strip, agent, "commit", extra={"GIT_EDITOR": self.editor("# Quillfeather Lane 9\nclean\n")})
                verbatim = self.repository(agent)
                set_verbatim = self.git_run(verbatim, "config", "commit.cleanup", "verbatim", agent=agent)
                self.assertEqual(set_verbatim.returncode, 0, set_verbatim.stderr)
                self.assert_refused(
                    verbatim,
                    agent,
                    "commit message line 1",
                    "commit",
                    extra={"GIT_EDITOR": self.editor("# Quillfeather Lane 9\n")},
                )

            with self.subTest(agent=agent, case="comment-char"):
                repository = self.repository(agent)
                configured = self.git_run(repository, "config", "core.commentChar", ";", agent=agent)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assert_clean(
                    repository,
                    agent,
                    "commit",
                    extra={"GIT_EDITOR": self.editor("; Quillfeather Lane 9\nclean\n")},
                )
                refused = self.repository(agent)
                configured = self.git_run(refused, "config", "core.commentChar", ";", agent=agent)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assert_refused(
                    refused,
                    agent,
                    "commit message line 1",
                    "commit",
                    extra={"GIT_EDITOR": self.editor("# Quillfeather Lane 9\n")},
                )

            with self.subTest(agent=agent, case="commit-msg"):
                repository = self.repository(agent)
                hook = repository / ".git" / "hooks" / "commit-msg"
                hook.write_text("#!/bin/sh\nprintf '%s\\n' 'Quillfeather Lane 9' >> \"$1\"\n", encoding="utf-8")
                hook.chmod(0o755)
                self.assert_refused(repository, agent, "commit message line 2", "commit", "-m", "clean")

            with self.subTest(agent=agent, case="scissors"):
                above = self.repository(agent)
                self.assert_refused(
                    above,
                    agent,
                    "commit message line 1",
                    "commit",
                    "--cleanup=scissors",
                    extra={"GIT_EDITOR": self.editor("# Quillfeather Lane 9\n# ------------------------ >8 ------------------------\nclean\n")},
                )
                below = self.repository(agent)
                self.assert_clean(
                    below,
                    agent,
                    "commit",
                    "--cleanup=scissors",
                    extra={"GIT_EDITOR": self.editor("clean\n# ------------------------ >8 ------------------------\nQuillfeather Lane 9\n")},
                )

            with self.subTest(agent=agent, case="scissors-without-editor"):
                repository = self.repository(agent)
                self.assert_refused(
                    repository,
                    agent,
                    "commit message line 3",
                    "commit",
                    "--cleanup=scissors",
                    "-m",
                    "clean\n# ------------------------ >8 ------------------------\nQuillfeather Lane 9",
                )

            with self.subTest(agent=agent, case="verbose"):
                repository = self.repository(agent)
                configured = self.git_run(repository, "config", "commit.verbose", "true", agent=agent)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                hook = repository / ".git" / "hooks" / "commit-msg"
                hook.write_text(
                    "#!/bin/sh\nprintf '%s\\n' '# ------------------------ >8 ------------------------' 'Quillfeather Lane 9' >> \"$1\"\n",
                    encoding="utf-8",
                )
                hook.chmod(0o755)
                self.assert_clean(repository, agent, "commit", "-m", "clean")

            with self.subTest(agent=agent, case="stored"):
                repository = self.repository(agent)
                message = "subject\n\nbody"
                self.assert_clean(repository, agent, "commit", "-m", message)
                stored = self.git_run(repository, "cat-file", "commit", "HEAD", agent=False, real=True)
                self.assertEqual(stored.returncode, 0, stored.stderr)
                _, _, body = stored.stdout.partition("\n\n")
                self.assertEqual(body, f"{message}\n")

    def test_git_message_auto_comment_char_handles_status_trailers_and_scissors(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="initial-comment"):
                repository = self.repository(agent)
                configured = self.git_run(repository, "config", "core.commentChar", "auto", agent=agent)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assert_clean(
                    repository,
                    agent,
                    "commit",
                    "-e",
                    "-m",
                    "# keep",
                    extra={"GIT_EDITOR": self.editor("clean\n; Quillfeather Lane 9\n")},
                )
                refused = self.repository(agent)
                configured = self.git_run(refused, "config", "core.commentChar", "auto", agent=agent)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assert_refused(
                    refused,
                    agent,
                    "commit message line 2",
                    "commit",
                    "-e",
                    "-m",
                    "# keep",
                    extra={"GIT_EDITOR": self.editor("clean\n# Quillfeather Lane 9\n")},
                )

            with self.subTest(agent=agent, case="no-initial-comment"):
                repository = self.repository(agent)
                configured = self.git_run(repository, "config", "core.commentChar", "auto", agent=agent)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assert_clean(
                    repository,
                    agent,
                    "commit",
                    extra={"GIT_EDITOR": self.editor("clean\n# Quillfeather Lane 9\n")},
                )

            with self.subTest(agent=agent, case="no-status"):
                repository = self.repository(agent)
                configured = self.git_run(repository, "config", "core.commentChar", "auto", agent=agent)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assert_clean(
                    repository,
                    agent,
                    "commit",
                    "-e",
                    "-m",
                    "# keep",
                    "--no-status",
                    extra={"GIT_EDITOR": self.editor("clean\n; Quillfeather Lane 9\n")},
                )

            with self.subTest(agent=agent, case="caller-trailer-and-signoff"):
                for extra_arguments in (("--trailer", "X-Test: y"), ("-s",)):
                    repository = self.repository(agent)
                    configured = self.git_run(repository, "config", "core.commentChar", "auto", agent=agent)
                    self.assertEqual(configured.returncode, 0, configured.stderr)
                    self.assert_clean(
                        repository,
                        agent,
                        "commit",
                        "-e",
                        "-m",
                        "# keep",
                        *extra_arguments,
                        extra={"GIT_EDITOR": self.editor("clean\n; Quillfeather Lane 9\n")},
                    )

        remote = self.repository(True, remote=True)
        configured = self.git_run(remote, "config", "core.commentChar", "auto", agent=True)
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assert_clean(
            remote,
            True,
            "commit",
            "-e",
            "-m",
            "# keep",
            extra={"GIT_EDITOR": self.editor("clean\n; Quillfeather Lane 9\n")},
        )
        refused = self.repository(True, remote=True)
        configured = self.git_run(refused, "config", "core.commentChar", "auto", agent=True)
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assert_refused(
            refused,
            True,
            "commit message line 2",
            "commit",
            "-e",
            "-m",
            "# keep",
            extra={"GIT_EDITOR": self.editor("clean\n# Quillfeather Lane 9\n")},
        )

    def test_repo_hooks_run_and_the_opt_outs_stay_unscanned(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="repository-hooks"):
                repository = self.repository(agent)
                hooks = repository / ".git" / "hooks"
                for name in ("pre-commit", "commit-msg"):
                    marker = repository / f"{name}.marker"
                    (hooks / name).write_text(
                        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {marker}\ncat >> {marker}\n",
                        encoding="utf-8",
                    )
                    (hooks / name).chmod(0o755)
                self.assert_clean(repository, agent, "commit", "-m", "clean")
                self.assertTrue((repository / "pre-commit.marker").exists())
                self.assertIn("COMMIT_EDITMSG", (repository / "commit-msg.marker").read_text(encoding="utf-8"))

            with self.subTest(agent=agent, case="configured-hooks"):
                repository = self.repository(agent)
                hooks = self.root / f"own-hooks-{agent}"
                hooks.mkdir(exist_ok=True)
                marker = repository / "configured.marker"
                for name in ("pre-commit", "commit-msg"):
                    (hooks / name).write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
                    (hooks / name).chmod(0o755)
                configured = self.git_run(repository, "config", "core.hooksPath", str(hooks), agent=agent)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assert_clean(repository, agent, "commit", "-m", "clean")
                self.assertTrue(marker.exists())

            with self.subTest(agent=agent, case="failing-pre-commit"):
                repository = self.repository(agent)
                hook = repository / ".git" / "hooks" / "pre-commit"
                hook.write_text("#!/bin/sh\nexit 19\n", encoding="utf-8")
                hook.chmod(0o755)
                result = self.git_run(repository, "commit", "-m", "clean", agent=agent)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                direct = self.hook_run(repository, "pre-commit", agent=agent)
                self.assertEqual(direct.returncode, 19, direct.stderr)

            with self.subTest(agent=agent, case="failing-commit-msg"):
                repository = self.repository(agent)
                hook = repository / ".git" / "hooks" / "commit-msg"
                hook.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
                hook.chmod(0o755)
                result = self.git_run(repository, "commit", "-m", "clean", agent=agent)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                message = repository / "message"
                message.write_text("clean\n", encoding="utf-8")
                direct = self.hook_run(repository, "commit-msg", str(message), agent=agent)
                self.assertEqual(direct.returncode, 23, direct.stderr)

            with self.subTest(agent=agent, case="caller-hooks-path"):
                repository = self.repository(agent)
                hooks = self.root / f"caller-hooks-{agent}"
                hooks.mkdir(exist_ok=True)
                self.stage(repository, "leak.txt", "Quillfeather Lane 9\n", agent)
                self.assert_clean(repository, agent, "-c", f"core.hooksPath={hooks}", "commit", "-m", "unchecked")
            with self.subTest(agent=agent, case="no-verify"):
                repository = self.repository(agent)
                self.stage(repository, "leak.txt", "Quillfeather Lane 9\n", agent)
                self.assert_clean(repository, agent, "commit", "--no-verify", "-m", "unchecked")

            with self.subTest(agent=agent, case="real-git"):
                repository = self.repository(agent)
                self.stage(repository, "leak.txt", "Quillfeather Lane 9\n", agent)
                result = self.git_run(repository, "commit", "-m", "unchecked", agent=agent, real=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_unreadable_lists_refuse_git_commits(self) -> None:
        invalid = self.root / "invalid.sops.yaml"
        invalid.write_text("terms: clear text\n", encoding="utf-8")
        empty_sops_home = self.root / "empty-sops-home"
        empty_sops_home.mkdir()
        blank = encrypt_yaml("terms: |\n  # blank\n\n")
        no_sops = os.pathsep.join(
            entry for entry in self.person["PATH"].split(os.pathsep) if not (Path(entry) / "sops").exists()
        )
        cases = (
            ("sops", {"PATH": no_sops}, "install sops with mise"),
            ("key", {"DOTFILES_LEAK_TERMS_SOPS_HOME": str(empty_sops_home)}, "add this machine's age key to .sops.yaml"),
            ("missing", {"DOTFILES_LEAK_TERMS_FILE": str(self.root / "missing.sops.yaml")}, "run scripts/dotfiles-sync local"),
            ("invalid", {"DOTFILES_LEAK_TERMS_FILE": str(invalid)}, "fix it with sops edit leak-terms.sops.yaml"),
            ("blank", {"DOTFILES_LEAK_TERMS_FILE": str(blank)}, "add a term with sops edit leak-terms.sops.yaml"),
        )
        for agent in (False, True):
            for label, extra, correction in cases:
                with self.subTest(agent=agent, case=label):
                    repository = self.repository(agent)
                    self.stage(repository, "clean.txt", "clean\n", agent)
                    before = self.head(repository, agent)
                    environment = self.environment(agent, **extra)
                    if label == "key":
                        for name in ("SOPS_AGE_KEY", "SOPS_AGE_KEY_FILE", "SOPS_AGE_KEY_CMD"):
                            environment.pop(name, None)
                    result = subprocess.run(
                        ["git", "commit", "--allow-empty", "-m", "clean"],
                        cwd=repository,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn(correction, result.stderr)
                    self.assertEqual(self.head(repository, agent), before)
                    self.assert_hidden(result)


class JjLeaks(unittest.TestCase):
    session = "01a0fa64-b3a5-7415-b143-c2a338b5da8b"
    marker_names = (
        "OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION", "CLAUDECODE",
        "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "SSH_CONNECTION", "SSH_TTY", "TMUX", "AGENT_SHIM_PERSON",
    )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.xdg = self.root / "xdg"
        self.tmpdir = self.root / "tmp"
        for directory in (self.home, self.xdg, self.tmpdir):
            directory.mkdir()
        self.config = self.root / "jjconfig.toml"
        self.config.write_text("[user]\nname = \"Leak Test\"\nemail = \"leak-test@example.invalid\"\n", encoding="utf-8")
        base = {
            name: value
            for name, value in os.environ.items()
            if name not in self.marker_names
            and not name.startswith(("PI_", "LEGION_", "GIT_CONFIG_"))
            and name not in {"ENVOY_SESSION_ID", "CLAUDECODE", "AGENTBOX_SESSION", "AGENT_SHIM_AGENT",
                             "JJ_EDITOR", "VISUAL", "EDITOR"}
        }
        self.person = {
            **base,
            **fixture_environment(),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "TMPDIR": str(self.tmpdir),
            "PATH": f"{ROOT / 'shims'}{os.pathsep}{os.environ['PATH']}",
            "JJ_CONFIG": str(self.config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        self.agent = {**self.person, "OMP_SESSION_ID": self.session}
        self.jj_binary = real_jj()
        self.repositories = 0
        self.editors = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def environment(self, agent: bool, **extra: str) -> dict[str, str]:
        return {**(self.agent if agent else self.person), **extra}

    def jj(self, repository: Path, *arguments: str, agent: bool, real: bool = False, input: str | bytes | None = None,
           extra: dict[str, str] | None = None, text: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.jj_binary if real else "jj", *arguments],
            cwd=repository,
            env=self.environment(agent, **(extra or {})),
            input=input,
            capture_output=True,
            text=text,
            check=False,
        )

    def repository(self, agent: bool) -> Path:
        self.repositories += 1
        repository = self.root / f"repo-{self.repositories}"
        repository.mkdir()
        initialized = self.jj(repository, "git", "init", agent=agent)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        return repository

    def write(self, repository: Path, name: str, text: str) -> None:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def state(self, repository: Path, agent: bool) -> tuple[str, str]:
        listed = self.jj(
            repository,
            "--ignore-working-copy",
            "log",
            "-r",
            "all()",
            "--no-graph",
            "-T",
            'change_id ++ "\\n" ++ parents.map(|p| p.change_id()).join(",") ++ "\\n" ++ description ++ "\\n---\\n"',
            agent=agent,
            real=True,
        )
        current = self.jj(repository, "--ignore-working-copy", "log", "-r", "@", "--no-graph", "-T", "change_id", agent=agent, real=True)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(current.returncode, 0, current.stderr)
        return listed.stdout, current.stdout

    def snapshot_state(self, repository: Path, agent: bool) -> tuple[str, str]:
        status = self.jj(repository, "status", agent=agent)
        self.assertEqual(status.returncode, 0, status.stderr)
        return self.state(repository, agent)

    def assert_hidden(self, result: subprocess.CompletedProcess[str]) -> None:
        output = result.stdout + result.stderr
        for term in TEST_TERMS:
            self.assertNotIn(term.casefold(), output.casefold())

    def assert_refused(self, repository: Path, agent: bool, place: str, *arguments: str,
                       input: str | bytes | None = None, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        before = self.snapshot_state(repository, agent)
        result = self.jj(repository, *arguments, agent=agent, input=input, extra=extra)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"leak terms: {place}: listed leak term", result.stderr)
        self.assertIn("sops edit leak-terms.sops.yaml", result.stderr)
        self.assertEqual(self.state(repository, agent), before)
        self.assert_hidden(result)
        return result

    def assert_clean(self, repository: Path, agent: bool, *arguments: str, input: str | None = None,
                     extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        result = self.jj(repository, *arguments, agent=agent, input=input, extra=extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_hidden(result)
        return result

    def description(self, repository: Path, agent: bool, revision: str = "@") -> str:
        result = self.jj(repository, "--ignore-working-copy", "log", "-r", revision, "--no-graph", "-T", "description", agent=agent, real=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def editor(self, text: str, status: int = 0) -> tuple[str, Path]:
        self.editors += 1
        script = self.root / f"editor-{self.editors}.sh"
        arguments = self.root / f"editor-{self.editors}.args"
        script.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > {shlex.quote(str(arguments))}\n"
            "for argument in \"$@\"; do\n"
            "    if [ -f \"$argument\" ]; then file=$argument; fi\n"
            "done\n"
            f"printf '%s' {shlex.quote(text)} > \"$file\"\n"
            f"exit {status}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return str(script), arguments

    def editor_with_environment(self, text: str, status: int = 0) -> tuple[str, Path, Path]:
        self.editors += 1
        script = self.root / f"editor-{self.editors}.sh"
        arguments = self.root / f"editor-{self.editors}.args"
        environment = self.root / f"editor-{self.editors}.environment"
        script.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > {shlex.quote(str(arguments))}\n"
            f"printf '%s' \"$JJ_LEAK_TEST_EDITOR\" > {shlex.quote(str(environment))}\n"
            "for argument in \"$@\"; do\n"
            "    if [ -f \"$argument\" ]; then file=$argument; fi\n"
            "done\n"
            f"printf '%s' {shlex.quote(text)} > \"$file\"\n"
            f"exit {status}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return str(script), arguments, environment

    def set_user_editor(self, value: str) -> None:
        self.config.write_text(
            "[user]\nname = \"Leak Test\"\nemail = \"leak-test@example.invalid\"\n[ui]\neditor = " + value + "\n",
            encoding="utf-8",
        )

    def test_jj_term_refuses_target_diffs_and_controls(self) -> None:
        commands = (
            ("commit", "-m", "ok"),
            ("ci", "-m", "ok"),
            ("describe", "-m", "ok"),
            ("desc", "-m", "ok"),
            ("new",),
            ("new", "root()"),
        )
        tool = self.root / "test-diff-tool"
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
        tool_arguments = (
            "--config",
            f"merge-tools.fixture.program={json.dumps(str(tool))}",
            "commit",
            "--tool",
            "fixture",
            "-m",
            "ok",
        )
        for agent in (False, True):
            for arguments in commands:
                with self.subTest(agent=agent, command=arguments):
                    repository = self.repository(agent)
                    self.write(repository, "note.txt", "Quillfeather Lane 9\n")
                    if arguments[0] in {"describe", "desc"}:
                        self.snapshot_state(repository, agent)
                        commit_id = self.jj(
                            repository,
                            "--ignore-working-copy",
                            "log",
                            "-r",
                            "@",
                            "--no-graph",
                            "-T",
                            "commit_id",
                            agent=agent,
                            real=True,
                        )
                        self.assertEqual(commit_id.returncode, 0, commit_id.stderr)
                        place = f"commit {commit_id.stdout.strip()[:12]} note.txt:1"
                    else:
                        place = "note.txt:1"
                    self.assert_refused(repository, agent, place, *arguments)

                    clean = self.repository(agent)
                    self.write(clean, "note.txt", "clean\n")
                    self.assert_clean(clean, agent, *arguments)

            with self.subTest(agent=agent, case="interactive"):
                repository = self.repository(agent)
                self.write(repository, "note.txt", "Quillfeather Lane 9\n")
                self.assert_refused(repository, agent, "note.txt:1", "commit", "-i", "-m", "ok")
                clean = self.repository(agent)
                self.write(clean, "note.txt", "clean\n")
                self.assert_clean(clean, agent, *tool_arguments)

            with self.subTest(agent=agent, case="path"):
                repository = self.repository(agent)
                self.write(repository, "zorbleplex.txt", "clean\n")
                self.assert_refused(repository, agent, "<listed term>.txt", "commit", "-m", "ok")

            with self.subTest(agent=agent, case="limited-fileset"):
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.write(repository, "dirty.txt", "Quillfeather Lane 9\n")
                self.assert_clean(repository, agent, "commit", "clean.txt", "-m", "ok")

    def test_jj_message_editor_config_precedes_separator(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="refusal"):
                editor, _ = self.editor("Quillfeather Lane 9\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.write(repository, "other.txt", "other\n")
                self.assert_refused(
                    repository,
                    agent,
                    "commit message line 1",
                    "commit",
                    "--",
                    "clean.txt",
                    extra={"JJ_EDITOR": editor},
                )

            with self.subTest(agent=agent, case="clean"):
                editor, _ = self.editor("clean editor message\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.write(repository, "other.txt", "other\n")
                self.assert_clean(repository, agent, "commit", "--", "clean.txt", extra={"JJ_EDITOR": editor})
                committed = self.jj(
                    repository,
                    "--ignore-working-copy",
                    "diff",
                    "--git",
                    "-r",
                    "@-",
                    agent=agent,
                    real=True,
                )
                self.assertEqual(committed.returncode, 0, committed.stderr)
                self.assertIn("b/clean.txt", committed.stdout)
                self.assertNotIn("b/other.txt", committed.stdout)

    def test_jj_term_checks_each_described_revision_and_alias(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="adds-then-removes"):
                repository = self.repository(agent)
                self.write(repository, "note.txt", "Quillfeather Lane 9\n")
                made = self.jj(repository, "describe", "-m", "adds", agent=agent, real=True)
                self.assertEqual(made.returncode, 0, made.stderr)
                made = self.jj(repository, "commit", "-m", "removes", agent=agent, real=True)
                self.assertEqual(made.returncode, 0, made.stderr)
                self.write(repository, "note.txt", "clean\n")
                made = self.jj(repository, "describe", "-m", "removes", agent=agent, real=True)
                self.assertEqual(made.returncode, 0, made.stderr)
                made = self.jj(repository, "commit", "-m", "next", agent=agent, real=True)
                self.assertEqual(made.returncode, 0, made.stderr)
                commit_id = self.jj(
                    repository,
                    "--ignore-working-copy",
                    "log",
                    "-r",
                    "@--",
                    "--no-graph",
                    "-T",
                    "commit_id",
                    agent=agent,
                    real=True,
                )
                self.assertEqual(commit_id.returncode, 0, commit_id.stderr)
                self.assert_refused(
                    repository,
                    agent,
                    f"commit {commit_id.stdout.strip()[:12]} note.txt:1",
                    "describe",
                    "-r",
                    "@-",
                    "-r",
                    "@--",
                    "-m",
                    "ok",
                )

            with self.subTest(agent=agent, case="alias"):
                self.config.write_text(
                    "[user]\nname = \"Leak Test\"\nemail = \"leak-test@example.invalid\"\n[aliases]\nc = [\"commit\"]\n",
                    encoding="utf-8",
                )
                repository = self.repository(agent)
                self.write(repository, "note.txt", "Quillfeather Lane 9\n")
                self.assert_refused(repository, agent, "note.txt:1", "c", "-m", "ok")

    def test_jj_message_refuses_forms_and_keeps_stored_messages(self) -> None:
        commands = (("describe",), ("commit",), ("new",))
        for agent in (False, True):
            for command in commands:
                with self.subTest(agent=agent, command=command):
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    self.assert_refused(repository, agent, "commit message line 2", *command, "-m", "subject\nQuillfeather Lane 9")

                    clean = self.repository(agent)
                    self.write(clean, "clean.txt", "clean\n")
                    self.assert_clean(clean, agent, *command, "--message=first", "-msecond")
                    revision = "@-" if command == ("commit",) else "@"
                    self.assertEqual(self.description(clean, agent, revision), "first\n\nsecond\n")

            with self.subTest(agent=agent, case="stdin-refused"):
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_refused(repository, agent, "commit message line 1", "describe", "--stdin", input="Quillfeather Lane 9")

            with self.subTest(agent=agent, case="stdin-preserves-bytes"):
                for text in ("", "no final newline", "three\n\n\n"):
                    subject = self.repository(agent)
                    control = self.repository(agent)
                    self.write(subject, "clean.txt", "clean\n")
                    self.write(control, "clean.txt", "clean\n")
                    actual = self.assert_clean(subject, agent, "describe", "--stdin", input=text)
                    expected = self.jj(control, "describe", "--stdin", agent=agent, real=True, input=text)
                    self.assertEqual(expected.returncode, 0, expected.stderr)
                    self.assertEqual(self.description(subject, agent), self.description(control, agent), actual.stderr)

            with self.subTest(agent=agent, case="stdin-nul"):
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                before = self.snapshot_state(repository, agent)
                result = self.jj(
                    repository,
                    "describe",
                    "--stdin",
                    agent=agent,
                    input=b"clean\x00message",
                    text=False,
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
                self.assertIn("cannot read a NUL byte", output)
                self.assertEqual(self.state(repository, agent), before)
                for term in TEST_TERMS:
                    self.assertNotIn(term.casefold(), output.casefold())

    def test_jj_message_editor_options_and_unknown_option_refuse(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="jj-editor"):
                editor, _ = self.editor("Quillfeather Lane 9\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_refused(repository, agent, "commit message line 1", "describe", extra={"JJ_EDITOR": editor})

            with self.subTest(agent=agent, case="editor-over-message"):
                editor, _ = self.editor("Quillfeather Lane 9\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_refused(repository, agent, "commit message line 1", "describe", "--editor", "-m", "clean", extra={"JJ_EDITOR": editor})

            with self.subTest(agent=agent, case="user-string"):
                editor, arguments = self.editor("clean from editor\n")
                self.set_user_editor(json.dumps(f"{editor} --wait"))
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe")
                self.assertEqual(arguments.read_text(encoding="utf-8").splitlines()[-1].endswith(".jjdescription"), True)

            with self.subTest(agent=agent, case="user-array-path"):
                editor, arguments = self.editor("clean array editor\n")
                self.set_user_editor(json.dumps([editor, "$path", "--wait"]))
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe")
                values = arguments.read_text(encoding="utf-8").splitlines()
                self.assertTrue(values[0].endswith(".jjdescription"))
                self.assertEqual(values[1], "--wait")

            with self.subTest(agent=agent, case="user-structured"):
                editor, arguments, configured_environment = self.editor_with_environment("clean structured editor\n")
                self.set_user_editor(
                    "{ env = { JJ_LEAK_TEST_EDITOR = \"configured\" }, command = "
                    + json.dumps([editor, "$path"])
                    + " }"
                )
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe")
                self.assertTrue(arguments.read_text(encoding="utf-8").splitlines()[0].endswith(".jjdescription"))
                self.assertEqual(configured_environment.read_text(encoding="utf-8"), "configured")

            with self.subTest(agent=agent, case="visual-and-editor"):
                for variable in ("VISUAL", "EDITOR"):
                    editor, arguments = self.editor("clean environment editor\n")
                    self.config.write_text("[user]\nname = \"Leak Test\"\nemail = \"leak-test@example.invalid\"\n", encoding="utf-8")
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    self.assert_clean(repository, agent, "describe", extra={variable: editor})
                    self.assertTrue(arguments.read_text(encoding="utf-8").splitlines()[-1].endswith(".jjdescription"))

            with self.subTest(agent=agent, case="command-config-editor"):
                editor, arguments = self.editor("clean command config editor\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe", "--config", f"ui.editor={json.dumps(editor + ' --wait')}")
                self.assertTrue(arguments.read_text(encoding="utf-8").splitlines()[-1].endswith(".jjdescription"))

            with self.subTest(agent=agent, case="editor-failure"):
                editor, _ = self.editor("clean\n", status=19)
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                result = self.jj(repository, "describe", agent=agent, extra={"JJ_EDITOR": editor})
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("Failed to edit description", result.stderr)

            with self.subTest(agent=agent, case="jj-lines-clean"):
                editor, _ = self.editor("JJ: Quillfeather Lane 9\nclean\nJJ: ignore-rest\nQuillfeather Lane 9\n")
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                self.assert_clean(repository, agent, "describe", extra={"JJ_EDITOR": editor})

            for command, option in (
                ("describe", "--unknown-option"),
                ("new", "--edit"),
                ("new", "-d"),
            ):
                with self.subTest(agent=agent, case=f"unknown-{command}-{option}"):
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    before = self.snapshot_state(repository, agent)
                    result = self.jj(repository, command, option, "-m", "clean", agent=agent)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn(
                        f"jj shim: cannot read option {option}, so the leak-term check cannot run; nothing changed",
                        result.stderr,
                    )
                    self.assertEqual(self.state(repository, agent), before)
                    self.assert_hidden(result)

            with self.subTest(agent=agent, case="repository-global"):
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                before = self.snapshot_state(repository, agent)
                result = subprocess.run(
                    ["jj", "-R", str(repository), "describe", "-m", "Quillfeather Lane 9"],
                    cwd=self.root,
                    env=self.environment(agent),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("leak terms: commit message line 1: listed leak term", result.stderr)
                self.assertEqual(self.state(repository, agent), before)
                self.assert_hidden(result)


    def test_jj_message_editor_wrapper_loop_refuses_before_a_child_runs(self) -> None:
        for agent in (False, True):
            with self.subTest(agent=agent, case="shim"):
                self.set_user_editor(json.dumps(str(CHECK)))
                repository = self.repository(agent)
                self.write(repository, "clean.txt", "clean\n")
                before = self.snapshot_state(repository, agent)
                result = self.jj(repository, "describe", agent=agent)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("configured jj editor resolves to leak-terms-check", result.stderr)
                self.assertEqual(self.state(repository, agent), before)
                self.assert_hidden(result)

            with self.subTest(agent=agent, case="wrapper"):
                description = self.root / f"description-{agent}"
                description.write_text("clean\n", encoding="utf-8")
                result = subprocess.run(
                    [str(CHECK), "jj-editor", str(description)],
                    env=self.environment(agent, DOTFILES_JJ_EDITOR=json.dumps([str(CHECK), "jj-editor"])),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("recursive jj editor wrapper refused", result.stderr)
                self.assertEqual(description.read_text(encoding="utf-8"), "clean\n")
                self.assert_hidden(result)

            with self.subTest(agent=agent, case="already-active"):
                description = self.root / f"active-description-{agent}"
                description.write_text("clean\n", encoding="utf-8")
                ran = self.root / f"unexpected-editor-child-{agent}"
                editor = self.root / f"unexpected-editor-{agent}.sh"
                editor.write_text(
                    "#!/bin/sh\n"
                    f": > {shlex.quote(str(ran))}\n",
                    encoding="utf-8",
                )
                editor.chmod(0o755)
                result = subprocess.run(
                    [str(CHECK), "jj-editor", str(description)],
                    env=self.environment(
                        agent,
                        DOTFILES_JJ_EDITOR=json.dumps([str(editor)]),
                        DOTFILES_JJ_EDITOR_WRAPPER_ACTIVE="1",
                    ),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("recursive jj editor wrapper refused", result.stderr)
                self.assertFalse(ran.exists())
                self.assert_hidden(result)

    def test_jj_without_a_real_binary_refuses_without_recursing(self) -> None:
        result = subprocess.run(
            [str(ROOT / "shims" / "jj"), "status"],
            cwd=self.root,
            env=self.environment(
                False,
                PATH=str(ROOT / "shims"),
                DOTFILES_DIR=str(self.root / "missing-dotfiles"),
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("jj shim: no real jj found on PATH", result.stderr)
        self.assertNotIn("recurs", result.stderr.casefold())

    def test_unreadable_lists_refuse_jj_commits(self) -> None:
        invalid = self.root / "invalid.sops.yaml"
        invalid.write_text("terms: clear text\n", encoding="utf-8")
        empty_sops_home = self.root / "empty-sops-home"
        empty_sops_home.mkdir()
        blank = encrypt_yaml("terms: |\n  # blank\n\n")
        no_sops = os.pathsep.join(
            entry for entry in self.person["PATH"].split(os.pathsep) if not (Path(entry) / "sops").exists()
        )
        cases = (
            ("sops", {"PATH": no_sops}, "install sops with mise"),
            ("key", {"DOTFILES_LEAK_TERMS_SOPS_HOME": str(empty_sops_home)}, "add this machine's age key to .sops.yaml"),
            ("missing", {"DOTFILES_LEAK_TERMS_FILE": str(self.root / "missing.sops.yaml")}, "run scripts/dotfiles-sync local"),
            ("invalid", {"DOTFILES_LEAK_TERMS_FILE": str(invalid)}, "fix it with sops edit leak-terms.sops.yaml"),
            ("blank", {"DOTFILES_LEAK_TERMS_FILE": str(blank)}, "add a term with sops edit leak-terms.sops.yaml"),
        )
        for agent in (False, True):
            for label, extra, correction in cases:
                with self.subTest(agent=agent, case=label):
                    repository = self.repository(agent)
                    self.write(repository, "clean.txt", "clean\n")
                    environment = self.environment(agent, **extra)
                    if label == "key":
                        for name in ("SOPS_AGE_KEY", "SOPS_AGE_KEY_FILE", "SOPS_AGE_KEY_CMD"):
                            environment.pop(name, None)
                    result = subprocess.run(
                        ["jj", "commit", "clean.txt", "-m", "clean"],
                        cwd=repository,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn(correction, result.stderr)
                    self.assert_hidden(result)


class OutgoingLeaks(unittest.TestCase):
    """Outgoing Git and JJ commits are scanned independently of the public-leak gate."""

    agent_markers = (
        "OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION", "CLAUDECODE",
        "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "LEGION_ROLE", "LEGION_TREE", "LEGION_CONTROLLER",
        "LEGION_DAEMON_URL", "SSH_CONNECTION", "SSH_TTY", "TMUX", "AGENT_SHIM_PERSON",
    )
    public_url = "https://github.com/theorem-labs/rocq.git"
    unknown_url = "https://github.com/theorem-labs/unreadable-fixture.git"
    private_url = "https://github.com/theorem-labs/private-fixture.git"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.xdg = self.root / "xdg"
        self.tmpdir = self.root / "tmp"
        self.fake = self.root / "fake"
        self.sinks = self.root / "sinks"
        for directory in (self.home, self.xdg, self.tmpdir, self.fake, self.sinks):
            directory.mkdir()
        cache = self.home / ".cache" / "public-leak-check"
        cache.mkdir(parents=True)
        (cache / "theorem-labs_rocq").write_text("public", encoding="utf-8")
        (cache / "theorem-labs_private-fixture").write_text("private", encoding="utf-8")
        (cache / "theorem-labs_other-public").write_text("public", encoding="utf-8")
        self.public_log = self.root / "public-check.log"
        self.write_executable("gh", "#!/bin/bash\nexit 1\n")
        self.write_executable(
            "git-remote-sink",
            "#!/bin/bash\n"
            "key=$(printf '%s' \"$2\" | sha1sum | cut -c1-16)\n"
            f"sink={shlex.quote(str(self.sinks))}/$key.git\n"
            "[[ -d \"$sink\" ]] || /usr/bin/git init -q --bare \"$sink\"\n"
            f"printf '%s\\t%s\\n' \"$2\" \"$sink\" >> {shlex.quote(str(self.sinks) + '.log')}\n"
            "while IFS= read -r line; do\n"
            "    case \"$line\" in\n"
            "        capabilities) printf 'connect\\n\\n' ;;\n"
            "        'connect git-receive-pack'|'connect git-upload-pack')\n"
            "            printf '\\n'\n"
            "            exec env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_COMMON_DIR "
            "-u GIT_CONFIG_PARAMETERS -u GIT_CONFIG_COUNT /usr/bin/git \"${line#connect git-}\" \"$sink\"\n"
            "            ;;\n"
            "        '') exit 0 ;;\n"
            "        *) exit 1 ;;\n"
            "    esac\n"
            "done\n",
        )
        self.check = self.root / "public-leak-check"
        self.check.write_text(f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(self.public_log))}\nexit 0\n", encoding="utf-8")
        self.check.chmod(0o755)
        self.jj_config = self.root / "jj.toml"
        self.jj_config.write_text(
            '[user]\nname = "Push Test"\nemail = "push-test@example.invalid"\n[signing]\nbehavior = "drop"\n',
            encoding="utf-8",
        )
        base = {
            key: value for key, value in os.environ.items()
            if key not in self.agent_markers and not key.startswith(("GIT_CONFIG_", "LEGION_", "PI_"))
        }
        self.person = {
            **base,
            **fixture_environment(),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "TMPDIR": str(self.tmpdir),
            "PATH": os.pathsep.join([str(self.fake), str(ROOT / "shims"), str(Path(real_jj()).parent), os.environ["PATH"]]),
            "PUBLIC_LEAK_CHECK": str(self.check),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ALLOW_PROTOCOL": "sink",
            "JJ_CONFIG": str(self.jj_config),
        }
        self.agent = {**self.person, "OMP_SESSION_ID": "01a0fa64-b3a5-7415-b143-c2a338b5da8b"}
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.real("init", "-q", "-b", "main")
        self.real("config", "user.name", "Push Test")
        self.real("config", "user.email", "push-test@example.invalid")
        self.real("commit", "-q", "--allow-empty", "-m", "base")
        self.real("remote", "add", "origin", self.public_url)
        self.real("config", "remote.origin.vcs", "sink")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_executable(self, name: str, content: str) -> Path:
        path = self.fake / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def command(self, command: list[str], environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=self.repo, env=environment or self.agent, capture_output=True, text=True, check=False, timeout=60)

    def real(self, *arguments: str, environment: dict[str, str] | None = None) -> str:
        result = self.command(["/usr/bin/git", *arguments], environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def git_push(self, remote: str, commit: str, ref: str, environment: dict[str, str], *options: str) -> subprocess.CompletedProcess[str]:
        return self.command(["git", "push", *options, remote, f"{commit}:refs/heads/{ref}"], environment)

    def commit(self, path: str, content: str, message: str) -> str:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content + "\n", encoding="utf-8")
        self.real("add", path)
        self.real("commit", "--no-verify", "-m", message)
        return self.real("rev-parse", "HEAD")

    def sink(self, url: str) -> Path:
        return self.sinks / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.git"

    def received(self, commit: str, url: str) -> bool:
        sink = self.sink(url)
        return sink.is_dir() and subprocess.run(
            ["/usr/bin/git", "--git-dir", str(sink), "cat-file", "-e", commit],
            capture_output=True,
        ).returncode == 0

    def set_url(self, url: str) -> None:
        self.real("remote", "set-url", "origin", url)

    def assert_term_refusal(
        self,
        result: subprocess.CompletedProcess[str],
        commit: str,
        place: str,
        url: str,
        shim: str = "git",
    ) -> None:
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"error: leak terms: commit {commit[:12]} {place}: listed leak term", result.stderr)
        self.assertIn(f"error: {shim} shim: this push to {url} holds a listed leak term. Nothing was pushed.", result.stderr)
        self.assertFalse(self.received(commit, url), result.stderr)
        for term in TEST_TERMS:
            self.assertNotIn(term.casefold(), (result.stdout + result.stderr).casefold())

    def init_jj(self) -> None:
        result = self.command(["jj", "git", "init", "--colocate"])
        self.assertEqual(result.returncode, 0, result.stderr)

    def jj_push(self, commit: str, bookmark: str, environment: dict[str, str], executable: bool = False) -> subprocess.CompletedProcess[str]:
        imported = self.command(["jj", "git", "import"], environment)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        marked = self.command(["jj", "bookmark", "set", "--allow-backwards", bookmark, "-r", commit], environment)
        self.assertEqual(marked.returncode, 0, marked.stderr)
        config = ["--config", "git.executable-path=/usr/bin/git"] if executable else []
        return self.command(["jj", *config, "git", "push", "--bookmark", bookmark], environment)

    def test_push_refuses_git_content_message_options_and_hooks(self) -> None:
        cases = (
            ("content", "guard.txt", "Quillfeather Lane 9", "clean", (), "guard.txt:1"),
            ("message", "message.txt", "clean", "Quillfeather Lane 9", (), "message line 1"),
            ("no-verify", "no-verify.txt", "Quillfeather Lane 9", "clean", ("--no-verify",), "no-verify.txt:1"),
            ("caller-hooks", "caller-hooks.txt", "Quillfeather Lane 9", "clean", ("-c", "core.hooksPath=/dev/null"), "caller-hooks.txt:1"),
        )
        for agent, url in ((True, self.public_url), (False, self.public_url), (True, self.unknown_url), (False, self.unknown_url)):
            for label, path, content, message, options, place in cases:
                with self.subTest(agent=agent, url=url, case=label):
                    self.set_url(url)
                    actual_path = f"{label}-{int(agent)}-{url.rsplit('/', 1)[-1]}"
                    commit = self.commit(actual_path, content, message)
                    environment = self.agent if agent else self.person
                    actual_place = f"{actual_path}:1" if place.endswith(":1") else place
                    if options[:1] == ("-c",):
                        result = self.command(["git", *options, "push", "origin", f"{commit}:refs/heads/{label}-{agent}"], environment)
                    else:
                        result = self.git_push("origin", commit, f"{label}-{agent}", environment, *options)
                    self.assert_term_refusal(result, commit, actual_place, url)

    def test_push_refuses_wrapper_dispatcher_paths_and_changed_paths(self) -> None:
        for agent, url in ((True, self.public_url), (False, self.public_url), (True, self.unknown_url), (False, self.unknown_url)):
            for marker, file in (("PUBLIC-LEAK-CHECK-WRAPPER", "pre-push"), ("PUBLIC-LEAK-CHECK-DISPATCHER", "dispatch")):
                with self.subTest(agent=agent, url=url, marker=marker):
                    self.set_url(url)
                    hooks = self.root / f"{marker}-{agent}-{url.rsplit('/', 1)[-1]}"
                    hooks.mkdir()
                    executable = hooks / file
                    executable.write_text(f"#!/bin/bash\n# {marker}\nexit 0\n", encoding="utf-8")
                    executable.chmod(0o755)
                    self.real("config", "core.hooksPath", str(hooks))
                    path = f"{marker}-{agent}-{url.rsplit('/', 1)[-1]}/zorbleplex.txt"
                    commit = self.commit(path, "clean", "clean")
                    result = self.git_push("origin", commit, f"{marker}-{agent}", self.agent if agent else self.person)
                    self.assert_term_refusal(result, commit, path.replace("zorbleplex", "<listed term>"), url)
                    self.real("config", "--unset", "core.hooksPath")

    def test_push_refuses_jj_and_a_middle_stack_commit(self) -> None:
        self.init_jj()
        first = self.commit("first.txt", "clean", "first")
        middle = self.commit("middle.txt", "clean", "Quillfeather Lane 9")
        last = self.commit("last.txt", "clean", "last")
        for url in (self.public_url, self.unknown_url):
            self.set_url(url)
            for environment, executable in (
                (self.agent, False),
                (self.person | {"DOTFILES_JJ_NO_LEAK_CHECK": "1"}, False),
                (self.agent, True),
                (self.person | {"DOTFILES_JJ_NO_LEAK_CHECK": "1"}, True),
            ):
                with self.subTest(agent=environment is self.agent, executable=executable, url=url):
                    result = self.jj_push(last, f"stack-{executable}-{id(environment)}-{url.rsplit('/', 1)[-1]}", environment, executable)
                    self.assert_term_refusal(result, middle, "message line 1", url, "jj")
                    self.assertFalse(self.received(first, url))
                    self.assertFalse(self.received(last, url))
                    self.assertNotIn("DOTFILES_JJ_NO_LEAK_CHECK", result.stderr)

    def test_push_clean_stacks_and_merges_reach_the_sink(self) -> None:
        first = self.commit("stack-first.txt", "clean", "first")
        second = self.commit("stack-second.txt", "clean", "second")
        third = self.commit("stack-third.txt", "clean", "third")
        stack = self.git_push("origin", third, "clean-stack", self.person)
        self.assertEqual(stack.returncode, 0, stack.stderr)
        for commit in (first, second, third):
            self.assertTrue(self.received(commit, self.public_url))

        self.real("checkout", "-b", "clean-side")
        self.commit("clean-merge.txt", "side", "side")
        self.real("checkout", "main")
        self.commit("clean-merge.txt", "main", "main")
        merge = self.command(["/usr/bin/git", "merge", "--no-ff", "clean-side"], self.person)
        self.assertNotEqual(merge.returncode, 0)
        (self.repo / "clean-merge.txt").write_text("resolution\n", encoding="utf-8")
        self.real("add", "clean-merge.txt")
        self.real("commit", "--no-verify", "-m", "clean merge")
        merged = self.real("rev-parse", "HEAD")
        merge_push = self.git_push("origin", merged, "clean-merge", self.person)
        self.assertEqual(merge_push.returncode, 0, merge_push.stderr)
        self.assertTrue(self.received(merged, self.public_url))

        self.init_jj()
        before = self.public_log.read_text(encoding="utf-8").splitlines() if self.public_log.exists() else []
        jj_push = self.jj_push(merged, "clean-jj", self.person)
        self.assertEqual(jj_push.returncode, 0, jj_push.stderr)
        self.assertTrue(self.received(merged, self.public_url))
        after = self.public_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(after), len(before) + 1)

    def test_push_root_diff_ignores_log_configuration(self) -> None:
        (self.home / ".gitconfig").write_text(
            "[log]\n"
            "\tshowRoot = false\n"
            "\tshowSignature = true\n",
            encoding="utf-8",
        )
        initial_repository = self.repo
        try:
            for label, content in (("root-leak", "Quillfeather Lane 9"), ("root-clean", "clean")):
                with self.subTest(kind=label):
                    self.repo = self.root / label
                    self.repo.mkdir()
                    self.real("init", "-q", "-b", "main")
                    self.real("config", "user.name", "Push Test")
                    self.real("config", "user.email", "push-test@example.invalid")
                    self.real("remote", "add", "origin", self.public_url)
                    self.real("config", "remote.origin.vcs", "sink")
                    commit = self.commit("root.txt", content, label)
                    result = self.git_push("origin", commit, label, self.person)
                    if label == "root-leak":
                        self.assert_term_refusal(result, commit, "root.txt:1", self.public_url)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertTrue(self.received(commit, self.public_url))
        finally:
            self.repo = initial_repository

    def test_push_skips_an_empty_private_dry_run_destination(self) -> None:
        self.init_jj()
        self.set_url(self.private_url)
        self.write_executable(
            "jj",
            "#!/bin/bash\n"
            "if [[ \" $* \" == *' --dry-run '* ]]; then\n"
            "    printf 'Changes to push to origin:\\n'\n"
            "fi\n",
        )
        result = self.command(
            [str(ROOT / "shims" / "jj"), "git", "push"],
            self.person | {"DOTFILES_JJ_NO_LEAK_CHECK": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_push_refuses_a_combined_diff(self) -> None:
        self.real("checkout", "-b", "side")
        self.commit("merge.txt", "side", "side")
        self.real("checkout", "main")
        self.commit("merge.txt", "main", "main")
        merge = self.command(["/usr/bin/git", "merge", "--no-ff", "side"], self.agent)
        self.assertNotEqual(merge.returncode, 0)
        (self.repo / "merge.txt").write_text("Quillfeather Lane 9\n", encoding="utf-8")
        self.real("add", "merge.txt")
        self.real("commit", "--no-verify", "-m", "resolve")
        commit = self.real("rev-parse", "HEAD")
        for agent, url in ((True, self.public_url), (False, self.public_url), (True, self.unknown_url), (False, self.unknown_url)):
            with self.subTest(kind="git", agent=agent, url=url):
                self.set_url(url)
                self.assert_term_refusal(
                    self.git_push("origin", commit, f"merge-{agent}", self.agent if agent else self.person),
                    commit,
                    "merge.txt:1",
                    url,
                )
        self.init_jj()
        for agent, url in ((True, self.public_url), (False, self.public_url), (True, self.unknown_url), (False, self.unknown_url)):
            for executable in (False, True):
                with self.subTest(kind="jj", agent=agent, executable=executable, url=url):
                    self.set_url(url)
                    result = self.jj_push(
                        commit,
                        f"merge-jj-{agent}-{executable}-{url.rsplit('/', 1)[-1]}",
                        self.agent if agent else self.person,
                        executable,
                    )
                    self.assert_term_refusal(result, commit, "merge.txt:1", url, "jj")

    def test_push_refuses_person_terms_without_the_public_check(self) -> None:
        no_check = dict(self.person)
        no_check.pop("PUBLIC_LEAK_CHECK")
        commit = self.commit("without-public-check.txt", "Quillfeather Lane 9", "clean")
        for url in (self.public_url, self.unknown_url):
            with self.subTest(kind="git", url=url):
                self.set_url(url)
                result = self.git_push("origin", commit, f"no-public-{url.rsplit('/', 1)[-1]}", no_check)
                self.assert_term_refusal(result, commit, "without-public-check.txt:1", url)
        self.init_jj()
        for url in (self.public_url, self.unknown_url):
            with self.subTest(kind="jj", url=url):
                self.set_url(url)
                result = self.jj_push(commit, f"no-public-jj-{url.rsplit('/', 1)[-1]}", no_check)
                self.assert_term_refusal(result, commit, "without-public-check.txt:1", url, "jj")

    def test_push_controls_private_clean_force_deletion_and_direct_url(self) -> None:
        clean = self.commit("clean.txt", "clean", "clean")
        self.set_url(self.private_url)
        private = self.git_push("origin", clean, "private", self.person)
        self.assertEqual(private.returncode, 0, private.stderr)
        self.assertTrue(self.received(clean, self.private_url))
        self.set_url(self.public_url)
        leaky = self.commit("force.txt", "Quillfeather Lane 9", "force")
        self.assert_term_refusal(self.git_push("origin", leaky, "force", self.agent, "--force"), leaky, "force.txt:1", self.public_url)
        self.real("push", "--no-verify", "origin", f"{clean}:refs/heads/delete")
        deleted = self.command(["git", "push", "origin", ":refs/heads/delete"], self.person)
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.set_url(self.private_url)
        direct = self.command(["git", "push", f"sink::{self.public_url}", f"{leaky}:refs/heads/direct"], self.person)
        self.assert_term_refusal(direct, leaky, "force.txt:1", f"sink::{self.public_url}")

    def test_push_uses_the_destination_advertisement_not_other_refs(self) -> None:
        leaky = self.commit("advertisement.txt", "Quillfeather Lane 9", "leak")
        other_url = "https://github.com/theorem-labs/other-public.git"
        self.real("remote", "add", "other", other_url)
        self.real("config", "remote.other.vcs", "sink")
        self.real("push", "--no-verify", "other", f"{leaky}:refs/heads/held")
        self.real("update-ref", "refs/remotes/origin/stale", leaky)
        self.assert_term_refusal(self.git_push("origin", leaky, "destination", self.agent), leaky, "advertisement.txt:1", self.public_url)
        self.real("push", "--no-verify", "origin", f"{leaky}:refs/heads/already")
        up_to_date = self.git_push("origin", leaky, "again", self.person)
        self.assertEqual(up_to_date.returncode, 0, up_to_date.stderr)
        direct_url = f"sink::{self.unknown_url}"
        self.real("push", "--no-verify", direct_url, f"{leaky}:refs/heads/direct-held")
        direct_held = self.command(["git", "push", direct_url, f"{leaky}:refs/heads/direct-again"], self.person)
        self.assertEqual(direct_held.returncode, 0, direct_held.stderr)

    def test_push_refuses_a_failed_advertisement_read(self) -> None:
        self.write_executable(
            "git-remote-fail",
            "#!/bin/bash\n"
            "key=$(printf '%s' \"$2\" | sha1sum | cut -c1-16)\n"
            f"sink={shlex.quote(str(self.sinks))}/$key.git\n"
            "[[ -d \"$sink\" ]] || /usr/bin/git init -q --bare \"$sink\"\n"
            "while IFS= read -r line; do\n"
            "    case \"$line\" in\n"
            "        capabilities) printf 'connect\\n\\n' ;;\n"
            "        'connect git-receive-pack')\n"
            "            printf '\\n'\n"
            "            exec env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_COMMON_DIR "
            "-u GIT_CONFIG_PARAMETERS -u GIT_CONFIG_COUNT /usr/bin/git receive-pack \"$sink\"\n"
            "            ;;\n"
            "        'connect git-upload-pack') exit 1 ;;\n"
            "        '') exit 0 ;;\n"
            "        *) exit 1 ;;\n"
            "    esac\n"
            "done\n",
        )
        self.real("config", "remote.origin.vcs", "fail")
        leaky = self.commit("failed.txt", "Quillfeather Lane 9", "failed")
        result = self.git_push("origin", leaky, "failed", self.person | {"GIT_ALLOW_PROTOCOL": "sink:fail"})
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("cannot read text to check: git could not read outgoing commits", result.stderr)
        self.assertFalse(self.received(leaky, self.public_url))

    def test_unreadable_push_lists_refuse_git_and_jj(self) -> None:
        self.init_jj()
        invalid = self.root / "invalid.sops.yaml"
        invalid.write_text("terms: clear text\n", encoding="utf-8")
        empty_sops_home = self.root / "empty-sops-home"
        empty_sops_home.mkdir()
        blank = encrypt_yaml("terms: |\n  # blank\n\n")
        no_sops = os.pathsep.join(
            entry for entry in self.person["PATH"].split(os.pathsep) if not (Path(entry) / "sops").exists()
        )
        cases = (
            ("sops", {"PATH": no_sops}, "install sops with mise"),
            ("key", {"DOTFILES_LEAK_TERMS_SOPS_HOME": str(empty_sops_home)}, "add this machine's age key to .sops.yaml"),
            ("missing", {"DOTFILES_LEAK_TERMS_FILE": str(self.root / "missing.sops.yaml")}, "run scripts/dotfiles-sync local"),
            ("invalid", {"DOTFILES_LEAK_TERMS_FILE": str(invalid)}, "fix it with sops edit leak-terms.sops.yaml"),
            ("blank", {"DOTFILES_LEAK_TERMS_FILE": str(blank)}, "add a term with sops edit leak-terms.sops.yaml"),
        )
        for agent in (False, True):
            for label, extra, correction in cases:
                with self.subTest(agent=agent, case=label):
                    commit = self.commit(f"{label}-{agent}.txt", "clean", label)
                    environment = (self.agent if agent else self.person) | extra
                    if label == "key":
                        for name in ("SOPS_AGE_KEY", "SOPS_AGE_KEY_FILE", "SOPS_AGE_KEY_CMD"):
                            environment.pop(name, None)
                    git_result = self.git_push("origin", commit, f"git-{label}-{agent}", environment)
                    self.assertNotEqual(git_result.returncode, 0, git_result.stderr)
                    self.assertIn(correction, git_result.stderr)
                    self.assertFalse(self.received(commit, self.public_url))
                    jj_result = self.jj_push(commit, f"jj-{label}-{agent}", environment)
                    self.assertNotEqual(jj_result.returncode, 0, jj_result.stderr)
                    self.assertIn(correction, jj_result.stderr)
                    self.assertFalse(self.received(commit, self.public_url))
if __name__ == "__main__":
    unittest.main()
