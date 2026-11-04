#!/usr/bin/env python3
"""git outside an agent session on a machine with no person's GitHub login gets the App's token.

~/.gitconfig names `!gh auth git-credential` as the user's helper for github.com. On sami-devbox
(dispatch://INFRA-993) no person's login is allowed, so an ssh shell's `git ls-remote` of a private
theorem-labs repository failed with "could not read Username". A machine like that sets
`gh-app.host-profile` in its own git config (installers/gh-app-host.sh), and shims/gh then answers
the helper call with `gh-app-token <profile> get`. Everywhere else the call reaches the real gh as
before. gh-app-token and the real gh are stubs that record how they were called.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
REQUEST = "protocol=https\nhost=github.com\npath=theorem-labs/machcsl-riscv\n\n"
PROFILE = "[gh-app]\n\thost-profile = agent\n"


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


class HostCredential(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-host-credential-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.calls = self.tmp / "calls"
        stubs, real = self.tmp / "stubs", self.tmp / "real"
        stubs.mkdir()
        real.mkdir()
        # Ahead of shims/ on PATH, so the shim's `exec gh-app-token` reaches it.
        executable(stubs / "gh-app-token",
                   f'{{ echo "gh-app-token $*"; cat; }} >> "{self.calls}"\n'
                   'printf "username=x-access-token\\npassword=ghs_APPSTUB\\n"')
        # Past shims/ on PATH: the real gh the shim falls through to.
        executable(real / "gh", f'echo "real gh $*" >> "{self.calls}"; cat > /dev/null; exit 1')
        self.env = {
            "HOME": str(self.home), "LANG": "C.UTF-8", "TMPDIR": str(self.tmp),
            "PATH": os.pathsep.join([str(stubs), str(SHIMS), str(real), "/usr/bin", "/bin"]),
            "KNIVES_GH_BYPASS": "1", "GH_READ_CACHE": "0", "GH_READ_CACHE_LOG": "0",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        }

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def machine_config(self, text: str) -> None:
        """The machine's own git config file, ~/.config/git/config, where gh-app-host.sh writes."""
        path = self.home / ".config" / "git" / "config"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def shared_config(self, text: str) -> None:
        """~/.gitconfig, the file every machine shares through the dotfiles."""
        (self.home / ".gitconfig").write_text(text)

    def gh(self, *argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["gh", *argv], env=self.env, input=REQUEST, capture_output=True, text=True,
                              cwd=cwd or self.tmp, timeout=60, check=False)

    def recorded(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_a_host_profile_sends_get_to_the_app_helper_with_the_request(self):
        self.machine_config(PROFILE)
        result = self.gh("auth", "git-credential", "get")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("password=ghs_APPSTUB", result.stdout)
        self.assertEqual(self.recorded(), ["gh-app-token agent get", *REQUEST.splitlines()])

    def test_store_and_erase_end_in_the_shim(self):
        self.machine_config(PROFILE)
        for verb in ("store", "erase"):
            self.assertEqual(self.gh("auth", "git-credential", verb).returncode, 0)
        self.assertEqual(self.recorded(), [])

    def test_without_a_host_profile_the_real_gh_answers(self):
        self.gh("auth", "git-credential", "get")
        self.assertEqual(self.recorded(), ["real gh auth git-credential get"])

    def test_only_the_machine_file_can_choose_the_host_profile(self):
        # Not the shared ~/.gitconfig, which every machine reads, and not a repository's config.
        self.shared_config(PROFILE)
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], env=self.env, check=True)
        subprocess.run(["git", "-C", str(repo), "config", "gh-app.host-profile", "agent"], env=self.env, check=True)
        self.gh("auth", "git-credential", "get", cwd=repo)
        self.assertEqual(self.recorded(), ["real gh auth git-credential get"])

    def test_other_gh_calls_reach_the_real_gh(self):
        # Their token is test_gh_host_token.py's subject; here only that the real gh runs them.
        self.machine_config(PROFILE)
        self.gh("api", "user")
        self.assertEqual(self.recorded()[-1], "real gh api user")

    def test_git_through_the_user_helper_gets_the_app_token(self):
        # ~/.gitconfig's own shape (the helper list reset, then gh's helper, for github.com) beside
        # the machine file: with both present, git 2.43's `config --global` reads ~/.gitconfig alone.
        self.shared_config('[credential "https://github.com"]\n\thelper =\n\thelper = !gh auth git-credential\n'
                           "[credential]\n\tuseHttpPath = true\n")
        self.machine_config(PROFILE)
        result = subprocess.run(["git", "credential", "fill"], env=self.env, input=REQUEST, capture_output=True,
                                text=True, cwd=self.tmp, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("password=ghs_APPSTUB", result.stdout)
        self.assertEqual(self.recorded()[0], "gh-app-token agent get")


if __name__ == "__main__":
    unittest.main()
