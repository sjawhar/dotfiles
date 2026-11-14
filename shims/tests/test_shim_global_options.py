#!/usr/bin/env python3
"""Compare the git shim's global-option reader with Git 2.43."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REAL_GIT = "/usr/bin/git"
SHIMS = Path(__file__).resolve().parents[1]

# name, accepted argv before the command, result kind, command arguments, working directory
GLOBAL_OPTIONS = (
    ("-p", ("-p",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--paginate", ("--paginate",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("-P", ("-P",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--no-pager", ("--no-pager",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--no-replace-objects", ("--no-replace-objects",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--bare", ("--bare",), "bare", ("rev-parse", "--is-bare-repository"), "gitdir"),
    ("--literal-pathspecs", ("--literal-pathspecs",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--no-literal-pathspecs", ("--no-literal-pathspecs",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--glob-pathspecs", ("--glob-pathspecs",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--noglob-pathspecs", ("--noglob-pathspecs",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--icase-pathspecs", ("--icase-pathspecs",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--no-optional-locks", ("--no-optional-locks",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("-C separate", ("-C", "repo"), "directory", ("rev-parse", "--show-toplevel"), "root"),
    ("-c separate", ("-c", "test.probe=from-short"), "config", ("config", "--get", "test.probe"), "repo"),
    ("--shallow-file separate", ("--shallow-file", "shallow"), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--git-dir separate", ("--git-dir", ".git", "--work-tree", "."), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--git-dir equals", ("--git-dir={git_dir}", "--work-tree", "{repo}"), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--work-tree separate", ("--work-tree", "."), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--work-tree equals", ("--work-tree={repo}",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--namespace separate", ("--namespace", "test-space"), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--namespace equals", ("--namespace=test-space",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--config-env separate", ("--config-env", "test.probe=DOTFILES_GIT_PROBE"), "config", ("config", "--get", "test.probe"), "repo"),
    ("--config-env equals", ("--config-env=test.probe=DOTFILES_GIT_PROBE",), "config", ("config", "--get", "test.probe"), "repo"),
    ("--attr-source separate", ("--attr-source", "HEAD"), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--attr-source equals", ("--attr-source=HEAD",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--exec-path equals", ("--exec-path={exec_path}",), "top", ("rev-parse", "--show-toplevel"), "repo"),
    ("--exec-path bare", ("--exec-path", "ignored"), "query", ("rev-parse", "--show-toplevel"), "repo"),
    ("--html-path", ("--html-path",), "query", ("rev-parse", "--show-toplevel"), "repo"),
    ("--man-path", ("--man-path",), "query", ("rev-parse", "--show-toplevel"), "repo"),
    ("--info-path", ("--info-path",), "query", ("rev-parse", "--show-toplevel"), "repo"),
    ("--list-cmds", ("--list-cmds=main",), "query", ("rev-parse", "--show-toplevel"), "repo"),
    ("-h", ("-h",), "stop", ("rev-parse", "--show-toplevel"), "repo"),
    ("--help", ("--help",), "stop", ("rev-parse", "--show-toplevel"), "repo"),
    ("-v", ("-v",), "stop", ("rev-parse", "--show-toplevel"), "repo"),
    ("--version", ("--version",), "stop", ("rev-parse", "--show-toplevel"), "repo"),
)

# Rejected forms are as important as accepted forms: a future permissive reader must not move the
# command past an option Git itself rejects.
REJECTED_OPTIONS = (
    ("-C attached", ("-C.", "rev-parse", "--show-toplevel"), "repo", "rejected"),
    ("-c attached", ("-ctest.probe=value", "config", "--get", "test.probe"), "repo", "rejected"),
    ("--shallow-file equals", ("--shallow-file=shallow", "rev-parse", "--show-toplevel"), "repo", "unknown"),
    ("--list-cmds bare", ("--list-cmds", "rev-parse", "--show-toplevel"), "repo", "rejected"),
    ("--super-prefix", ("--super-prefix", "rev-parse", "--show-toplevel"), "repo", "unknown"),
    ("-C missing", ("-C",), "repo", "rejected"),
    ("-c missing", ("-c",), "repo", "rejected"),
    ("--git-dir missing", ("--git-dir",), "repo", "rejected"),
    ("--work-tree missing", ("--work-tree",), "repo", "rejected"),
    ("--namespace missing", ("--namespace",), "repo", "rejected"),
    ("--config-env missing", ("--config-env",), "repo", "rejected"),
    ("--shallow-file missing", ("--shallow-file",), "repo", "rejected"),
    ("--attr-source missing", ("--attr-source",), "repo", "rejected"),
)


class GitGlobalOptions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="git-global-options-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.nested = self.repo / "nested"
        self.nested.mkdir()
        self.shallow = self.repo / "shallow"
        self.shallow.write_text("")
        self.env = {
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Shim Test",
            "GIT_AUTHOR_EMAIL": "shim-test@example.invalid",
            "GIT_COMMITTER_NAME": "Shim Test",
            "GIT_COMMITTER_EMAIL": "shim-test@example.invalid",
            "DOTFILES_GIT_PROBE": "from-config-env",
            "PATH": f"{SHIMS}{os.pathsep}{os.environ['PATH']}",
        }
        self.real("init", "-q", str(self.repo), cwd=self.tmp, check=True)
        self.real("commit", "--allow-empty", "-qm", "init", cwd=self.repo, check=True)
        self.real("init", "-q", str(self.nested), cwd=self.repo, check=True)
        self.exec_path = self.real("--exec-path", cwd=self.repo, check=True).stdout.strip()

    def real(self, *argv: str, cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([REAL_GIT, *argv], cwd=cwd, env=self.env, capture_output=True, text=True, check=False)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def probe(self, argv: tuple[str, ...], cwd: Path) -> list[str]:
        result = subprocess.run(["git", *argv], cwd=cwd, env={**self.env, "DOTFILES_GIT_SHIM_READ_GLOBALS": "1"},
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        fields = result.stdout.split(b"\0")
        self.assertEqual(fields.pop(), b"", result.stdout)
        return [field.decode() for field in fields]

    def render(self, argv: tuple[str, ...]) -> tuple[str, ...]:
        values = {
            "git_dir": str(self.repo / ".git"),
            "repo": str(self.repo),
            "exec_path": self.exec_path,
        }
        return tuple(value.format(**values) for value in argv)

    def cwd(self, place: str) -> Path:
        if place == "root":
            return self.tmp
        if place == "gitdir":
            return self.repo / ".git"
        return self.repo

    def assert_runs_command(self, fields: list[str], command: str, real: subprocess.CompletedProcess[str], kind: str) -> None:
        self.assertEqual(real.returncode, 0, real.stderr)
        self.assertEqual(fields[0], command)
        if kind in {"top", "directory"}:
            self.assertEqual(real.stdout.strip(), str(self.repo.resolve()))
        elif kind == "bare":
            self.assertEqual(real.stdout, "false\n")
        elif kind == "config":
            self.assertEqual(fields[2:], [f"test.probe={real.stdout.strip()}"])
        if kind == "directory":
            self.assertEqual(fields[1], real.stdout.strip())

    def test_global_options_match_real_git(self) -> None:
        for name, prefix, kind, command, place in GLOBAL_OPTIONS:
            with self.subTest(name=name):
                cwd = self.cwd(place)
                argv = (*self.render(prefix), *command)
                real = self.real(*argv, cwd=cwd)
                fields = self.probe(argv, cwd)
                if kind in {"top", "bare", "config", "directory"}:
                    self.assert_runs_command(fields, command[0], real, kind)
                elif kind == "query":
                    self.assertEqual(real.returncode, 0, real.stderr)
                    self.assertNotEqual(real.stdout, "")
                    self.assertEqual(fields[0], "")
                else:
                    self.assertEqual(fields[0], "")
                if kind != "directory":
                    self.assertEqual(fields[1], str(cwd.resolve()))

    def test_rejected_options_have_no_command(self) -> None:
        for name, argv, place, kind in REJECTED_OPTIONS:
            with self.subTest(name=name):
                cwd = self.cwd(place)
                real = self.real(*argv, cwd=cwd)
                fields = self.probe(argv, cwd)
                self.assertNotEqual(real.returncode, 0)
                if kind == "unknown":
                    self.assertIn("unknown option", real.stderr)
                self.assertEqual(fields[0], "")
                self.assertEqual(fields[1], str(cwd.resolve()))

    def test_global_option_combinations(self) -> None:
        cases = (
            ("two -C", ("-C", "repo", "-C", "nested", "rev-parse", "--show-toplevel"), self.tmp, "nested"),
            ("-C after -c", ("-c", "test.probe=from-before-c", "-C", "repo", "config", "--get", "test.probe"), self.tmp, "repo"),
        )
        for name, argv, cwd, directory in cases:
            with self.subTest(name=name):
                real = self.real(*argv, cwd=cwd)
                fields = self.probe(argv, cwd)
                self.assertEqual(real.returncode, 0, real.stderr)
                self.assertEqual(fields[0], "rev-parse" if name == "two -C" else "config")
                self.assertEqual(fields[1], str((self.repo / directory).resolve()) if directory == "nested" else str(self.repo.resolve()))
                if argv[0] == "-c":
                    self.assertEqual(fields[2:], [f"test.probe={real.stdout.strip()}"])

        argv = ("-c", "-C", "config", "--get", "test.probe")
        real = self.real(*argv, cwd=self.repo)
        fields = self.probe(argv, self.repo)
        self.assertNotEqual(real.returncode, 0)
        self.assertIn("does not contain a section", real.stderr)
        self.assertEqual(fields[0], "config")
        self.assertEqual(fields[1], str(self.repo.resolve()))
        self.assertEqual(fields[2:], ["-C"])

    def test_alias_environment_rule_matches_git(self) -> None:
        self.real("config", "alias.st2", "-C . status", cwd=self.repo, check=True)
        self.real("config", "alias.st3", "-c test.probe=from-alias status", cwd=self.repo, check=True)
        for alias in ("st2", "st3"):
            with self.subTest(alias=alias):
                real = self.real(alias, "--porcelain", cwd=self.repo)
                shim = subprocess.run(["git", alias, "--porcelain"], cwd=self.repo, env=self.env,
                                      capture_output=True, text=True, check=False)
                self.assertEqual((shim.returncode, shim.stdout, shim.stderr),
                                 (real.returncode, real.stdout, real.stderr))
        self.assertIn("changes environment variables", self.real("st2", cwd=self.repo).stderr)
        self.assertEqual(self.real("st3", "--porcelain", cwd=self.repo).returncode, 0)


if __name__ == "__main__":
    unittest.main()
