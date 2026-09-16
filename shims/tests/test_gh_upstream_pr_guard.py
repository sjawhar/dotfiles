#!/usr/bin/env python3
"""gh shim: `pr create`/`pr edit` against an upstream owner (METR/,
UKGovernmentBEIS/, meridianlabs-ai/) is refused in an agent session unless
GH_UPSTREAM_PR_GRANT names the resolved target repo. Everything else forwards.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "gh"


class GhUpstreamPrGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.stub_dir = root / "bin"
        self.stub_dir.mkdir()
        self.argv_log = root / "argv"
        # No knives on this PATH, so the shim execs the stub directly. The stub
        # must not carry the knives-gh-shim marker or real_gh() would skip it.
        stub = self.stub_dir / "gh"
        stub.write_text(
            f'#!/bin/bash\nprintf "%s\\n" "$@" > {self.argv_log}\n', encoding="utf-8"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.plain_dir = root / "plain"
        self.plain_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def git_repo(self, name: str, remotes: dict[str, str], resolved: str | None = None) -> Path:
        repo = Path(self.temp_dir.name) / name
        repo.mkdir()
        env = {"PATH": "/usr/bin:/bin", "HOME": self.temp_dir.name}
        subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True)
        for remote, url in remotes.items():
            subprocess.run(["git", "remote", "add", remote, url], cwd=repo, env=env, check=True)
        if resolved is not None:
            subprocess.run(
                ["git", "config", f"remote.{resolved}.gh-resolved", "base"],
                cwd=repo, env=env, check=True,
            )
        return repo

    def run_shim(self, *args: str, cwd: Path | None = None, agent: bool = True,
                 grant: str | None = None, gh_repo: str | None = None):
        env = {
            "PATH": f"{SHIM.parent}:{self.stub_dir}:/usr/bin:/bin",
            "HOME": self.temp_dir.name,
            "DOTFILES_DIR": str(DOTFILES),
        }
        if agent:
            env["OMP_SESSION_ID"] = "test-session"
        if grant is not None:
            env["GH_UPSTREAM_PR_GRANT"] = grant
        if gh_repo is not None:
            env["GH_REPO"] = gh_repo
        return subprocess.run(
            [str(SHIM), *args], env=env, cwd=cwd or self.plain_dir,
            capture_output=True, text=True,
        )

    def assert_forwarded(self, result, *argv: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.argv_log.read_text().split("\n")[:-1], list(argv))
        self.argv_log.unlink()

    def assert_refused(self, result, slug: str, sub: str = "create") -> None:
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertIn(f"refusing 'gh pr {sub}' against upstream {slug}", result.stderr)
        self.assertIn(f"GH_UPSTREAM_PR_GRANT={slug}", result.stderr)
        self.assertFalse(self.argv_log.exists(), "real gh must not run")

    # --- refusals ---

    def test_create_with_explicit_upstream_repo_is_refused(self) -> None:
        self.assert_refused(self.run_shim("pr", "create", "-R", "METR/hawk", "--title", "x"),
                            "metr/hawk")
        self.assert_refused(self.run_shim("pr", "create", "--repo=UKGovernmentBEIS/inspect_ai"),
                            "ukgovernmentbeis/inspect_ai")
        self.assert_refused(
            self.run_shim("pr", "create", "-R", "https://github.com/meridianlabs-ai/inspect_swe.git"),
            "meridianlabs-ai/inspect_swe")

    def test_create_with_gh_repo_env_is_refused(self) -> None:
        self.assert_refused(self.run_shim("pr", "create", "--title", "x", gh_repo="METR/hawk"),
                            "metr/hawk")

    def test_bare_create_in_checkout_with_upstream_remote_is_refused(self) -> None:
        repo = self.git_repo("fork", {
            "origin": "https://github.com/sjawhar/hawk.git",
            "upstream": "https://github.com/METR/hawk.git",
        })
        self.assert_refused(self.run_shim("pr", "create", "--title", "x", cwd=repo), "metr/hawk")

    def test_resolved_marker_on_upstream_remote_is_refused(self) -> None:
        repo = self.git_repo("resolved-upstream", {
            "origin": "https://github.com/sjawhar/hawk.git",
            "upstream": "https://github.com/METR/hawk.git",
        }, resolved="upstream")
        self.assert_refused(self.run_shim("pr", "create", cwd=repo), "metr/hawk")

    def test_edit_against_upstream_is_refused(self) -> None:
        self.assert_refused(self.run_shim("pr", "edit", "1742", "-R", "METR/hawk"),
                            "metr/hawk", sub="edit")
        self.assert_refused(
            self.run_shim("pr", "edit", "https://github.com/METR/hawk/pull/1742", "--title", "x"),
            "metr/hawk", sub="edit")

    def test_grant_for_another_repo_does_not_count(self) -> None:
        self.assert_refused(
            self.run_shim("pr", "create", "-R", "UKGovernmentBEIS/inspect_ai", grant="metr/hawk"),
            "ukgovernmentbeis/inspect_ai")

    # --- pass-throughs ---

    def test_non_mutating_and_non_pr_commands_forward(self) -> None:
        self.assert_forwarded(self.run_shim("pr", "view", "-R", "METR/hawk", "1742"),
                              "pr", "view", "-R", "METR/hawk", "1742")
        self.assert_forwarded(self.run_shim("api", "repos/METR/hawk"),
                              "api", "repos/METR/hawk")

    def test_create_against_org_and_own_repos_forwards(self) -> None:
        self.assert_forwarded(self.run_shim("pr", "create", "-R", "trajectory-labs-pbc/hawk"),
                              "pr", "create", "-R", "trajectory-labs-pbc/hawk")
        self.assert_forwarded(self.run_shim("pr", "create", "-R", "sjawhar/knives"),
                              "pr", "create", "-R", "sjawhar/knives")

    def test_bare_create_with_only_org_remotes_forwards(self) -> None:
        repo = self.git_repo("org-only", {"origin": "https://github.com/trajectory-labs-pbc/agent-c.git"})
        self.assert_forwarded(self.run_shim("pr", "create", "--title", "x", cwd=repo),
                              "pr", "create", "--title", "x")

    def test_resolved_marker_on_org_remote_forwards_despite_upstream_remote(self) -> None:
        repo = self.git_repo("resolved-org", {
            "origin": "https://github.com/sjawhar/hawk.git",
            "upstream": "https://github.com/METR/hawk.git",
        }, resolved="origin")
        self.assert_forwarded(self.run_shim("pr", "create", cwd=repo), "pr", "create")

    def test_matching_grant_forwards(self) -> None:
        self.assert_forwarded(
            self.run_shim("pr", "create", "-R", "METR/hawk", "--title", "x", grant="metr/hawk"),
            "pr", "create", "-R", "METR/hawk", "--title", "x")
        self.assert_forwarded(
            self.run_shim("pr", "edit", "1742", "-R", "METR/hawk", grant="metr/hawk"),
            "pr", "edit", "1742", "-R", "METR/hawk")

    def test_outside_agent_context_the_guard_does_not_run(self) -> None:
        self.assert_forwarded(self.run_shim("pr", "create", "-R", "METR/hawk", agent=False),
                              "pr", "create", "-R", "METR/hawk")


if __name__ == "__main__":
    unittest.main()
