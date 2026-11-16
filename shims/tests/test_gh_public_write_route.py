#!/usr/bin/env python3
"""A gh write to an owner the GitHub App does not cover runs under the broker (dispatch://INFRA-2031).

gh-app-token no longer hands out a personal token, so shims/gh re-execs such a write as
`secret-run --register … GITHUB_PUBLIC_REPO_PAT -- sh -c 'GH_TOKEN="$GITHUB_PUBLIC_REPO_PAT" exec <shim> …'`,
and the call holds only the airlock's placeholder. On 2026-11-08 an agent posted on
FreeMixer/plugin-hostd, which has no installation, as the user (dispatch://INFRA-2006); the write
gate added for that refused every such write, and Sami answered "Stop manufacturing a permission
boundary here" (dispatch://INFRA-1475/ask/c23d0d6c). These tests drive the shim and the real
scripts/secret-run of this checkout with a stub gh-app-token, a stub agent-secrets that records its
argv and, asked to, runs the command with a placeholder, and a stub knives that records what reached
it. Nothing contacts GitHub.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).resolve().parents[1]
GH_SHIM = SHIMS / "gh"
PLACEHOLDER = "agent-secrets-placeholder-0123456789abcdefghijklmnopqrstuv"
COMMENT = ["issue", "comment", "371", "-R", "FreeMixer/plugin-hostd", "--body", "a comment"]


def stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class PublicWriteRoute(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-public-write-"))
        self.bin = self.tmp / "bin"
        self.reached = self.tmp / "reached-knives"
        self.as_record = self.tmp / "agent-secrets-record"
        self.calls = self.tmp / "gh-app-token-calls"
        stub(self.bin, "knives", f'printf "%s GH_TOKEN=%s APPROVAL=%s\\n" "$*" "${{GH_TOKEN:-}}" '
                                 f'"${{GH_SHIM_APPROVAL:-}}" >> {self.reached}; exit 0')
        stub(self.bin, "gh", "exit 0")  # a real-looking gh for real_gh(); it carries no shim marker
        stub(self.bin, "agent-secrets",
             '[ "$1" = identity ] && exit 0\n'
             f'for a in "$@"; do printf "%s\\n" "$a"; done >> {self.as_record}\n'
             '[ "${AS_RUN:-}" = 1 ] || exit 75\n'
             'while [ "$1" != -- ]; do shift; done; shift\n'
             f'GITHUB_PUBLIC_REPO_PAT={PLACEHOLDER} exec "$@"')
        self.env = {
            "PATH": os.pathsep.join([str(self.bin), "/usr/bin", "/bin"]),
            "HOME": str(self.tmp),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "TMPDIR": str(self.tmp),
            "DOTFILES_DIR": str(SHIMS.parent),
            "AGENT_SECRETS_URL": "https://b.example",
            "GH_READ_CACHE": "0",
            "GH_READ_CACHE_LOG": "0",
            # The public-text gate is a different control and refuses first in a scratch HOME.
            "PUBLIC_LEAK_CHECK": "/bin/true",
            # Marks this process as an agent session, as omp does.
            "PI_SESSION_FILE": str(self.tmp / "session.jsonl"),
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def source(self, answer: str) -> None:
        """Make gh-app-token answer `answer` to --source, and a read-only App token otherwise."""
        stub(self.bin, "gh-app-token",
             f'echo "$*" >> {self.calls}\n'
             f'case " $* " in *" --source "*) printf "%s\\n" "{answer}" ;; *) echo ghs_READ ;; esac')

    def run_gh(self, argv: list[str], **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(GH_SHIM), *argv], env={**self.env, **extra},
                              capture_output=True, text=True, cwd=self.tmp, timeout=60, check=False)

    def broker_argv(self) -> list[str]:
        return self.as_record.read_text().splitlines() if self.as_record.exists() else []

    def knives_calls(self) -> list[str]:
        return self.reached.read_text().splitlines() if self.reached.exists() else []

    def test_a_write_to_an_uncovered_owner_runs_under_the_broker(self):
        """The 2026-11-08 post: a comment on an owner with no App installation."""
        self.source("uncovered")
        result = self.run_gh(COMMENT)
        self.assertEqual(result.returncode, 75, result.stderr)
        argv = self.broker_argv()
        self.assertIn("GITHUB_PUBLIC_REPO_PAT", argv)
        self.assertEqual(argv[argv.index("--ttl") + 1], "12h")
        self.assertEqual(argv[argv.index("--wait") + 1], "30m")
        self.assertTrue(argv[argv.index("--reason") + 1].startswith(
            "gh issue comment on FreeMixer/plugin-hostd as sjawhar"))
        command = argv[argv.index("--") + 1:]
        self.assertEqual(command[:2], ["sh", "-c"])
        self.assertIn('GH_TOKEN="$GITHUB_PUBLIC_REPO_PAT" exec "$0" "$@"', command[2])
        self.assertEqual(command[3:], [str(GH_SHIM), *COMMENT])
        self.assertEqual(self.knives_calls(), [], "the write reached knives before the broker granted it")

    def test_the_granted_write_runs_once_on_the_placeholder(self):
        self.source("uncovered")
        result = self.run_gh(COMMENT, AS_RUN="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.knives_calls(), [f"gh -- {' '.join(COMMENT)} GH_TOKEN={PLACEHOLDER} APPROVAL="])
        self.assertIn("a secrets-broker placeholder", result.stderr)
        self.assertEqual(self.broker_argv().count("GITHUB_PUBLIC_REPO_PAT"), 1)

    def test_an_api_write_names_the_repository(self):
        self.source("uncovered")
        self.run_gh(["api", "-X", "POST", "repos/FreeMixer/plugin-hostd/issues/371/comments", "-f", "body=x"])
        argv = self.broker_argv()
        self.assertTrue(argv[argv.index("--reason") + 1].startswith("gh api write on FreeMixer/plugin-hostd as sjawhar"))

    def test_a_host_shell_write_routes_the_same_way(self):
        """No person-or-agent check: a host shell on a gh-app.host-profile machine routes too."""
        config = self.tmp / "config" / "git" / "config"
        config.parent.mkdir(parents=True)
        config.write_text("[gh-app]\n\thost-profile = agent\n")
        self.source("uncovered")
        self.run_gh(COMMENT, PI_SESSION_FILE="")
        self.assertIn("GITHUB_PUBLIC_REPO_PAT", self.broker_argv())
        self.assertEqual(self.knives_calls(), [])

    # --- what is not routed ----------------------------------------------

    def test_a_read_of_the_same_owner_is_not_routed(self):
        self.source("uncovered")
        self.run_gh(["issue", "view", "371", "-R", "FreeMixer/plugin-hostd"])
        self.assertEqual(self.broker_argv(), [])
        self.assertEqual(len(self.knives_calls()), 1, "a read must go to knives as before")

    def test_a_write_to_a_covered_owner_is_not_routed(self):
        self.source("app")
        self.run_gh(["issue", "comment", "1", "-R", "theorem-labs/agent-infra", "--body", "x"])
        self.assertEqual(self.broker_argv(), [])
        self.assertEqual(len(self.knives_calls()), 1)

    def test_a_write_that_carries_its_own_token_is_not_routed(self):
        self.source("uncovered")
        self.run_gh(COMMENT, GH_TOKEN="ghs_FROM_A_BROKER_GRANT")
        self.assertEqual(self.broker_argv(), [])
        self.assertFalse(self.calls.exists(), "a call with its own GH_TOKEN asked gh-app-token anyway")


if __name__ == "__main__":
    unittest.main()
