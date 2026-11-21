#!/usr/bin/env python3
"""The Claude Code launches of scripts/agent-private-check: which settings files' `env` blocks the
check reads, as Claude Code 2.1.316 applies them (its trust function, print mode as its argument
parser reads it, `--settings`, scripts/cld's own settings), and the refusal of a project file Claude
Code would apply once the folder's trust is accepted.

Technique: the real shims/claude and scripts/cld on private_check_support's scratch machine, against
its stand-in flag object and Dispatch; the check's parts alone are in test_agent_private_check_units.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from private_check_support import PRIVATE, Case
from private_project_stubs import PRIV_TOKEN, PUB_TOKEN, WHOAMI, WHOAMI_PUB, DispatchStub


class ClaudeCodeSettings(Case):
    TRUST = "for Dispatch, and Claude Code applies it once this folder's trust is accepted"

    def claude(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return self.m.run([str(self.m.tree / "shims" / "claude"), *args], {}, self.m.tree, cwd=cwd)

    def trust(self, *folders: Path) -> None:
        (self.m.home / ".claude.json").write_text(json.dumps(
            {"projects": {str(folder): {"hasTrustDialogAccepted": True} for folder in folders}}))

    def private_settings(self, folder: Path) -> None:
        (folder / ".claude").mkdir(parents=True)
        (folder / ".claude" / "settings.json").write_text(json.dumps(
            {"env": {"DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}}))

    def listening_clone(self) -> tuple[Path, DispatchStub]:
        """An untrusted clone whose settings name a one-line secret file and a listener of their own."""
        listener = DispatchStub({PUB_TOKEN: WHOAMI_PUB})
        self.addCleanup(listener.stop)
        clone = self.tmp / "untrusted-clone"
        (clone / ".claude").mkdir(parents=True)
        secret = self.tmp / "single-line-secret"
        secret.write_text(PUB_TOKEN + "\n")
        (clone / ".claude" / "settings.json").write_text(json.dumps({"env": {
            "DISPATCH_TOKEN_FILE": str(secret), "DISPATCH_URL": listener.url}}))
        return clone, listener

    def test_an_untrusted_clones_settings_refuse_an_interactive_start_and_are_not_sent(self) -> None:
        """Claude Code applies a project's `env` block in a -p run, in a trusted folder, or once the
        person accepts the folder's trust dialog, which no check can see before the start: so an
        interactive start in an untrusted clone whose settings name a bearer is refused, and nothing
        the file names is read or sent."""
        clone, listener = self.listening_clone()
        self.refused(self.claude(clone, "hello"), self.TRUST)
        self.refused(self.m.run([str(self.m.tree / "scripts" / "cld"), "hello"], {}, self.m.tree, cwd=clone), self.TRUST)
        self.assertEqual(listener.calls, [])
        # Controls: a -p run applies the block, and so does an interactive start once the folder is trusted.
        self.started(self.claude(clone, "-p", "hello"), "claude")
        self.assertEqual(listener.paths(), [WHOAMI])
        self.trust(clone)
        self.started(self.claude(clone, "hello"), "claude")
        self.assertEqual(listener.paths(), [WHOAMI, WHOAMI])

    def test_a_short_option_that_takes_its_clusters_rest_is_not_print_mode(self) -> None:
        """`-nprod` names the session "prod" (its parser gives `-n` the rest of the cluster): an
        interactive start, so the untrusted clone's settings are refused, not read and sent."""
        clone, listener = self.listening_clone()
        for args in (("-nprod",), ("-n", "prod"), ("-dapi",), ("-n", "-prod")):
            with self.subTest(args=args):
                self.refused(self.claude(clone, *args), self.TRUST)
        self.assertEqual(listener.calls, [])
        # Control: `-cp` is print mode, so the block is read and its bearer asked about at its server.
        self.started(self.claude(clone, "-cp", "hi"), "claude")
        self.assertEqual(listener.paths(), [WHOAMI])

    def test_a_flag_given_a_value_is_not_print_mode(self) -> None:
        """Claude Code rejects `--print=false` (`error: unknown option '--print=false'`) and starts
        nothing, so the untrusted clone's settings are refused, not read and sent."""
        clone, listener = self.listening_clone()
        for args in (("--print=false", "hi"), ("--verbose=x", "-p")):
            with self.subTest(args=args):
                self.refused(self.claude(clone, *args), self.TRUST)
        self.assertEqual(listener.calls, [])
        # Control: `--print hi` is print mode, so the block is read and its bearer asked about once.
        self.started(self.claude(clone, "--print", "hi"), "claude")
        self.assertEqual(listener.paths(), [WHOAMI])

    def test_a_subfolder_of_a_trusted_repository_is_checked(self) -> None:
        repo = self.tmp / "trusted-repo"
        (repo / ".git").mkdir(parents=True)
        self.private_settings(repo / "sub")
        self.trust(repo)
        self.refused(self.claude(repo / "sub", "hello"), PRIVATE)
        # Control: a record above the repository's root does not reach it, so its settings are not
        # read: the start is refused for trust, and Dispatch is asked nothing.
        self.trust(self.tmp)
        self.refused(self.claude(repo / "sub", "hello"), self.TRUST)
        self.assertEqual(self.m.dispatch.paths(), [WHOAMI])

    def test_a_clone_under_a_trusted_parent_is_not_trusted(self) -> None:
        parent = self.tmp / "trusted-parent"
        clone = parent / "clone"
        (clone / ".git").mkdir(parents=True)
        self.private_settings(clone)
        self.trust(parent)
        self.refused(self.claude(clone, "hello"), self.TRUST)
        self.assertEqual(self.m.dispatch.paths(), [])
        # Control: the same folder outside a repository is trusted through its parent, and checked.
        shutil.rmtree(clone / ".git")
        self.refused(self.claude(clone, "hello"), PRIVATE)

    def test_a_short_flag_cluster_holding_p_is_print_mode(self) -> None:
        folder = self.tmp / "untrusted-folder"
        self.private_settings(folder)
        for flags in ("-cp", "-pc", "--print"):
            with self.subTest(flags=flags):
                self.refused(self.claude(folder, flags, "hi"), PRIVATE)
        # Control: `-c` is an interactive start, so the settings are not read: refused for trust, never asked.
        calls = len(self.m.dispatch.paths())
        self.refused(self.claude(folder, "-c", "hi"), self.TRUST)
        self.assertEqual(len(self.m.dispatch.paths()), calls)

    def test_a_settings_env_block_bearer_is_checked(self) -> None:
        project = self.tmp / "claude-project"
        settings = project / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"env": {"DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}}))
        for argv in ([str(self.m.tree / "shims" / "claude"), "-p", "ok"], [str(self.m.tree / "scripts" / "cld"), "-p", "ok"]):
            with self.subTest(launcher=Path(argv[0]).name):
                self.refused(self.m.run(argv, {}, self.m.tree, cwd=project), PRIVATE)
        user = self.tmp / "claude-config"
        (user / "settings.json").parent.mkdir(parents=True)
        (user / "settings.json").write_text(settings.read_text())
        self.refused(self.m.shim("claude", "-p", "ok", CLAUDE_CONFIG_DIR=str(user)), PRIVATE)
        # Control: codex reads no Claude Code settings.
        self.started(self.m.run([str(self.m.tree / "shims" / "codex"), "exec", "ok"], {}, self.m.tree, cwd=project), "codex")

    def test_a_settings_files_env_block_is_checked(self) -> None:
        # scripts/cld passes its profile's cld-settings.json with --settings, after its own flags.
        cld_settings = self.m.tree / ".claude" / "cld-settings.json"
        cld_settings.write_text(json.dumps({"env": {"DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}}))
        self.refused(self.m.cld("-p", "ok"), PRIVATE)
        # Control: `cld --version` is a version, past cld's own flags, so it starts with no check.
        self.started(self.m.cld("--version"), "claude")
        cld_settings.unlink()
        self.started(self.m.cld("-p", "ok"), "claude")
        inline = json.dumps({"env": {"DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}})
        multiline = json.dumps({"model": "sonnet", "env": {"DISPATCH_TOKEN": PRIV_TOKEN, "DISPATCH_URL": self.m.dispatch.url}},
                               indent=2)
        (self.tmp / "named.json").write_text(inline)
        for args in (("--settings", str(self.tmp / "named.json")), (f"--settings={inline}",), ("--settings", multiline)):
            with self.subTest(args=args[0][:12], lines=args[-1].count("\n") + 1):
                self.refused(self.m.shim("claude", *args, "-p", "ok"), PRIVATE)
        # Control: a multi-line inline value with no bearer starts, through cld too.
        harmless = json.dumps({"model": "sonnet", "env": {"HARMLESS": "1"}}, indent=2)
        self.started(self.m.shim("claude", "--settings", harmless, "-p", "ok"), "claude")
        self.started(self.m.cld("--settings", harmless, "-p", "ok"), "claude")


if __name__ == "__main__":
    unittest.main()
