#!/usr/bin/env python3
"""A gh call that arrives over SSH with no terminal is an agent's (shims/tests/test_shim_agent_context.py): `ssh
sami-devbox gh …` carries no session marker, so the SSH rule decides, and the call takes the agent route: the agent
config directory, the App routing, the refusal of `gh auth` verbs and the public-text leak check. A person's own SSH
session (SSH_TTY set, or a terminal), and a person who sets AGENT_SHIM_PERSON=1, reach gh as before. knives and gh are
stubs; nothing reaches GitHub."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
DOTFILES = SHIMS.parent
GH = SHIMS / "gh"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from leak_terms_fixture import leak_check_environment  # noqa: E402
from stdin_cases import SSH_CONNECTION, SSH_TTY, run_with_tty, run_without_tty  # noqa: E402

KNIVES = """#!/bin/bash
# stub knives: `knives gh -- ARGV`; routes (a token in GH_TOKEN) when GH_REPO names a repository, as knives does
shift; [[ "${1:-}" == -- ]] && shift
if [[ -z "${GH_TOKEN:-}" ]]; then
    if [[ -n "${GH_REPO:-}" ]]; then export GH_TOKEN=$STUB_APP_TOKEN
    else echo "knives gh: no target repository (stub)" >&2; fi
fi
exec "$KNIVES_REAL_GH" "$@"
"""
REAL_GH = """#!/bin/bash
printf 'token=%s cfg=%s args=%s\\n' "${GH_TOKEN:-none}" "${GH_CONFIG_DIR:-none}" "$*" >> "$GH_LOG"
echo '{"data": {}}'
"""
NOTICE = "gh shim: non-interactive SSH, so an agent's call"


class SshAgentContext(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, text in (("knives", KNIVES), ("gh", REAL_GH), ("leak-check", "#!/bin/sh\nexit 0\n")):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        self.log = self.tmp / "gh.log"
        self.log.touch()
        config = self.tmp / "config"
        (config / "gh-agent").mkdir(parents=True)
        (config / "gh-agent" / "hosts.yml").write_text("github.com:\n    oauth_token: placeholder\n")
        (config / "gh-agent" / "config.yml").write_text("")
        self.work = self.tmp / "work"  # no repository here
        self.work.mkdir()
        self.base = {
            # The leak-term check reads every write's text (shims/gh).
            **leak_check_environment(os.pathsep.join([str(SHIMS), str(self.bin), "/usr/bin", "/bin"])),
            "HOME": str(self.tmp / "home"),
            "USER": "no-such-user-gh-ssh-test",
            "DOTFILES_DIR": str(DOTFILES),
            "XDG_CONFIG_HOME": str(config),
            "PUBLIC_LEAK_CHECK": str(self.bin / "leak-check"),
            "GH_LOG": str(self.log),
            "STUB_APP_TOKEN": "stub-app-token",
            "TMPDIR": str(self.tmp),
        }

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def gh_calls(self):
        return self.log.read_text().splitlines()

    def agent_cfg(self) -> Path:
        """The agent config directory the shim gives gh (scripts/gh-agent-config makes one there)."""
        return Path(self.base["XDG_CONFIG_HOME"]) / "gh-agent"

    def as_person(self, args: list[str]) -> list[str]:
        """What the stub gh records for a person's call: no token routed, and gh's own config directory."""
        return [f"token=none cfg=none args={' '.join(args)}"]

    MUTATION = ["api", "graphql", "-f", 'query=mutation { resolveReviewThread(input: {threadId: "PRRT_x"}) { thread { id } } }']

    def test_a_call_over_ssh_with_no_terminal_takes_the_agent_route(self):
        env = {**self.base, "SSH_CONNECTION": SSH_CONNECTION, "GH_REPO": "o/r"}
        result = run_without_tty([str(GH), "api", "user"], env=env, cwd=self.work, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(NOTICE, result.stderr)
        self.assertEqual(self.gh_calls(), [f"token=stub-app-token cfg={self.agent_cfg()} args=api user"])

    def test_a_persons_ssh_session_reaches_gh_as_the_person(self):
        # Whatever this one command reads on stdin: a pipe into it is not an agent (review of
        # INFRA-1538).
        for run in (run_with_tty, run_without_tty):
            with self.subTest(run=run.__name__):
                self.log.write_text("")
                env = {**self.base, "SSH_CONNECTION": SSH_CONNECTION, "SSH_TTY": SSH_TTY}
                result = run([str(GH), "api", "user"], env=env, cwd=self.work, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(NOTICE, result.stderr)
                self.assertEqual(self.gh_calls(), self.as_person(["api", "user"]))

    def test_a_persons_pane_with_no_ssh_tty_reaches_gh_as_the_person(self):
        # dispatch://INFRA-4089: Sami's own pane of a tmux server that never carried SSH_TTY holds
        # SSH_CONNECTION and no SSH_TTY. The pane's terminal is the session's, piped stdin or not.
        for stdin in (None, ""):
            with self.subTest(piped=stdin is not None):
                self.log.write_text("")
                result = run_with_tty([str(GH), "api", "user"], input=stdin, cwd=self.work, timeout=120,
                                      env={**self.base, "SSH_CONNECTION": SSH_CONNECTION})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(NOTICE, result.stderr)
                self.assertEqual(self.gh_calls(), self.as_person(["api", "user"]))

    def test_agent_shim_person_reaches_gh_as_the_person(self):
        env = {**self.base, "SSH_CONNECTION": SSH_CONNECTION, "AGENT_SHIM_PERSON": "1"}
        result = run_without_tty([str(GH), "api", "user"], env=env, cwd=self.work, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(NOTICE, result.stderr)
        self.assertEqual(self.gh_calls(), self.as_person(["api", "user"]))

    # A machine that names gh-app.host-profile in its own git config (sami-devbox,
    # installers/gh-app-host.sh) is NOT an exception to any of this. The first draft of INFRA-1538
    # kept the host route for such a call and so lifted it out of the whole agent block: past the
    # leak check, past the refusal of the person's own login and past the agent config directory
    # (review of INFRA-1538).
    def host_profile(self) -> dict[str, str]:
        config = Path(self.base["XDG_CONFIG_HOME"]) / "git"
        config.mkdir(parents=True, exist_ok=True)
        (config / "config").write_text("[gh-app]\n\thost-profile = agent\n")
        return {**self.base, "SSH_CONNECTION": SSH_CONNECTION}

    def test_a_targetless_call_over_ssh_takes_the_agent_route_on_a_host_profile_machine(self):
        # The host route would leave GH_CONFIG_DIR alone and ask gh-app-token for an owner-wide token.
        result = run_without_tty([str(GH), *self.MUTATION], env=self.host_profile(), cwd=self.work, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(NOTICE, result.stderr)
        self.assertEqual(self.gh_calls(), [f"token=none cfg={self.agent_cfg()} args={' '.join(self.MUTATION)}"])

    def test_the_leak_check_still_runs_over_ssh_on_a_host_profile_machine(self):
        check = self.bin / "leak-check"
        check.write_text('#!/bin/sh\necho LEAK-CHECK-CALLED >&2\nexit 1\n')
        check.chmod(0o755)
        env = {**self.host_profile(), "GH_REPO": "o/r"}
        result = run_without_tty([str(GH), "issue", "comment", "1", "--body", "x"], env=env,
                                 cwd=self.work, timeout=120)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("LEAK-CHECK-CALLED", result.stderr)
        self.assertEqual(self.gh_calls(), [], "gh ran although the leak check refused the call")

    def test_gh_auth_token_is_still_refused_over_ssh_on_a_host_profile_machine(self):
        result = run_without_tty([str(GH), "auth", "token"], env=self.host_profile(), cwd=self.work, timeout=120)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("gh shim: REFUSED", result.stderr)
        self.assertEqual(self.gh_calls(), [], "gh auth token reached the real gh")


if __name__ == "__main__":
    unittest.main()
