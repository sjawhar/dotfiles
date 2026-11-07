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
import sys
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stdin_cases import SSH_CONNECTION, SSH_TTY, run_with_tty, run_without_tty  # noqa: E402
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

    # INFRA-1538, and its review: a command over SSH with no terminal in its session is an
    # agent's, and the host profile is no exception to that — `gh-app-token` registers the broker
    # session itself (`secret-run --register`), the profile installers/gh-app-host.sh writes IS
    # `agent`, and the exception the first draft made lifted such a call out of every other agent
    # safeguard too. So `ssh sami-devbox git clone …` now answers through the agent route, and
    # only a person's own session (SSH_TTY set) keeps the host route. The profile name tells the
    # two apart: the host route passes it on, the agent route says `agent`.
    HOST_PROFILE = "[gh-app]\n\thost-profile = hostprof\n"

    def test_a_credential_request_over_ssh_takes_the_agent_route(self):
        self.machine_config(self.HOST_PROFILE)
        env = {**self.env, "SSH_CONNECTION": SSH_CONNECTION, "DOTFILES_DIR": str(SHIMS.parent)}
        result = run_without_tty(["gh", "auth", "git-credential", "get"], env=env, cwd=self.tmp, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()[0], "gh-app-token agent get")

    def test_a_persons_ssh_session_keeps_the_host_route(self):
        # Git hands a credential helper its request down a pipe, so this case has no terminal on
        # stdin and must still be the person's (review of INFRA-1538).
        self.machine_config(self.HOST_PROFILE)
        for run in (run_without_tty, run_with_tty):
            with self.subTest(run=run.__name__):
                self.calls.unlink(missing_ok=True)
                env = {**self.env, "SSH_CONNECTION": SSH_CONNECTION, "SSH_TTY": SSH_TTY}
                result = run(["gh", "auth", "git-credential", "get"], env=env, cwd=self.tmp, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.recorded()[0], "gh-app-token hostprof get")

    def test_git_in_a_persons_ssh_session_keeps_the_host_route(self):
        # The whole chain the review drove: git in the person's interactive SSH shell, through
        # ~/.gitconfig's `!gh auth git-credential` helper, which reads its request from a pipe.
        self.shared_config('[credential "https://github.com"]\n\thelper =\n\thelper = !gh auth git-credential\n'
                           "[credential]\n\tuseHttpPath = true\n")
        self.machine_config(self.HOST_PROFILE)
        env = {**self.env, "SSH_CONNECTION": SSH_CONNECTION, "SSH_TTY": SSH_TTY}
        result = subprocess.run(["git", "credential", "fill"], env=env, input=REQUEST, capture_output=True,
                                text=True, cwd=self.tmp, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("password=ghs_APPSTUB", result.stdout)
        self.assertEqual(self.recorded()[0], "gh-app-token hostprof get")

    def test_a_session_marker_over_ssh_still_takes_the_agent_route(self):
        self.machine_config(self.HOST_PROFILE)
        env = {**self.env, "SSH_CONNECTION": SSH_CONNECTION, "OMP_SESSION_ID": "01a1-session",
               "DOTFILES_DIR": str(SHIMS.parent)}
        result = run_without_tty(["gh", "auth", "git-credential", "get"], env=env, cwd=self.tmp, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()[0], "gh-app-token agent get")


if __name__ == "__main__":
    unittest.main()
