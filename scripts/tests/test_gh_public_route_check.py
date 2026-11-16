#!/usr/bin/env python3
"""scripts/gh-public-route-check reports its three INFRA-2031 probes as pass or fail.

The check is a live one; here every command it calls is a stub on PATH, and the real secret-run of
this checkout stands between it and a stub agent-secrets, so the test is about the check's own
logic: it reports PASS only when an uncovered owner's read is not a personal token (a), a push to it
waits at the broker on a named request (b), and the token reaches GitHub only through the airlock
(c). Each probe also has a control: the stub set that makes exactly that probe fail. No probe prints
a token, and a request left waiting is cancelled.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
DOTFILES = SCRIPTS.parent
CHECK = SCRIPTS / "gh-public-route-check"
PLACEHOLDER = "agent-secrets-placeholder-0123456789abcdefghijklmnopqrstuv"
WAITING = "agent-secrets: request req-42 to broker https://b.example is still waiting for approval; nothing was run. Check it with: agent-secrets status req-42"


def stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# agent-secrets: `identity` exits $IDENTITY (0 unless set), `machine login-status` prints issued,
# `register … --exec -- CMD` logs and runs CMD as a registered session, `cancel ID` logs; the run form
# logs its names and --ttl, then $GRANT decides: `pending` prints the waiting line and exits 75,
# `value` runs the command with the bare value and no proxy, anything else runs it with the
# placeholder and a proxy.
AGENT_SECRETS = r"""
log() { printf '%s\n' "$*" >> "$AS_LOG"; }
case "$1" in
  identity) exit "${IDENTITY:-0}" ;;
  machine) echo issued; exit 0 ;;
  register) log "REGISTER $*"; while [ "$1" != -- ]; do shift; done; shift; IDENTITY=0 exec "$@" ;;
  cancel) log "CANCEL $2"; exit 0 ;;
esac
names=() ttl=""
while [ $# -gt 0 ] && [ "$1" != -- ]; do
  case "$1" in --ttl) ttl="$2"; shift 2 ;; --reason|--wait) shift 2 ;; *) names+=("$1"); shift ;; esac
done
shift
log "RUN ${names[*]} ttl=$ttl"
case "${GRANT:-placeholder}" in
  pending) echo "$WAITING" >&2; exit 75 ;;
  value) GITHUB_PUBLIC_REPO_PAT=ghp_the_real_value exec "$@" ;;
  *) GITHUB_PUBLIC_REPO_PAT="$PLACEHOLDER" HTTPS_PROXY=http://127.0.0.1:9 exec "$@" ;;
esac
"""

# git: init and the plumbing answer; a push runs the route a shim would take: $PUSH `route` asks the
# broker as shims/git does (agent-secrets through secret-run), `direct` fails as a push to GitHub does.
GIT = r"""
while [ "$1" = -C ]; do shift 2; done
case "$1" in
  init) exit 0 ;;
  hash-object) echo 4b825dc642cb6eb9a060e54bf8d69288fbee4904 ;;
  commit-tree) echo 1111111111111111111111111111111111111111 ;;
  push)
    if [ "${PUSH:-route}" = route ]; then
      exec "$SECRET_RUN" --register --reason "git push" --ttl 12h --wait "${AGENT_SECRETS_WAIT:-30m}" GITHUB_PUBLIC_REPO_PAT -- true
    fi
    echo "remote: Repository not found." >&2; exit 128 ;;
esac
"""

# Fake tokens of the two kinds probe (a) tells apart, built here so no line of this file reads as
# a credential to the public leak check. The stub gh-app-token prints $STUB_PRINTS.
READ_TOKEN = "gh" + "s_" + "readowner" + "0" * 27
PERSONAL_TOKEN = "gh" + "p_" + "personal" + "0" * 28

GOOD = {
    "gh-app-token": 'printf "%s\\n" "$STUB_PRINTS"',
    "git": GIT,
    "agent-secrets": AGENT_SECRETS,
    "curl": 'case "$*" in *"token $PLACEHOLDER"*) ;; *ghp_the_real_value*) printf 200; exit 0 ;; *) printf 401; exit 0 ;; esac\n'
            'if [ -n "${HTTPS_PROXY:-}${https_proxy:-}" ]; then printf 200; else printf 401; fi',
}


class GhPublicRouteCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-public-route-check-"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.as_log = self.tmp / "agent-secrets.log"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_check(self, overrides=None, args=(), **env):
        for name, body in {**GOOD, **(overrides or {})}.items():
            stub(self.bin, name, body)
        return subprocess.run(
            [str(CHECK), *args],
            env={
                "PATH": os.pathsep.join([str(self.bin), "/usr/bin", "/bin"]),
                "HOME": str(self.tmp), "TMPDIR": str(self.tmp), "LANG": "C.UTF-8",
                "DOTFILES_DIR": str(DOTFILES), "AGENT_SECRETS_URL": "https://b.example",
                "SECRET_RUN": str(SCRIPTS / "secret-run"), "AS_LOG": str(self.as_log),
                "PLACEHOLDER": PLACEHOLDER, "WAITING": WAITING, "STUB_PRINTS": READ_TOKEN, **env,
            },
            capture_output=True, text=True, timeout=120, check=False,
        )

    def log(self):
        return self.as_log.read_text().splitlines() if self.as_log.exists() else []

    def assert_no_token(self, result):
        for text in (result.stdout, result.stderr):
            self.assertNotIn(READ_TOKEN, text)
            self.assertNotIn(PLACEHOLDER, text)
            self.assertNotIn("ghp_the_real_value", text)

    def test_every_probe_passes_on_the_broker_route(self):
        # (b) leaves request req-42 waiting; (c), granted, runs inside the grant.
        result = self.run_check(GRANT="pending", args=("--probe", "a", "--probe", "b"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"PASS a: .*\(ghs_…\)")
        self.assertRegex(result.stdout, r"PASS b: .*request req-42 \(exit 75\)")
        self.assertIn("VERDICT: pass", result.stdout)
        self.assert_no_token(result)
        result = self.run_check(args=("--probe", "c"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS c: the token reaches GitHub only through the airlock", result.stdout)
        self.assert_no_token(result)

    def test_a_waiting_request_is_joined_by_c_and_cancelled_at_the_end(self):
        result = self.run_check(GRANT="pending", args=("--wait", "0s"))
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stdout, r"PASS b:")
        self.assertRegex(result.stdout, r"FAIL c: request req-42 still waited for approval after --wait 0s")
        self.assertIn("NOTE: cancelled broker request req-42", result.stdout)
        # (b) and (c) ask with one --ttl, so the broker joins them into one request (one card).
        self.assertEqual([line for line in self.log() if line.startswith("RUN")],
                         ["RUN GITHUB_PUBLIC_REPO_PAT ttl=12h", "RUN GITHUB_PUBLIC_REPO_PAT ttl=12h"])
        self.assertEqual(self.log()[-1], "CANCEL req-42")

    def test_no_broker_identity_runs_the_check_as_one_registered_session(self):
        unit = self.tmp / ".config" / "systemd" / "user" / "agent-secrets-helper.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("")
        result = self.run_check(GRANT="pending", args=("--wait", "0s"), IDENTITY="1")
        self.assertIn("PASS b:", result.stdout, result.stderr)
        registered = [line for line in self.log() if line.startswith("REGISTER")]
        self.assertEqual(len(registered), 1, self.log())
        self.assertIn("gh-public-route-check --wait 0s", registered[0])
        self.assertEqual(self.log()[-1], "CANCEL req-42")

    # The controls: each makes exactly one probe fail.

    def test_a_fails_when_a_personal_token_is_printed(self):
        result = self.run_check(args=("--probe", "a"), STUB_PRINTS=PERSONAL_TOKEN)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stdout, r"FAIL a: gh-app-token printed a personal token \(ghp_…\)")
        self.assertNotIn(PERSONAL_TOKEN, result.stdout + result.stderr)

    def test_a_fails_when_nothing_is_printed(self):
        result = self.run_check({"gh-app-token": "exit 1"}, args=("--probe", "a"))
        self.assertRegex(result.stdout, r"FAIL a: gh-app-token printed nothing")

    def test_b_fails_when_the_push_goes_straight_to_github(self):
        result = self.run_check(args=("--probe", "b"), PUSH="direct")
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stdout, r"FAIL b: a push to reearth exited 128, .*Repository not found")
        self.assertNotIn("CANCEL", "\n".join(self.log()))

    def test_b_fails_on_75_that_names_no_request(self):
        result = self.run_check({"git": GIT.replace("push)", "push) exit 75 ;;\n  never)")}, args=("--probe", "b"))
        self.assertRegex(result.stdout, r"FAIL b: a push to reearth exited 75, not 75 naming a broker request")

    def test_c_fails_when_the_command_holds_the_value_itself(self):
        # No airlock entry: the grant hands over the value, so the direct call works too.
        result = self.run_check(args=("--probe", "c"), GRANT="value")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL c: through the proxy 200 (200 wanted), without it 200 (401 wanted)", result.stdout)
        self.assert_no_token(result)


if __name__ == "__main__":
    unittest.main()
