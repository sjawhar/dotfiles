#!/usr/bin/env python3
"""gh outside an agent session on a machine with no person's GitHub login gets the App's token.

On sami-devbox (dispatch://INFRA-993) a host shell's gh had no token: installers/agent-spend.sh's
`gh api` fetch failed, and a hooked push was refused because the leak check could not read the
repository's visibility. Where the machine's own git config names `gh-app.host-profile`
(installers/gh-app-host.sh), shims/gh mints an installation token for the call's target owner
with `gh-app-token <profile> --owner OWNER` and gives it to gh as GH_TOKEN. gh-app-token and the
real gh are stubs that record how they were called.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stdin_cases import SSH_CONNECTION, SSH_TTY, run_without_tty  # noqa: E402
PROFILE = "[gh-app]\n\thost-profile = agent\n"


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


class HostToken(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-host-token-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.calls = self.tmp / "calls"
        stubs, real = self.tmp / "stubs", self.tmp / "real"
        stubs.mkdir()
        real.mkdir()
        # Prints an App token, or the personal fallback token for owner `upstream`.
        executable(stubs / "gh-app-token",
                   f'echo "gh-app-token $* life=$GH_APP_TOKEN_MIN_LIFETIME" >> "{self.calls}"\n'
                   'if [[ "$3" == upstream ]]; then echo ghp_PERSONAL; else echo "ghs_APP_$3"; fi')
        executable(real / "gh", f'echo "real gh $* token=${{GH_TOKEN:-none}}" >> "{self.calls}"')
        self.env = {
            "HOME": str(self.home), "LANG": "C.UTF-8", "TMPDIR": str(self.tmp),
            "PATH": os.pathsep.join([str(stubs), str(SHIMS), str(real), "/usr/bin", "/bin"]),
            "KNIVES_GH_BYPASS": "1", "GH_READ_CACHE": "0", "GH_READ_CACHE_LOG": "0",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        }

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def machine_config(self, text: str = PROFILE) -> None:
        path = self.home / ".config" / "git" / "config"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def gh(self, *argv: str, cwd: Path | None = None, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(["gh", *argv], env={**self.env, **env}, capture_output=True, text=True,
                              cwd=cwd or self.tmp, timeout=60, check=False)

    def recorded(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_api_repos_path_names_the_owner(self):
        self.machine_config()
        self.gh("api", "repos/theorem-labs/agent-infra", "--jq", ".full_name")
        self.assertEqual(self.recorded(), [
            "gh-app-token agent --owner theorem-labs life=1800",
            "real gh api repos/theorem-labs/agent-infra --jq .full_name token=ghs_APP_theorem-labs",
        ])

    def test_repo_flags_name_the_owner(self):
        self.machine_config()
        for argv in (["pr", "list", "-R", "sjawhar/dotfiles"], ["pr", "list", "--repo=sjawhar/dotfiles"],
                     ["pr", "list", "-Rsjawhar/dotfiles"], ["pr", "list", "--repo", "github.com/sjawhar/dotfiles"]):
            self.calls.unlink(missing_ok=True)
            self.gh(*argv)
            self.assertEqual(self.recorded()[0], "gh-app-token agent --owner sjawhar life=1800", argv)

    def test_gh_repo_env_names_the_owner(self):
        self.machine_config()
        self.gh("issue", "list", GH_REPO="sjawhar/legion")
        self.assertEqual(self.recorded()[0], "gh-app-token agent --owner sjawhar life=1800")

    def test_origin_remote_names_the_owner(self):
        self.machine_config()
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], env=self.env, check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/METR/vivaria.git"],
                       env=self.env, check=True)
        self.gh("pr", "list", cwd=repo)
        self.assertEqual(self.recorded()[0], "gh-app-token agent --owner METR life=1800")

    def test_no_target_defaults_to_theorem_labs(self):
        self.machine_config()
        self.gh("api", "user")
        self.assertEqual(self.recorded()[0], "gh-app-token agent --owner theorem-labs life=1800")

    def test_a_personal_fallback_token_is_not_used(self):
        self.machine_config()
        result = self.gh("api", "repos/upstream/x")
        self.assertIn("no GitHub App installation token for owner upstream", result.stderr)
        self.assertEqual(self.recorded()[1], "real gh api repos/upstream/x token=none")

    def test_a_caller_token_wins(self):
        self.machine_config()
        for var in ("GH_TOKEN", "GITHUB_TOKEN"):
            self.calls.unlink(missing_ok=True)
            self.gh("api", "user", **{var: "ghs_CALLER"})
            self.assertFalse(any(c.startswith("gh-app-token") for c in self.recorded()), var)

    def test_without_a_host_profile_nothing_is_minted(self):
        self.gh("api", "user")
        self.assertEqual(self.recorded(), ["real gh api user token=none"])

    def test_auth_verbs_reach_the_real_gh_unchanged(self):
        self.machine_config()
        self.gh("auth", "status")
        self.assertEqual(self.recorded(), ["real gh auth status token=none"])

    # INFRA-1538, and its review: a command over SSH with no terminal in its session is an
    # agent's, and a host profile is NOT an exception to that. The host token below is for a
    # person's own shell and for the machine's own units; an agent's call takes the agent route,
    # with every agent safeguard (test_gh_unrouted.py), and mints no owner-wide host token here.
    def test_a_call_over_ssh_takes_the_agent_route_and_mints_no_host_token(self):
        self.machine_config()
        result = run_without_tty(["gh", "api", "repos/theorem-labs/x"],
                                 env={**self.env, "SSH_CONNECTION": SSH_CONNECTION,
                                      "DOTFILES_DIR": str(SHIMS.parent)},
                                 cwd=self.tmp, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse([c for c in self.recorded() if c.startswith("gh-app-token")], self.recorded())
        self.assertEqual(self.recorded()[-1], "real gh api repos/theorem-labs/x token=none")

    def test_a_persons_ssh_session_still_gets_the_host_token(self):
        self.machine_config()
        result = run_without_tty(["gh", "api", "repos/theorem-labs/x"],
                                 env={**self.env, "SSH_CONNECTION": SSH_CONNECTION, "SSH_TTY": SSH_TTY},
                                 cwd=self.tmp, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()[-1], "real gh api repos/theorem-labs/x token=ghs_APP_theorem-labs")


if __name__ == "__main__":
    unittest.main()
