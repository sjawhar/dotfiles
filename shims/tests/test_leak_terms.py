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


if __name__ == "__main__":
    unittest.main()
