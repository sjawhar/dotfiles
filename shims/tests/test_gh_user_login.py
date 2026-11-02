#!/usr/bin/env python3
"""No gh an agent process runs may act as the user's own GitHub login, and a call the App may not
make ends with a refusal that names the call, the identity and the route.

On 2026-11-02T00:07Z an omp Python eval cell ran `gh pr review 1178 -R theorem-labs/agent-infra
--approve`; mise's python3 shim had put mise's own gh ahead of shims/gh on the kernel's PATH, gh
read the user's login from ~/.config/gh, and the approval went out as the user.

GitHub here is a stub served on a unix socket, which gh reaches through its own `http_unix_socket`
config key with plain HTTP, so each request's credential is visible to the test. The user's login
is a fake token in a scratch HOME, and the session bus points nowhere, so no real keyring or token
is read. The gh is the real binary (mise's), found past every shim on PATH; knives is a stand-in
that hands gh the App's token, as `knives gh` does for a routed call.
"""

import json
import os
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path

SHIMS = Path(__file__).parents[1]
DOTFILES = SHIMS.parent
GH_SHIM = SHIMS / "gh"
AGENT_CONFIG = DOTFILES / "scripts" / "gh-agent-config"
AGENT_SESSION = DOTFILES / "scripts" / "agent-session"
USER_TOKEN = "gho_FAKEUSERTOKEN0000000000000000000000"
APP_TOKEN = "ghs_FAKEAPPTOKEN00000000000000000000000"
PAT_TOKEN = "ghp_FAKEPUBLICREPOPAT000000000000000000"
# Seconds after "now" at which the stub's spent core budget resets.
RESET_IN = 600
HEAD = "556750abe84e20ed2e431d7dcf4b029df4d7bd9a"
REVIEW = ["pr", "review", "1178", "-R", "o/r", "--approve", "--body", "VERDICT: APPROVE"]
COMMENT = ["pr", "comment", "1178", "-R", "o/r", "--body", "VERDICT: APPROVE"]


def real_gh() -> Path | None:
    """The first gh on PATH, or in mise's install dirs, that is not a shim (shims/gh's own test)."""
    dirs = os.environ.get("PATH", "").split(os.pathsep)
    for root in (Path.home() / ".mise" / "installs" / "gh", Path.home() / ".local/share/mise/installs/gh"):
        dirs += [str(p.parent) for p in sorted(root.glob("*/*/bin/gh"))]
    for d in dirs:
        candidate = Path(d) / "gh"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            if b"knives-gh-shim" not in candidate.read_bytes()[:512]:
                return candidate
    return None


class Handler(BaseHTTPRequestHandler):
    log_path: Path

    def log_message(self, *args):
        pass

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        auth = self.headers.get("Authorization", "")
        token = auth.split(" ", 1)[1] if " " in auth else auth
        try:
            query = json.loads(raw).get("query", "") if raw else ""
        except ValueError:
            query = ""
        with open(self.log_path, "a") as log:
            log.write(json.dumps({"path": self.path, "token": token,
                                  "mutation": query.lstrip().startswith("mutation")}) + "\n")
        if self.path.split("?")[0].endswith("/rate_limit"):
            reset = int(time.time()) + RESET_IN
            return self._reply(200, {"resources": {
                "core": {"limit": 5000, "used": 5000, "remaining": 0, "reset": reset},
                "graphql": {"limit": 5000, "used": 10, "remaining": 4990, "reset": reset + 1800}}})
        if "ratelimited" in self.path:
            return self._reply(403, {"message": "API rate limit exceeded for user ID 1. If you reach out to GitHub "
                                     "Support for help, please include the request ID.",
                                     "documentation_url": "https://docs.github.com/rest/overview/rate-limits"})
        login = {USER_TOKEN: "fake-user", APP_TOKEN: "fake-app[bot]"}.get(token)
        if login is None:
            return self._reply(401, {"message": "Bad credentials", "status": "401"})
        if "addPullRequestReview" in query:
            if login == "fake-app[bot]":
                return self._reply(200, {"data": {"addPullRequestReview": None}, "errors": [
                    {"type": "UNPROCESSABLE", "message": "Can not approve your own pull request"}]})
            return self._reply(200, {"data": {"addPullRequestReview": {"clientMutationId": ""}}})
        if "addComment" in query:
            return self._reply(200, {"data": {"addComment": {"commentEdge": {"node": {
                "url": "https://github.com/o/r/pull/1178#issuecomment-1"}}}}})
        pr = {"id": "PR_1", "number": 1178, "title": "t", "state": "OPEN", "headRefOid": HEAD,
              "url": "https://github.com/o/r/pull/1178", "headRefName": "b", "baseRefName": "main",
              "isDraft": False, "isCrossRepository": False, "author": {"login": "fake-app[bot]"},
              "headRepositoryOwner": {"login": "o"}, "headRepository": {"name": "r"}}
        return self._reply(200, {"data": {"repository": {"pullRequest": pr, "hasIssuesEnabled": True}}})

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _handle


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("stub", 0)


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


@unittest.skipIf(real_gh() is None, "no real gh binary on this machine")
class UserLogin(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-user-login-"))
        self.home = self.tmp / "home"
        user_cfg = self.home / ".config" / "gh"
        user_cfg.mkdir(parents=True)
        self.sock = self.tmp / "github.sock"
        (user_cfg / "config.yml").write_text(f'version: "1"\ngit_protocol: https\nhttp_unix_socket: {self.sock}\n')
        (user_cfg / "hosts.yml").write_text(
            f"github.com:\n    users:\n        fake-user:\n            oauth_token: {USER_TOKEN}\n"
            f"    git_protocol: https\n    user: fake-user\n    oauth_token: {USER_TOKEN}\n")
        self.log = self.tmp / "requests.jsonl"
        self.log.touch()
        handler = type("H", (Handler,), {"log_path": self.log})
        self.server = Server(str(self.sock), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.gh = real_gh()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        # knives gh, routed: the App's token in GH_TOKEN, then the real gh.
        executable(self.bin / "knives",
                   '[ "$1" = gh ] && shift; [ "$1" = -- ] && shift\n'
                   f'[ -n "${{KNIVES_UNROUTED:-}}" ] || export GH_TOKEN={APP_TOKEN}\n'
                   'exec "$KNIVES_REAL_GH" "$@"')
        self.env = {
            "HOME": str(self.home), "USER": os.environ.get("USER", "u"), "LANG": "C.UTF-8",
            "XDG_RUNTIME_DIR": str(self.tmp), "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.tmp}/no-bus",
            "TMPDIR": str(self.tmp), "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
            "GH_READ_CACHE": "0", "GH_READ_CACHE_LOG": "0", "PUBLIC_LEAK_CHECK": "/bin/true",
            "DOTFILES_DIR": str(DOTFILES),
        }
        self.agent = {"PI_SESSION_FILE": str(self.tmp / "session.jsonl")}
        # The 00:07Z eval kernel's order: mise's gh first, shims/ after it.
        self.kernel_path = os.pathsep.join([str(self.gh.parent), str(SHIMS), str(self.bin), "/usr/bin", "/bin"])
        self.shim_path = os.pathsep.join([str(SHIMS), str(self.gh.parent), str(self.bin), "/usr/bin", "/bin"])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp)

    def run_gh(self, argv, path, **extra):
        before = len(self.log.read_text().splitlines())
        result = subprocess.run(["gh", *argv], env={**self.env, "PATH": path, **extra},
                                capture_output=True, text=True, cwd=self.tmp, timeout=60, check=False)
        sent = [json.loads(line) for line in self.log.read_text().splitlines()[before:]]
        return result, sent

    def agent_config_dir(self) -> str:
        return subprocess.run([str(AGENT_CONFIG)], env={**self.env, "PATH": "/usr/bin:/bin"},
                              capture_output=True, text=True, check=True).stdout.strip()

    def assert_never_the_user(self, sent):
        self.assertTrue(sent, "gh sent no request at all, so the test proves nothing")
        self.assertNotIn(USER_TOKEN, [r["token"] for r in sent])

    def test_control_the_kernel_path_without_the_agent_config_acts_as_the_user(self):
        # The incident, reproduced: a review and a comment alike go out on the user's login.
        for argv in (REVIEW, COMMENT):
            result, sent = self.run_gh(argv, self.kernel_path, **self.agent)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({r["token"] for r in sent}, {USER_TOKEN})
            self.assertEqual(sum(r["mutation"] for r in sent), 1)

    def test_a_gh_past_the_shim_with_the_agent_config_never_sends_the_user_token(self):
        agent_cfg = self.agent_config_dir()
        for argv in (REVIEW, COMMENT):
            result, sent = self.run_gh(argv, self.kernel_path, GH_CONFIG_DIR=agent_cfg, **self.agent)
            self.assertNotEqual(result.returncode, 0)
            self.assert_never_the_user(sent)
            self.assertFalse(any(r["mutation"] for r in sent))

    def test_the_agent_config_hides_the_keyring_from_gh_auth_too(self):
        # Without a hosts.yml, gh falls back to the keyring's unkeyed entry here.
        env = {**self.env, "PATH": self.kernel_path, "GH_CONFIG_DIR": self.agent_config_dir()}
        token = subprocess.run([str(self.gh), "auth", "token"], env=env, capture_output=True, text=True, check=False)
        self.assertNotIn("gho_", token.stdout)
        self.assertIn("placeholder-not-a-token", token.stdout)
        cred = subprocess.run([str(self.gh), "auth", "git-credential", "get"], env=env, capture_output=True,
                              text=True, input="protocol=https\nhost=github.com\n\n", check=False)
        self.assertNotIn(USER_TOKEN, cred.stdout)

    def test_the_agent_config_is_rewritten_when_changed_and_keeps_the_user_config_link(self):
        agent_cfg = Path(self.agent_config_dir())
        (agent_cfg / "hosts.yml").write_text(f"github.com:\n    user: fake-user\n    oauth_token: {USER_TOKEN}\n")
        self.assertEqual(Path(self.agent_config_dir()), agent_cfg)
        self.assertNotIn(USER_TOKEN, (agent_cfg / "hosts.yml").read_text())
        self.assertEqual(oct((agent_cfg / "hosts.yml").stat().st_mode & 0o777), "0o600")
        self.assertEqual(os.readlink(agent_cfg / "config.yml"), str(self.home / ".config" / "gh" / "config.yml"))

    def test_the_shim_routes_a_comment_to_the_app(self):
        result, sent = self.run_gh(COMMENT, self.shim_path, **self.agent)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({r["token"] for r in sent}, {APP_TOKEN})
        self.assertNotIn("REFUSED", result.stderr)

    def test_a_review_github_refuses_for_the_app_ends_with_the_route(self):
        result, sent = self.run_gh(REVIEW, self.shim_path, **self.agent)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual({r["token"] for r in sent}, {APP_TOKEN})
        err = result.stderr
        self.assertIn("Can not approve your own pull request", err)  # GitHub's own error stays
        self.assertIn("gh shim: REFUSED by GitHub: `gh pr review 1178 -R o/r --approve --body VERDICT:\\ APPROVE`", err)
        self.assertIn("the user login `fake-user` from gh's hosts.yml and keyring is never used by an agent", err)
        self.assertIn("`gh pr comment 1178 -R o/r --body-file <file>`", err)
        self.assertIn("`VERDICT: APPROVE at <full 40-character head sha>`", err)
        # agent-infra main needs an approving review, which a comment is not.
        self.assertIn(
            "`~/.dotfiles/scripts/post-review 1178 <full 40-character head sha> APPROVE <body-file> o/r`", err
        )
        self.assertIn("only when your session did not author the pull request", err)

    def test_an_api_review_refused_for_the_app_gets_the_same_route(self):
        argv = ["api", "-X", "POST", "repos/o/r/pulls/7/reviews", "-f", "event=REQUEST_CHANGES"]
        executable(self.bin / "knives", 'shift 2; echo "gh: Resource not accessible by integration (HTTP 403)" >&2; exit 1')
        result, _ = self.run_gh(argv, self.shim_path, **self.agent)
        self.assertEqual(result.returncode, 1)
        self.assertIn("`gh pr comment 7 -R o/r --body-file <file>`", result.stderr)
        self.assertIn("`VERDICT: REQUEST_CHANGES at", result.stderr)
        self.assertIn("post-review 7 <full 40-character head sha> REQUEST_CHANGES <body-file> o/r`", result.stderr)

    def test_an_unrouted_call_reaches_github_without_the_user_and_says_to_stop(self):
        result, sent = self.run_gh(REVIEW, self.shim_path, KNIVES_UNROUTED="1", **self.agent)
        self.assertNotEqual(result.returncode, 0)
        self.assert_never_the_user(sent)
        self.assertIn("gh shim: REFUSED by GitHub:", result.stderr)

    def test_any_other_call_github_refuses_says_to_stop_and_tell_the_lead(self):
        executable(self.bin / "knives", 'echo "HTTP 403: Must have admin rights to Repository." >&2; exit 1')
        result, _ = self.run_gh(["pr", "merge", "5", "-R", "o/r", "--admin"], self.shim_path, **self.agent)
        self.assertEqual(result.returncode, 1)
        self.assertIn("`gh pr merge 5 -R o/r --admin`", result.stderr)
        self.assertIn("Instead: stop, and tell your lead which call GitHub refused", result.stderr)

    def rate_limited(self, knives_body):
        executable(self.bin / "knives", '[ "$1" = gh ] && shift; [ "$1" = -- ] && shift\n' + knives_body +
                   '\nexec "$KNIVES_REAL_GH" "$@"')
        argv = ["api", "repos/o/ratelimited/pulls"]
        result, sent = self.run_gh(argv, self.shim_path, **self.agent)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("API rate limit exceeded", result.stderr)  # GitHub's own error stays
        self.assertIn("gh shim: RATE LIMITED: `gh api repos/o/ratelimited/pulls`", result.stderr)
        self.assertNotIn("REFUSED", result.stderr)
        self.assertNotIn("tell your lead", result.stderr)
        self.assertIn("use one GraphQL query", result.stderr)
        # The reset comes from the rate_limit endpoint, read on the call's own route.
        self.assertIn("/rate_limit", [r["path"].split("?")[0] for r in sent])
        want = time.strftime("%H:%M", time.gmtime(time.time() + RESET_IN))
        self.assertRegex(result.stderr, r"Limit: core 0 of 5000 left; it resets at \d\d:\d\d:\d\dZ \(in 1[01] min\)")
        self.assertIn(f"resets at {want}", result.stderr)
        self.assertNotIn("graphql 4990", result.stderr)  # a budget with requests left is not the cause
        return result.stderr

    def test_a_spent_public_repo_token_says_rate_limit_and_reset_not_refusal(self):
        err = self.rate_limited(
            'echo "gh-app-token: minting token for profile agent (owner=o) through the broker..." >&2\n'
            'echo "gh-app-token: no App installation for owner o; using the personal token GH_PUBLIC_REPO_PAT'
            ' - this call will act as its user, not as the App" >&2\n'
            f'export GH_TOKEN={PAT_TOKEN}')
        self.assertIn("the shared public-repo token GH_PUBLIC_REPO_PAT (owner o has no App installation)", err)

    def test_a_spent_app_installation_says_rate_limit_too(self):
        err = self.rate_limited(
            'echo "gh-app-token: minting token for profile agent (owner=o) through the broker..." >&2\n'
            f'export GH_TOKEN={APP_TOKEN}')
        self.assertIn("the GitHub App installation for owner o, which every agent session shares", err)

    def test_a_call_knives_could_not_route_says_to_name_a_repository_not_to_stop(self):
        # 2026-11-02: `gh api markdown` outside a checkout; knives names no target and gh's 401 follows.
        executable(self.bin / "knives",
                   'echo "knives gh: no target repository (no -R/--repo flag, ...), or set GH_REPO=<owner>/<repo>." >&2\n'
                   'echo "gh: Bad credentials (HTTP 401)" >&2; exit 1')
        result, _ = self.run_gh(["api", "markdown", "--input", "-"], self.shim_path, **self.agent)
        self.assertEqual(result.returncode, 1)
        self.assertIn("gh shim: NOT ROUTED: `gh api markdown --input -`", result.stderr)
        self.assertIn("GH_REPO=<owner>/<repo>", result.stderr)
        self.assertNotIn("REFUSED", result.stderr)
        self.assertNotIn("tell your lead", result.stderr)

    def test_a_failure_that_is_not_a_refusal_adds_nothing(self):
        executable(self.bin / "knives", 'echo "GraphQL: Could not resolve to a PullRequest" >&2; exit 1')
        result, _ = self.run_gh(["pr", "view", "5", "-R", "o/r"], self.shim_path, **self.agent)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("REFUSED", result.stderr)

    def test_the_shim_refuses_gh_auth_token_naming_the_identity(self):
        result, sent = self.run_gh(["auth", "token"], self.shim_path, **self.agent)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(sent, [])
        self.assertIn("gh shim: REFUSED: `gh auth token`", result.stderr)
        self.assertIn("it would have used the user login `fake-user` from gh's hosts.yml and keyring", result.stderr)
        self.assertIn("Instead: ", result.stderr)

    def test_agent_session_exports_the_agent_config_and_keeps_a_chosen_one(self):
        dotfiles = self.tmp / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        shutil.copy(AGENT_CONFIG, dotfiles / "scripts" / "gh-agent-config")
        shutil.copy(DOTFILES / "scripts" / "agent-spend-env.sh", dotfiles / "scripts" / "agent-spend-env.sh")
        executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        executable(self.bin / "print-env", 'printf "%s\\n" "$GH_CONFIG_DIR"')
        env = {**self.env, "DOTFILES_DIR": str(dotfiles), "PATH": f"{self.bin}:/usr/bin:/bin",
               "XDG_CONFIG_HOME": str(self.home / ".config")}
        for given, want in ((None, str(self.home / ".config" / "gh-agent")),
                            (str(self.home / ".config" / "gh"), str(self.home / ".config" / "gh-agent")),
                            ("/legion/state/gh", "/legion/state/gh")):
            run_env = dict(env) if given is None else {**env, "GH_CONFIG_DIR": given}
            out = subprocess.run([str(AGENT_SESSION), str(self.bin / "print-env")], env=run_env,
                                 capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(out, want, f"GH_CONFIG_DIR given {given!r}")


@unittest.skipIf(shutil.which("mise") is None and not (DOTFILES / "bin" / "mise").exists()
                 and not (Path.home() / ".dotfiles" / "bin" / "mise").exists(), "no mise")
class MisePath(unittest.TestCase):
    def test_every_path_mise_builds_starts_with_shims(self):
        # A mise shim (omp's python3 for the eval kernel) runs its tool with this PATH.
        mise = shutil.which("mise") or str(next(p for p in (DOTFILES / "bin" / "mise",
                                                            Path.home() / ".dotfiles" / "bin" / "mise") if p.exists()))
        env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "MISE_GLOBAL_CONFIG_FILE": str(DOTFILES / "mise.toml"),
               "DOTFILES_DIR": str(DOTFILES), "MISE_DATA_DIR": os.environ.get("MISE_DATA_DIR", str(Path.home() / ".mise"))}
        out = subprocess.run([mise, "env", "--json"], env=env, capture_output=True, text=True, cwd="/", check=True)
        self.assertEqual(json.loads(out.stdout)["PATH"].split(os.pathsep)[0], str(SHIMS))


if __name__ == "__main__":
    unittest.main()
