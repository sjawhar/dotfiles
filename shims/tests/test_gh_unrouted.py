#!/usr/bin/env python3
"""In an agent session gh runs only with the token knives routed: a call that needs GitHub and got none is not sent,
exits 4 with one line naming the fix, and leaves an `unrouted-refused` line in the shim's call log
(scripts/gh-routed-only). Whether stderr is kept or dropped changes nothing. On 2026-11-03 a lane's 18 GraphQL
mutations by node id, from a directory with no repository, each got the placeholder login's 401, and the lane, which
dropped stderr, never read why. A routed call, a token the caller set, a command gh answers without GitHub, and any
call outside an agent session run as before. knives and gh are stubs; nothing reaches GitHub."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
DOTFILES = SHIMS.parent
GH = SHIMS / "gh"
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
printf 'token=%s args=%s\\n' "${GH_TOKEN:-none}" "$*" >> "$GH_LOG"
echo '{"data": {}}'
"""


class Unrouted(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, text in (("knives", KNIVES), ("gh", REAL_GH), ("leak-check", "#!/bin/sh\nexit 0\n")):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        self.log = self.tmp / "gh.log"
        self.log.touch()
        self.cache = self.tmp / "cache"
        config = self.tmp / "config"
        (config / "gh-agent").mkdir(parents=True)
        (config / "gh-agent" / "hosts.yml").write_text("github.com:\n    oauth_token: placeholder\n")
        (config / "gh-agent" / "config.yml").write_text("")
        self.work = self.tmp / "work"  # no repository here
        self.work.mkdir()
        self.base = {
            "PATH": os.pathsep.join([str(SHIMS), str(self.bin), "/usr/bin", "/bin"]),
            "HOME": str(self.tmp / "home"),
            "USER": "no-such-user-gh-unrouted-test",
            "DOTFILES_DIR": str(DOTFILES),
            "XDG_CONFIG_HOME": str(config),
            "GH_READ_CACHE_DIR": str(self.cache),
            "PUBLIC_LEAK_CHECK": str(self.bin / "leak-check"),
            "GH_LOG": str(self.log),
            "STUB_APP_TOKEN": "stub-app-token",
            "TMPDIR": str(self.tmp),
        }
        self.agent = {**self.base, "OMP_SESSION_ID": "01a00000-0000-7000-8000-000000000000"}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_gh(self, args, env, stderr=subprocess.PIPE):
        return subprocess.run([str(GH), *args], env=env, cwd=self.work, stdout=subprocess.PIPE, stderr=stderr,
                              text=True, timeout=120, check=False)

    def gh_calls(self):
        # The read cache adds --include to a plain `gh api` GET; that is not what these tests are about.
        return [line.removesuffix(" --include") for line in self.log.read_text().splitlines()]

    def call_log(self):
        lines = []
        for path in sorted((self.cache / "log").glob("*.jsonl")):
            lines += [json.loads(line) for line in path.read_text().splitlines() if line]
        return lines

    MUTATION = ["api", "graphql", "-f", 'query=mutation { resolveReviewThread(input: {threadId: "PRRT_x"}) { thread { id } } }']

    def test_an_unrouted_call_is_refused_before_github_with_stderr_dropped(self):
        result = self.run_gh(self.MUTATION, self.agent, stderr=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(self.gh_calls(), [], "an unrouted call reached gh")
        events = [e["ev"] for e in self.call_log()]
        self.assertIn("unrouted-refused", events)

    def test_the_refusal_is_one_line_naming_the_fix(self):
        result = self.run_gh(["api", "user"], self.agent)
        self.assertEqual(result.returncode, 4, result.stderr)
        mine = [line for line in result.stderr.splitlines() if line.startswith("gh shim:")]
        self.assertEqual(len(mine), 1, result.stderr)
        self.assertIn("NOT ROUTED, so not sent", mine[0])
        self.assertNotIn("REFUSED", result.stderr)  # not a refusal of the App: no App was asked
        self.assertNotIn("tell your lead", result.stderr)
        self.assertIn("GH_REPO=<owner>/<repo>", mine[0])
        self.assertEqual(self.gh_calls(), [])

    def test_a_routed_call_uses_the_app_token_whatever_stderr_is(self):
        for stderr in (subprocess.DEVNULL, subprocess.PIPE):
            with self.subTest(stderr=stderr):
                self.log.write_text("")
                result = self.run_gh(self.MUTATION, {**self.agent, "GH_REPO": "o/r"}, stderr=stderr)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(self.gh_calls()), 1)
                self.assertTrue(self.gh_calls()[0].startswith("token=stub-app-token "), self.gh_calls())

    def test_a_command_gh_answers_without_github_still_runs(self):
        for args in (["--version"], ["help"], ["api", "--help"], ["extension", "list"], ["config", "get", "editor"]):
            with self.subTest(args=args):
                self.log.write_text("")
                result = self.run_gh(args, self.agent)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.gh_calls(), ["token=none args=" + " ".join(args)])

    def test_a_token_the_caller_set_runs_as_before(self):
        result = self.run_gh(["api", "user"], {**self.agent, "GH_TOKEN": "caller-token"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gh_calls(), ["token=caller-token args=api user"])

    def test_outside_an_agent_session_an_unrouted_call_reaches_gh(self):
        result = self.run_gh(["api", "user"], self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gh_calls(), ["token=none args=api user"])


if __name__ == "__main__":
    unittest.main()
