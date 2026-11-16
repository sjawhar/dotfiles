#!/usr/bin/env python3
"""A `git push` to an owner the GitHub App does not cover runs under the broker (INFRA-2031).

shims/git's public_push_gate re-execs such a push as
`secret-run --register … GITHUB_PUBLIC_REPO_PAT -- <this shim> -c <one-shot credential helper> push …`,
so the only credential in the push is the airlock's placeholder. These tests drive the real
shims/git and the real scripts/secret-run of this checkout with a fake real git (it answers the reads
the shim makes and records each push with its argv and environment), a stub gh-app-token whose
--source says whether the owner is covered, and a stub agent-secrets that records its argv and,
asked to, runs the command with a placeholder. Nothing contacts GitHub. The credential test runs the
real git with the exact `-c` arguments the shim passed, against the gh-app routes include and a
global helper, so it fails if those arguments stop winning.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).resolve().parents[1]
DOTFILES = SHIMS.parent
GIT_SHIM = SHIMS / "git"
PLACEHOLDER = "agent-secrets-placeholder-0123456789abcdefghijklmnopqrstuv"
UNCOVERED = "https://github.com/reearth/ygo.git"

FAKE_GIT = r"""#!/bin/bash
full="$*"
while [ $# -gt 0 ]; do
  case "$1" in
    -C|-c|--git-dir|--work-tree|--namespace|--config-env|--exec-path) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
cmd="${1:-}"; [ $# -gt 0 ] && shift
case "$cmd" in
  remote)
    case "${1:-}" in
      get-url) printf '%s\n' "$REMOTE_URL" ;;
      -v) printf 'origin\t%s (fetch)\norigin\t%s (push)\n' "$REMOTE_URL" "$REMOTE_URL" ;;
      "") echo origin ;;
    esac ;;
  ls-remote) [ "${1:-}" = --get-url ] && printf '%s\n' "${!#}" ;;
  config) exit 1 ;;
  push)
    { printf 'PUSH'; for a in "$@"; do printf ' [%s]' "$a"; done; printf '\n'
      printf 'ARGV %s\n' "$full"
      printf 'ENV routed=%s jj=%s pat=%s\n' "${GIT_SHIM_PUBLIC_ROUTED-unset}" "${GIT_SHIM_JJ_PUSH-unset}" "${GITHUB_PUBLIC_REPO_PAT-unset}"
    } >> "$GIT_RECORD"
    exit 0 ;;
esac
exit 0
"""

# Records its argv (one per line) and the route marker it was given, then, with AS_RUN=1, runs the
# command after `--` with the placeholder, as a grant does; without it, exits 75 as a pending one.
AGENT_SECRETS = r"""#!/bin/bash
[ "$1" = identity ] && exit 0
{ printf 'ROUTED %s\n' "${GIT_SHIM_PUBLIC_ROUTED-unset}"; for a in "$@"; do printf '%s\n' "$a"; done; } >> "$AS_RECORD"
[ "${AS_RUN:-}" = 1 ] || exit 75
while [ "$1" != -- ]; do shift; done
shift
GITHUB_PUBLIC_REPO_PAT="$PLACEHOLDER" exec "$@"
"""


def executable(path: Path, body: str) -> None:
    path.write_text(body if body.startswith("#!") else "#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class PublicPushRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="git-public-push-"))
        self.bin = self.tmp / "bin"          # stubs ahead of the shim
        self.realdir = self.tmp / "real"     # the fake real git, past the shim
        self.bin.mkdir()
        self.realdir.mkdir()
        self.git_record = self.tmp / "git-record"
        self.as_record = self.tmp / "agent-secrets-record"
        executable(self.realdir / "git", FAKE_GIT)
        executable(self.bin / "agent-secrets", AGENT_SECRETS)
        executable(self.bin / "gh-app-token", 'printf "%s\\n" "${SRC:-uncovered}"')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def env(self, **extra):
        return {
            "PATH": os.pathsep.join([str(self.bin), str(SHIMS), str(self.realdir), "/usr/bin", "/bin"]),
            "HOME": str(self.tmp), "TMPDIR": str(self.tmp), "LANG": "C.UTF-8", "USER": "nobodyx",
            "DOTFILES_DIR": str(DOTFILES), "AGENT_SECRETS_URL": "https://b.example",
            "PI_SESSION_FILE": str(self.tmp / "session.jsonl"), "REMOTE_URL": UNCOVERED,
            "GIT_RECORD": str(self.git_record), "AS_RECORD": str(self.as_record),
            "PLACEHOLDER": PLACEHOLDER, "GIT_TERMINAL_PROMPT": "0", **extra,
        }

    def push(self, *argv, runner=(), **extra):
        argv = argv or ("origin", "HEAD:refs/heads/feature-x")
        return subprocess.run([*runner, str(GIT_SHIM), "push", *argv], env=self.env(**extra),
                              capture_output=True, text=True, timeout=60, cwd=self.tmp, check=False)

    def broker_argv(self):
        return self.as_record.read_text().splitlines() if self.as_record.exists() else []

    def git_pushes(self):
        return self.git_record.read_text().splitlines() if self.git_record.exists() else []

    def command_after(self, argv):
        return argv[argv.index("--") + 1:]

    def test_an_uncovered_owner_push_runs_under_the_broker(self):
        result = self.push()
        self.assertEqual(result.returncode, 75, result.stderr)
        argv = self.broker_argv()
        self.assertEqual(argv[0], "ROUTED git")
        self.assertIn("GITHUB_PUBLIC_REPO_PAT", argv)
        self.assertEqual(argv[argv.index("--ttl") + 1], "12h")
        self.assertEqual(argv[argv.index("--wait") + 1], "30m")
        self.assertTrue(argv[argv.index("--reason") + 1].startswith("git push to reearth/ygo as sjawhar"))
        command = self.command_after(argv)
        self.assertEqual(command[0], str(GIT_SHIM))  # through this shim again, so its gates run
        self.assertEqual(command[1:3], ["-c", "credential.https://github.com.helper="])
        self.assertEqual(command[3], "-c")
        self.assertTrue(command[4].startswith("credential.https://github.com.helper=!f(){"))
        self.assertEqual(command[5:], ["push", "origin", "HEAD:refs/heads/feature-x"])
        self.assertEqual(self.git_pushes(), [], "the push went to git before the broker granted it")

    def test_a_persons_push_routes_the_same_way(self):
        # No person-or-agent check: Sami's own shell holds no personal token either.
        env = {"PI_SESSION_FILE": ""}
        self.push(**env)
        self.assertIn("GITHUB_PUBLIC_REPO_PAT", self.broker_argv())
        self.assertEqual(self.git_pushes(), [])

    def test_the_wait_and_a_cited_approval_reach_the_broker(self):
        ask = "dispatch://INFRA-2031/ask/a7d1ac41-45c4-4504-a985-7c92998d2158"
        self.push(AGENT_SECRETS_WAIT="0s", GH_SHIM_APPROVAL=ask)
        argv = self.broker_argv()
        self.assertEqual(argv[argv.index("--wait") + 1], "0s")
        self.assertEqual(argv[argv.index("--ttl") + 1], "15m")
        # The whole ask reference survives secret-run-reason's 100-rune cut of the caller's reason.
        self.assertTrue(argv[argv.index("--reason") + 1].startswith(f"approved at {ask}: git push to reearth/"))

    def test_the_granted_push_runs_once_with_the_placeholder_and_no_route_marker(self):
        result = self.push(AS_RUN="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        [push, argv_line, env_line] = self.git_pushes()
        self.assertIn("[origin] [HEAD:refs/heads/feature-x]", push)
        self.assertEqual(env_line, f"ENV routed=unset jj=0 pat={PLACEHOLDER}")
        self.assertEqual(len([line for line in self.broker_argv() if line.startswith("ROUTED")]), 1)
        self.assertIn("credential.https://github.com.helper=!f(){", argv_line)

    def test_the_one_shot_helper_answers_before_every_other_helper(self):
        self.push()
        command = self.command_after(self.broker_argv())
        config = command[1:5]
        # Each helper prints its own marker as the password; printf builds the `password=` line, so
        # no line here reads as a credential to the public leak check.
        (self.tmp / ".gitconfig").write_text(
            '[credential "https://github.com"]\n\thelper = "!f(){ printf \'%s=%s\\\\n\' username g password FROM-GLOBAL; }; f"\n')
        executable(self.bin / "gh-app-token", "printf '%s=%s\\n' username r password FROM-ROUTES-HELPER")
        routes = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "include.path",
                  "GIT_CONFIG_VALUE_0": str(DOTFILES / "gh-app-routes.gitconfig")}
        request = "protocol=https\nhost=github.com\npath=reearth/ygo.git\n\n"

        def fill(*options, **extra):
            env = {**self.env(), "GIT_CONFIG_NOSYSTEM": "1", "GITHUB_PUBLIC_REPO_PAT": PLACEHOLDER, **extra}
            return subprocess.run(["/usr/bin/git", *options, "credential", "fill"], input=request, env=env,
                                  capture_output=True, text=True, timeout=30, cwd=self.tmp, check=False)

        # Controls: without the shim's -c, a person's global helper answers, and in an agent session
        # the routes include does; both are live, so the routed fills below can fail.
        self.assertIn("FROM-GLOBAL", fill().stdout)
        self.assertIn("FROM-ROUTES-HELPER", fill(**routes).stdout)
        for extra in ({}, routes):
            routed = fill(*config, **extra)
            self.assertEqual(routed.returncode, 0, routed.stderr)
            self.assertIn(f"password={PLACEHOLDER}", routed.stdout)
            self.assertIn("username=x-access-token", routed.stdout)
            self.assertNotIn("FROM-", routed.stdout)

    def test_a_covered_owner_push_is_not_routed(self):
        self.push(UNCOVERED, "HEAD:refs/heads/feature-x", SRC="app")
        self.assertEqual(self.broker_argv(), [])
        self.assertTrue(any(line.startswith("PUSH") for line in self.git_pushes()))

    def test_an_ssh_push_is_not_routed(self):
        # git authenticates ssh with a key and never asks a credential helper.
        self.push("git@github.com:reearth/ygo.git", "HEAD:refs/heads/feature-x")
        self.assertEqual(self.broker_argv(), [])
        self.assertTrue(any(line.startswith("PUSH") for line in self.git_pushes()))

    def test_the_inner_routed_push_does_not_route_again(self):
        self.push(UNCOVERED, "HEAD:refs/heads/feature-x", GIT_SHIM_PUBLIC_ROUTED="git")
        self.assertEqual(self.broker_argv(), [])
        self.assertIn("ENV routed=unset jj=0 pat=unset", self.git_pushes())

    def test_a_jj_push_routes_and_its_inner_push_stays_jjs(self):
        jj = self.tmp / "jj"
        shutil.copy("/bin/bash", jj)  # from_jj reads the parent's executable name
        runner = (str(jj), "-c", '"$0" "$@"; exit $?')
        self.push(UNCOVERED, "HEAD:refs/heads/feature-x", runner=runner)
        self.assertEqual(self.broker_argv()[0], "ROUTED jj")
        self.git_record.unlink(missing_ok=True)
        self.push(UNCOVERED, "HEAD:refs/heads/feature-x", GIT_SHIM_PUBLIC_ROUTED="jj")
        self.assertIn("ENV routed=unset jj=1 pat=unset", self.git_pushes())


if __name__ == "__main__":
    unittest.main()
