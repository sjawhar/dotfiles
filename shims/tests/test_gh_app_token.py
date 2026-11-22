#!/usr/bin/env python3
import importlib.util
import http.server
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).parents[1] / "gh-app-token"
SPEC = importlib.util.spec_from_loader("gh_app_token", SourceFileLoader("gh_app_token", str(MODULE_PATH)))
assert SPEC and SPEC.loader
gh_app_token = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gh_app_token)

GIT_CONFIG = {
    "gh-app.agent.app-id": "3202636",
    "gh-app.agent.broker-secret": "GITHUB_APP_SJAWHAR",
    "gh-app.agent.fallback-secret": "GH_PUBLIC_REPO_PAT",
}


def release(**installations):
    """A broker release of the github-app secret: owner -> (installation id, token)."""
    return json.dumps(
        {
            "installations": {
                owner: {"installation_id": installation_id, "token": token, "expires_at": "2099-01-01T00:00:00Z"}
                for owner, (installation_id, token) in installations.items()
            }
        }
    )


BROKER_RESPONSE = release(
    **{"sjawhar": (111, "broker-token-sjawhar"), "trajectory-labs-pbc": (222, "broker-token-trajectory")}
)
BROKER_RESPONSE_WITHOUT_TRAJECTORY = release(sjawhar=(111, "broker-token-sjawhar"))
NARROWED_RESPONSE = release(sjawhar=(111, "broker-token-sjawhar-narrowed"))

# A `secrets` stand-in serves only the upstream fallback and records every
# request. The App key must never be requested from this command.
SECRETS_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$SECRETS_STUB_LOG"
case "$2" in
  GH_PUBLIC_REPO_PAT) printf '%s\\n' ghp_stub_upstream_token ;;
  *) printf '%s\\n' "secrets: secret '$2' not found" >&2; exit 1 ;;
esac
"""

# This is a controlled boundary double for the broker command. It receives the
# real argv issued by the shim, returns a complete broker release, and records
# every argument separately so argument boundaries and command-line token leaks
# cannot pass unnoticed. It also records the agent-secrets its PATH resolves,
# which is how the real secret-run picks the broker.
SECRET_RUN_STUB = """#!/bin/sh
for arg do
  printf '%s\\n' "$arg" >> "$SECRET_RUN_STUB_LOG"
done
command -v agent-secrets > "$SECRET_RUN_STUB_LOG.agent-secrets" || true
if [ -n "${SECRET_RUN_STUB_EXIT:-}" ]; then
  [ -z "${SECRET_RUN_STUB_STDERR:-}" ] || printf '%s\\n' "$SECRET_RUN_STUB_STDERR" >&2
  exit "$SECRET_RUN_STUB_EXIT"
fi
printf '%s' "$SECRET_RUN_STUB_RESPONSE"
"""


def checked_now():
    """A cache entry's checked_at for a check at GitHub just now: inside the shim's interval, so
    a case about something else asks GitHub nothing (live_cached_token)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GitHubStandIn:
    """GitHub's GET /rate_limit, the one call the shim makes with a cached token: 401 for a token
    in revoked, answers[token] for a token there ("not-http" for a reply that is not HTTP), 200
    for any other. Each request is recorded as (path, token), and during, when set, runs before
    the answer: what another caller does while the check is out."""

    def __init__(self):
        self.revoked = set()
        self.answers = {}
        self.requests = []
        self.during = None
        stand_in = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                stand_in.requests.append((self.path, token))
                if stand_in.during:
                    stand_in.during()
                status = 401 if token in stand_in.revoked else stand_in.answers.get(token, 200)
                if status == "not-http":
                    self.wfile.write(b"not an HTTP reply\r\n\r\n")
                    return
                body = b"{}"
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass  # a line here would land in the stderr a case captures

        class Server(http.server.ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                pass  # a reply to a shim that stopped waiting fails; its traceback would land there too

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class GhAppTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.git_config = dict(GIT_CONFIG)
        self.refuse_owner_values = {}
        self.cache_dir = Path(self.temp_dir.name) / "cache"
        self.stub_dir = Path(self.temp_dir.name) / "bin"
        self.stub_dir.mkdir()
        for name, contents in {"secrets": SECRETS_STUB, "secret-run": SECRET_RUN_STUB}.items():
            stub = self.stub_dir / name
            stub.write_text(contents)
            stub.chmod(0o700)
        self.secrets_log = Path(self.temp_dir.name) / "secrets.log"
        self.secret_run_log = Path(self.temp_dir.name) / "secret-run.log"
        self.github = GitHubStandIn()
        self.patches = [
            patch.object(gh_app_token, "CACHE_DIR", self.cache_dir),
            patch.object(gh_app_token, "GITHUB_API", self.github.url),
            patch.object(gh_app_token, "git_config", side_effect=self.config_value),
            patch.object(gh_app_token, "git_config_optional", side_effect=self.git_config.get),
            patch.object(gh_app_token, "refused_owners", side_effect=self.refused_owners),
            patch.dict(
                os.environ,
                {
                    "PATH": f"{self.stub_dir}{os.pathsep}{os.environ['PATH']}",
                    "SECRETS_STUB_LOG": str(self.secrets_log),
                    "SECRET_RUN_STUB_LOG": str(self.secret_run_log),
                    "SECRET_RUN_STUB_RESPONSE": BROKER_RESPONSE,
                    "NO_PROXY": "127.0.0.1",
                    "no_proxy": "127.0.0.1",
                },
            ),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.github.close()
        self.temp_dir.cleanup()

    def config_value(self, key):
        value = self.git_config.get(key)
        if value is None:
            raise gh_app_token.AppTokenError(f"missing git config: {key}")
        return value

    def refused_owners(self, profile):
        """refused_owners as the shim's returns it: the profile's refuse-owner values, casefolded."""
        return frozenset(value.casefold() for value in self.refuse_owner_values.get(profile, ()))

    @staticmethod
    def refusal_detail(profile, owner):
        return (
            f"gh-app-token: owner {owner} is refused for profile {profile} on this machine "
            f"(gh-app.{profile}.refuse-owner): no token"
        )

    def refuse_owner(self, *owners):
        self.refuse_owner_values["agent"] = list(owners)

    def assert_refusal_decision(self, profile, owner, detail):
        decisions = (self.cache_dir / "decisions.log").read_text().splitlines()
        self.assertEqual(len(decisions), 1)
        decision = json.loads(decisions[0])
        self.assertEqual(decision["profile"], profile)
        self.assertEqual(decision["owner"], owner)
        self.assertEqual(decision["decision"], "refused")
        self.assertEqual(decision["detail"], detail)

    def secrets_calls(self):
        return self.secrets_log.read_text().splitlines() if self.secrets_log.exists() else []

    def secret_run_calls(self):
        return self.secret_run_log.read_text().splitlines() if self.secret_run_log.exists() else []

    @staticmethod
    def broker_command(owner, narrowing=()):
        return [
            "--register",
            "--reason",
            f"gh-app-token {owner}",
            *narrowing,
            "GITHUB_APP_SJAWHAR",
            "--",
            "printenv",
            "GITHUB_APP_SJAWHAR",
        ]

    def set_broker_response(self, response):
        os.environ["SECRET_RUN_STUB_RESPONSE"] = response

    # Every agent marker shims/gh and gh-app-token test for. A case that means "a person at
    # their own shell" must clear all of them, because the test process is itself an agent's.
    AGENT_MARKERS = (
        "OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION",
        "CLAUDECODE", "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "LEGION_ROLE", "LEGION_TREE",
        "LEGION_CONTROLLER", "LEGION_DAEMON_URL", "SSH_CONNECTION", "GIT_CONFIG_COUNT",
    )

    def as_person(self):
        """Make this call look like a person at their own terminal.

        An empty value is falsy to the same test the shim makes, and AGENT_SHIM_PERSON=1 is
        the documented escape for a deliberate non-interactive command of their own.
        """
        return patch.dict(
            os.environ,
            {name: "" for name in self.AGENT_MARKERS} | {"AGENT_SHIM_PERSON": "1"},
        )

    def as_agent(self):
        return patch.dict(os.environ, {"OMP_SESSION_ID": "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"})

    def run_main(self, argv, stdin=""):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(stdin)):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = gh_app_token.main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_a_secret_resolves_through_mise_shims_when_the_caller_removed_them_from_path(self):
        # 2026-10-24 (INFRA-219): mise runs [settings.github] credential_command with its
        # shims dir taken off PATH, and outside an interactive shell that dir is the only
        # place `secrets` is found. Every mint there failed, and mise fell back to a dead
        # gh login that GitHub refused with 401.
        mise_data = Path(self.temp_dir.name) / "mise"
        (mise_data / "shims").mkdir(parents=True)
        (mise_data / "shims" / "secrets").write_text(SECRETS_STUB)
        (mise_data / "shims" / "secrets").chmod(0o700)
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "MISE_DATA_DIR": str(mise_data)}):
            self.assertEqual(
                gh_app_token.resolve_secret("GH_PUBLIC_REPO_PAT"), "ghp_stub_upstream_token"
            )

    def test_the_broker_is_asked_through_mise_shims_when_the_caller_removed_them_from_path(self):
        # The same INFRA-219 PATH reaches secret-run, which picks the broker by finding
        # agent-secrets (and falls back to secrets): both are mise tools, so secret-run runs
        # with mise's shims dir after the caller's PATH.
        mise_data = Path(self.temp_dir.name) / "mise"
        (mise_data / "shims").mkdir(parents=True)
        (mise_data / "shims" / "agent-secrets").write_text("#!/bin/sh\nexit 0\n")
        (mise_data / "shims" / "agent-secrets").chmod(0o700)
        with patch.dict(
            os.environ, {"PATH": f"{self.stub_dir}{os.pathsep}/usr/bin:/bin", "MISE_DATA_DIR": str(mise_data)}
        ):
            rc, stdout, _ = self.run_main(["agent", "--owner", "sjawhar"])
        self.assertEqual((rc, stdout), (0, "broker-token-sjawhar\n"))
        self.assertEqual(
            Path(f"{self.secret_run_log}.agent-secrets").read_text().strip(),
            str(mise_data / "shims" / "agent-secrets"),
        )

    def test_secret_run_comes_from_this_checkout_when_path_lacks_it(self):
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            executable, _ = gh_app_token.secret_run_command()
        self.assertEqual(Path(executable), MODULE_PATH.resolve().parents[1] / "scripts" / "secret-run")

    def test_owner_precedence_uses_flag_then_env_then_path(self):
        request = {"path": "path-owner/repo.git"}
        with patch.dict(os.environ, {"GH_APP_OWNER": "environment-owner"}):
            self.assertEqual(gh_app_token.select_owner("flag-owner", request), "flag-owner")
            self.assertEqual(gh_app_token.select_owner(None, request), "environment-owner")
        self.assertEqual(gh_app_token.select_owner(None, request), "path-owner")
        self.assertIsNone(gh_app_token.select_owner(None, {}))

    def test_broker_response_returns_requested_token_caches_every_owner_and_never_logs_it(self):
        # This catches a return-to-JWT implementation, parsing just the requested
        # owner, or passing a broker token to another process as an argument.
        rc, stdout, stderr = self.run_main(["agent", "--owner", "sjawhar"])

        self.assertEqual((rc, stdout), (0, "broker-token-sjawhar\n"))
        self.assertNotIn("broker-token", stderr)
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))
        self.assertEqual(gh_app_token.get_cached_token("agent", "trajectory-labs-pbc"), "broker-token-trajectory")
        self.assertEqual(
            gh_app_token.covered_owners("3202636"),
            {"sjawhar": 111, "trajectory-labs-pbc": 222},
        )
        self.assertEqual(self.secrets_calls(), [])
        self.assertNotIn("broker-token-sjawhar", self.secret_run_calls())
        self.assertNotIn("broker-token-trajectory", self.secret_run_calls())
        self.assertNotIn("broker-token", (self.cache_dir / "decisions.log").read_text())

        with patch.dict(os.environ, {"SECRET_RUN_STUB_EXIT": "77"}):
            rc, stdout, stderr = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=trajectory-labs-pbc/agent-c.git\n\n"
            )

        self.assertEqual(rc, 0)
        self.assertIn("password=broker-token-trajectory", stdout)
        self.assertEqual(stderr, "")
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))

    def test_cached_owner_does_not_call_broker_again(self):
        # This catches refreshing a valid cache entry and unnecessarily asking the broker.
        rc, _, _ = self.run_main(["agent", "--owner", "sjawhar"])
        self.assertEqual(rc, 0)
        self.set_broker_response(BROKER_RESPONSE_WITHOUT_TRAJECTORY)

        rc, stdout, stderr = self.run_main(["agent", "--owner", "sjawhar"])

        self.assertEqual((rc, stdout, stderr), (0, "broker-token-sjawhar\n", ""))
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))

    def test_owner_never_covered_uses_only_the_fallback_pat_for_a_person(self):
        # This catches treating a missing installation as an App failure or
        # consulting secretsd for the broker-only App key.
        # As a person: the fallback is what their own push to an uncovered owner runs on, and
        # dispatch://INFRA-2031 refuses it only for an agent session.
        with self.as_person():
            rc, stdout, stderr = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=METR/hawk.git\n\n"
            )

        self.assertEqual(rc, 0)
        self.assertIn("username=x-access-token", stdout)
        self.assertIn("password=ghp_stub_upstream_token", stdout)
        self.assertNotIn("quit=1", stdout)
        # An uncovered owner gets the personal token, but never silently: the caller must
        # see that the identity about to act is the human's and not the App's.
        self.assertIn("METR", stderr)
        self.assertIn("GH_PUBLIC_REPO_PAT", stderr)
        self.assertIn("personal", stderr)
        self.assertEqual(self.secrets_calls(), ["get GH_PUBLIC_REPO_PAT --value"])
        self.assertEqual(self.secret_run_calls(), self.broker_command("metr"))
        self.assertEqual(
            gh_app_token.get_cached_token("agent", "trajectory-labs-pbc"),
            "broker-token-trajectory",
        )
        self.assertEqual(
            gh_app_token.covered_owners("3202636"),
            {"sjawhar": 111, "trajectory-labs-pbc": 222},
        )
        # The fallback is static: no token-cache entry is written for it.
        self.assertFalse(gh_app_token.token_cache_file("agent", "metr").exists())

    def test_an_agents_push_to_an_uncovered_owner_is_refused(self):
        """The hole dispatch://INFRA-2006 left: `gh` writes were gated, `git push` was not.

        git reaches the same personal token through the credential helper, which execs this
        program before that gate, so an agent could push to a third-party repository under the
        account owner's name. A fetch or clone of a public repository asks for no credential,
        so refusing here does not touch reads.
        """
        with self.as_agent():
            rc, stdout, stderr = self.run_main(
                ["agent", "get", "--for-write"],
                stdin="host=github.com\npath=METR/hawk.git\n\n",
            )

        self.assertEqual(rc, 1)
        self.assertIn("quit=1", stdout)
        self.assertNotIn("password=", stdout)
        self.assertIn("METR", stderr)
        self.assertIn("personal token", stderr)
        self.assertIn("have a person do it", stderr)

    def test_an_agents_routed_read_through_the_credential_interface_still_works(self):
        """knives mints for every routed `gh` call, read and write alike, through this same
        `get` interface (knives 3.2.3, src/commands/gh.rs). An earlier version of this gate
        refused on credential mode and would have failed ordinary `gh api` reads of uncovered
        owners outright. The write signal is --for-write, which only shims/gh's
        `gh auth git-credential` branch passes.
        """
        with self.as_agent():
            rc, stdout, _ = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=METR/hawk.git\n\n"
            )

        self.assertEqual(rc, 0)
        self.assertIn("password=ghp_stub_upstream_token", stdout)
        self.assertNotIn("quit=1", stdout)

    def test_an_agents_read_of_an_uncovered_owner_still_works(self):
        """Reads are untouched: refusing them would drop public-upstream reads from 5,000 an
        hour to the unauthenticated 60, shared across every box on the machine."""
        with self.as_agent():
            rc, stdout, _ = self.run_main(["agent", "--owner", "METR"])

        self.assertEqual(rc, 0)
        self.assertIn("ghp_stub_upstream_token", stdout)

    def test_an_agents_write_to_a_covered_owner_is_untouched(self):
        """Only the personal fallback is refused; an App installation token is not the person."""
        with self.as_agent():
            rc, stdout, _ = self.run_main(["agent", "--owner", "sjawhar", "--for-write"])

        self.assertEqual(rc, 0)
        self.assertIn("broker-token-sjawhar", stdout)

    def test_covered_owner_missing_from_later_broker_release_refuses_without_fallback(self):
        # 2026-09-17: two review verdicts on trajectory-labs-pbc/agent-c posted as the
        # human. A release that omits an owner it listed before is a contradiction, not a
        # routing decision: shrinking the historical coverage map would act as the human.
        rc, _, _ = self.run_main(["agent", "--owner", "sjawhar"])
        self.assertEqual(rc, 0)
        gh_app_token.token_cache_file("agent", "trajectory-labs-pbc").unlink()
        self.set_broker_response(BROKER_RESPONSE_WITHOUT_TRAJECTORY)

        rc, stdout, stderr = self.run_main(
            ["agent", "get"], stdin="host=github.com\npath=trajectory-labs-pbc/agent-c.git\n\n"
        )

        self.assertEqual((rc, stdout), (1, "quit=1\n"))
        self.assertIn("trajectory-labs-pbc", stderr)
        self.assertIn("refusing", stderr)
        self.assertEqual(self.secrets_calls(), [])
        self.assertEqual(
            self.secret_run_calls(),
            self.broker_command("sjawhar") + self.broker_command("trajectory-labs-pbc"),
        )

        rc, stdout, _ = self.run_main(["agent", "--owner", "trajectory-labs-pbc"])
        self.assertEqual((rc, stdout), (1, ""))
        self.assertEqual(self.secrets_calls(), [])

    def test_concurrent_cache_misses_never_drop_historical_coverage(self):
        # Two cache misses that each receive a different release must serialize
        # their coverage read-merge-write. The barriers make an unlocked merge
        # perform both reads before either write.
        first_owner, second_owner = "first-owner", "second-owner"
        releases = {
            first_owner: {
                first_owner: {
                    "installation_id": 333,
                    "token": "broker-token-first",
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            },
            second_owner: {
                second_owner: {
                    "installation_id": 444,
                    "token": "broker-token-second",
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            },
        }
        coverage_file = gh_app_token.covered_owners_file("3202636")
        original_covered_owners = gh_app_token.covered_owners
        original_write_cache_file = gh_app_token.write_cache_file
        read_started = threading.Barrier(2)
        reads_complete = threading.Barrier(2)
        second_write = threading.Event()

        def raced_covered_owners(app_id):
            try:
                read_started.wait(timeout=60)
            except threading.BrokenBarrierError:
                return original_covered_owners(app_id)
            covered = original_covered_owners(app_id)
            try:
                reads_complete.wait(timeout=60)
            except threading.BrokenBarrierError:
                pass
            return covered

        def raced_write_cache_file(path, data):
            if path == coverage_file and not read_started.broken:
                if first_owner in data:
                    if not second_write.wait(timeout=60):
                        raise RuntimeError("second coverage write did not run")
                else:
                    original_write_cache_file(path, data)
                    second_write.set()
                    return
            original_write_cache_file(path, data)

        results, errors = {}, []

        def cache_miss(owner):
            try:
                results[owner] = gh_app_token.get_token("agent", owner, refused=frozenset())
            except Exception as error:
                errors.append(error)

        with (
            patch.object(
                gh_app_token,
                "request_broker_installations",
                side_effect=lambda secret_name, owner, scope=None: releases[owner],
            ),
            patch.object(gh_app_token, "covered_owners", side_effect=raced_covered_owners),
            patch.object(gh_app_token, "write_cache_file", side_effect=raced_write_cache_file),
            redirect_stderr(io.StringIO()),
        ):
            first = threading.Thread(target=cache_miss, args=(first_owner,))
            second = threading.Thread(target=cache_miss, args=(second_owner,))
            first.start()
            second.start()
            first.join(timeout=300)
            second.join(timeout=300)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(
            results,
            {first_owner: "broker-token-first", second_owner: "broker-token-second"},
        )
        self.assertEqual(
            gh_app_token.covered_owners("3202636"),
            {first_owner: 333, second_owner: 444},
        )

    def test_corrupt_coverage_file_refuses_without_reading_the_fallback_pat(self):
        # A damaged permanent record is unknown coverage, not proof an owner was
        # never covered. Treating it as an empty mapping re-enables human identity.
        rc, _, _ = self.run_main(["agent", "--owner", "sjawhar"])
        self.assertEqual(rc, 0)
        gh_app_token.token_cache_file("agent", "trajectory-labs-pbc").unlink()
        gh_app_token.covered_owners_file("3202636").write_text("{")
        self.set_broker_response(BROKER_RESPONSE_WITHOUT_TRAJECTORY)

        rc, stdout, stderr = self.run_main(
            ["agent", "get"], stdin="host=github.com\npath=trajectory-labs-pbc/agent-c.git\n\n"
        )

        self.assertEqual((rc, stdout), (1, "quit=1\n"))
        self.assertIn("covered-owner", stderr)
        self.assertEqual(self.secrets_calls(), [])

    def test_broker_parse_failure_never_reads_or_logs_a_fallback_token(self):
        # This catches treating corrupt broker output as an uncovered owner or
        # including its captured stdout in an error message.
        sentinel = "broker-token-malformed-output"
        self.set_broker_response(f'{{"installations": {{"sjawhar": "{sentinel}"')

        rc, stdout, stderr = self.run_main(
            ["agent", "get"], stdin="host=github.com\npath=sjawhar/dotfiles.git\n\n"
        )

        self.assertEqual((rc, stdout), (1, "quit=1\n"))
        self.assertIn("invalid installations JSON", stderr)
        self.assertNotIn(sentinel, stdout)
        self.assertNotIn(sentinel, stderr)
        self.assertNotIn(sentinel, (self.cache_dir / "decisions.log").read_text())
        self.assertEqual(self.secrets_calls(), [])

    def test_owner_matching_is_case_insensitive_before_the_broker_request(self):
        # This catches handing an uppercase owner to the broker and falling back
        # despite a lowercased installed owner in the release.
        rc, stdout, stderr = self.run_main(
            ["agent", "get"], stdin="host=github.com\npath=SJAWHAR/dotfiles.git\n\n"
        )

        self.assertEqual(rc, 0)
        self.assertIn("password=broker-token-sjawhar", stdout)
        self.assertNotIn("personal", stderr)
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))
        self.assertEqual(self.secrets_calls(), [])

    def assert_refusal_stops_every_token_path(self, credential_mode):
        owner = "trajectory-labs-pbc"
        self.refuse_owner(owner)
        self.git_config["gh-app.agent.key-secret"] = "GITHUB_APP_PRIVATE_KEY"
        old_cache_dir = Path(self.temp_dir.name) / "old-cache"
        old_cache_dir.mkdir()
        old_cache_file = old_cache_dir / gh_app_token.token_cache_file("agent", owner).name
        old_cache_file.write_text(json.dumps({
            "token": "cached-token-that-must-not-be-served",
            "expires_at": "2099-01-01T00:00:00Z",
        }))
        detail = self.refusal_detail("agent", owner)
        argv = ["agent", "get"] if credential_mode else ["agent", "--owner", owner]
        stdin = "host=github.com\npath=trajectory-labs-pbc/repo.git\n\n" if credential_mode else ""

        def forbidden(name):
            def called(*args, **kwargs):
                self.fail(f"{name} was called for a refused owner")

            return called

        with (
            patch.object(gh_app_token, "DEFAULT_CACHE_DIR", self.cache_dir),
            patch.object(gh_app_token, "OLD_CACHE_DIR", old_cache_dir),
            patch.object(gh_app_token, "seed_cache_dir", side_effect=forbidden("seed_cache_dir")),
            patch.object(gh_app_token, "get_cached_token", side_effect=forbidden("get_cached_token")),
            patch.object(gh_app_token, "mint_from_secretsd", side_effect=forbidden("mint_from_secretsd")),
            patch.object(gh_app_token, "fallback_token", side_effect=forbidden("fallback_token")),
        ):
            rc, stdout, stderr = self.run_main(argv, stdin=stdin)

        expected_stdout = "quit=1\n" if credential_mode else ""
        self.assertEqual((rc, stdout, stderr), (1, expected_stdout, detail + "\n"))
        self.assertEqual(self.secrets_calls(), [])
        self.assertEqual(self.secret_run_calls(), [])
        self.assertEqual(sorted(path.name for path in self.cache_dir.iterdir()), ["decisions.log"])
        self.assertFalse((old_cache_dir / gh_app_token.MOVED_NOTE).exists())
        self.assert_refusal_decision("agent", owner, detail)

    def test_refused_owner_stops_every_token_path_in_direct_mode(self):
        self.assert_refusal_stops_every_token_path(credential_mode=False)

    def test_refused_owner_stops_every_token_path_in_credential_mode(self):
        self.assert_refusal_stops_every_token_path(credential_mode=True)

    def test_same_owner_without_refusal_uses_cached_token_in_direct_and_credential_modes(self):
        owner = "trajectory-labs-pbc"
        gh_app_token.write_cache_file(
            gh_app_token.token_cache_file("agent", owner),
            {"token": "cached-token", "expires_at": "2099-01-01T00:00:00Z"},
        )

        direct = self.run_main(["agent", "--owner", owner])
        credential = self.run_main(
            ["agent", "get"], stdin="host=github.com\npath=trajectory-labs-pbc/repo.git\n\n"
        )

        self.assertEqual(direct, (0, "cached-token\n", ""))
        self.assertEqual(credential[0], 0)
        self.assertIn("password=cached-token", credential[1])
        self.assertEqual(credential[2], "")
        self.assertEqual(self.secrets_calls(), [])
        self.assertEqual(self.secret_run_calls(), [])

    def test_an_owner_wide_release_caches_no_token_for_a_refused_owner(self):
        # A mint for sjawhar gets the owner-wide release, which holds the refused owner's token
        # too; caching every owner of it would leave a token for the refused owner on this machine.
        self.refuse_owner("trajectory-labs-pbc")
        self.set_broker_response(BROKER_RESPONSE)

        rc, stdout, stderr = self.run_main(["agent", "--owner", "sjawhar"])

        self.assertEqual((rc, stdout), (0, "broker-token-sjawhar\n"), stderr)
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))
        self.assertEqual(
            sorted(path.name for path in self.cache_dir.glob("token-*")),
            [gh_app_token.token_cache_file("agent", "sjawhar").name],
        )
        self.assertFalse(gh_app_token.token_cache_file("agent", "trajectory-labs-pbc").exists())
        self.assertEqual(
            gh_app_token.covered_owners("3202636"),
            {"sjawhar": 111, "trajectory-labs-pbc": 222},
        )

    def test_refuse_owner_matching_is_case_insensitive(self):
        # git hands back the owner as the machine config spells it, here in lowercase; a remote URL or
        # a person may spell it in mixed case. Matching the owner as requested would mint its token.
        self.refuse_owner("trajectory-labs-pbc")
        requested = "Trajectory-Labs-PBC"
        detail = self.refusal_detail("agent", requested)

        direct = self.run_main(["agent", "--owner", requested])
        credential = self.run_main(
            ["agent", "get"], stdin=f"host=github.com\npath={requested}/agent-c.git\n\n"
        )

        self.assertEqual(direct, (1, "", detail + "\n"))
        self.assertEqual(credential, (1, "quit=1\n", detail + "\n"))
        self.assertEqual(self.secret_run_calls(), [])
        self.assertEqual(self.secrets_calls(), [])

    def test_gh_app_owner_refusal_stops_the_direct_owner_override(self):
        owner = "trajectory-labs-pbc"
        self.refuse_owner(owner)

        with patch.dict(os.environ, {"GH_APP_OWNER": owner}):
            rc, stdout, stderr = self.run_main(["agent"])

        self.assertEqual(
            (rc, stdout, stderr),
            (1, "", self.refusal_detail("agent", owner) + "\n"),
        )

    def test_broker_pending_or_denied_names_machine_login(self):
        # This catches propagating a broker refusal without the actionable
        # machine-login remedy, or swapping identity to the personal token.
        for exit_code in ("75", "77"):
            with self.subTest(exit_code=exit_code), patch.dict(
                os.environ, {"SECRET_RUN_STUB_EXIT": exit_code}
            ):
                rc, stdout, stderr = self.run_main(
                    ["agent", "get"], stdin="host=github.com\npath=sjawhar/dotfiles.git\n\n"
                )
            self.assertEqual((rc, stdout), (1, "quit=1\n"))
            self.assertIn("agent-secrets-login", stderr)
        self.assertEqual(self.secrets_calls(), [])

    def test_broker_unreachable_quits_naming_the_cause_and_never_reads_secretsd(self):
        # A broker outage must stop git with the reason agent-secrets gave, never fall back
        # to the personal token or to an App key in secretsd.
        cause = "agent-secrets: nothing answered at 127.0.0.1:9: the connection was refused"
        with patch.dict(os.environ, {"SECRET_RUN_STUB_EXIT": "1", "SECRET_RUN_STUB_STDERR": cause}):
            rc, stdout, stderr = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=sjawhar/dotfiles.git\n\n"
            )
        self.assertEqual((rc, stdout), (1, "quit=1\n"))
        self.assertIn(cause, stderr)
        self.assertEqual(self.secrets_calls(), [])

    def test_the_clients_enrollment_wait_notices_are_never_the_cause(self):
        # The client says it waits for a launcher still enrolling the box before its own
        # refusal; the cause is the refusal, whether the launcher finished in that wait and a
        # later step failed, or the wait ran out.
        notices = (
            "agent-secrets: the launcher is still enrolling this process (/run/user/1/agent-secrets/enrollment.pending is there); waiting up to 20s for its key.pem and enrollment\n"
            "agent-secrets: still waiting for the launcher's enrollment\n"
        )
        for cause in (
            "agent-secrets: nothing answered at 127.0.0.1:9: the connection was refused",
            "agent-secrets: /run/user/1/agent-secrets/key.pem: not a PEM file (waited 20s while /run/user/1/agent-secrets/enrollment.pending was there)",
        ):
            with self.subTest(cause=cause), patch.dict(
                os.environ, {"SECRET_RUN_STUB_EXIT": "1", "SECRET_RUN_STUB_STDERR": notices + cause}
            ):
                rc, stdout, stderr = self.run_main(
                    ["agent", "get"], stdin="host=github.com\npath=sjawhar/dotfiles.git\n\n"
                )
                self.assertEqual((rc, stdout), (1, "quit=1\n"))
                self.assertIn(f"failed (exit 1): {cause}", stderr)
        self.assertEqual(self.secrets_calls(), [])

    def test_uninstalled_owner_without_fallback_secret_quits(self):
        del self.git_config["gh-app.agent.fallback-secret"]

        rc, stdout, stderr = self.run_main(
            ["agent", "get"], stdin="host=github.com\npath=METR/hawk.git\n\n"
        )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "quit=1\n")
        self.assertIn(
            "no App installation for owner METR and no fallback-secret on profile agent", stderr
        )
        self.assertEqual(self.secrets_calls(), [])

    def test_uninstalled_owner_with_unreadable_fallback_secret_quits(self):
        self.git_config["gh-app.agent.fallback-secret"] = "GH_MISSING_TOKEN"

        rc, stdout, stderr = self.run_main(
            ["agent", "get"], stdin="host=github.com\npath=METR/hawk.git\n\n"
        )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "quit=1\n")
        self.assertIn("failed to fetch secret GH_MISSING_TOKEN", stderr)
        self.assertIn("secret 'GH_MISSING_TOKEN' not found", stderr)

    def test_uninstalled_owner_prints_fallback_secret_in_cli_mode(self):
        rc, stdout, _ = self.run_main(["agent", "--owner", "METR"])
        self.assertEqual((rc, stdout), (0, "ghp_stub_upstream_token\n"))

        del self.git_config["gh-app.agent.fallback-secret"]
        rc, stdout, stderr = self.run_main(["agent", "--owner", "METR"])
        self.assertEqual((rc, stdout), (1, ""))
        self.assertIn("no App installation for owner METR", stderr)

    def test_missing_owner_falls_through_in_credential_mode_only(self):
        rc, stdout, _ = self.run_main(["agent", "get"], stdin="host=github.com\n\n")
        self.assertEqual((rc, stdout), (0, ""))
        rc, _, stderr = self.run_main(["agent"])
        self.assertEqual(rc, 1)
        self.assertIn("target owner required", stderr)

    def test_real_error_emits_quit_to_stop_gits_helper_chain(self):
        with patch.object(
            gh_app_token, "get_token", side_effect=gh_app_token.AppTokenError("secret missing")
        ):
            rc, stdout, stderr = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=sjawhar/repo.git\n\n"
            )
        self.assertEqual(rc, 1)
        self.assertIn("quit=1", stdout)
        self.assertNotIn("password=", stdout)
        self.assertIn("secret missing", stderr)

    def test_credential_mode_emits_git_credential_response(self):
        rc, stdout, _ = self.run_main(
            ["agent", "get"], stdin="host=github.com\npath=sjawhar/repo.git\n\n"
        )

        self.assertEqual(rc, 0)
        self.assertIn("username=x-access-token", stdout)
        self.assertIn("password=broker-token-sjawhar", stdout)

    def test_token_cache_is_partitioned_by_profile_and_owner(self):
        token_data = {
            "token": "cached-token",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        gh_app_token.write_cache_file(gh_app_token.token_cache_file("agent", "sjawhar"), token_data)
        self.assertEqual(gh_app_token.get_cached_token("agent", "sjawhar"), "cached-token")
        self.assertIsNone(gh_app_token.get_cached_token("agent", "trajectory-labs-pbc"))
        self.assertIsNone(gh_app_token.get_cached_token("other-profile", "sjawhar"))

    def test_a_profile_whose_app_id_changes_never_gets_the_old_apps_token(self):
        # Given: a token cached for the profile while it named one App. On 2026-10-26 the
        # review profile was first declared with the wrong App id, then corrected; the cache
        # kept answering with the first App's token for any later id, nonsense ones included.
        token_data = {
            "token": "old-app-token",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        gh_app_token.write_cache_file(gh_app_token.token_cache_file("agent", "sjawhar"), token_data)
        # When: the profile's app-id changes, and a caller asks for a token.
        self.git_config["gh-app.agent.app-id"] = "9999999"
        rc, stdout, _ = self.run_main(["agent", "--owner", "sjawhar"])
        # Then: the old App's token is not reused; the broker is asked for a fresh release.
        self.assertEqual((rc, stdout), (0, "broker-token-sjawhar\n"))
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))

    def test_a_cached_token_is_reused_only_with_the_life_the_caller_asks_for(self):
        # Given: a cached token with twelve minutes left, the way a long `gh run watch`
        # was handed one and failed with 401 twelve minutes in (2026-10-22).
        token_data = {
            "token": "cached-token",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat(),
        }
        gh_app_token.write_cache_file(gh_app_token.token_cache_file("agent", "sjawhar"), token_data)
        request = "host=github.com\npath=sjawhar/repo.git\n\n"

        # When: a caller asks with no lifetime, then for half an hour.
        _, default_answer, _ = self.run_main(["agent", "get"], stdin=request)
        with patch.dict(os.environ, {gh_app_token.MIN_LIFETIME_ENV: "1800"}):
            _, long_answer, _ = self.run_main(["agent", "get"], stdin=request)

        # Then: the default keeps the five-minute margin and reuses the cache; the
        # half-hour caller gets a fresh release, which replaces the cached token.
        self.assertIn("password=cached-token", default_answer)
        self.assertIn("password=broker-token-sjawhar", long_answer)
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))
        self.assertEqual(gh_app_token.get_cached_token("agent", "sjawhar"), "broker-token-sjawhar")

    def test_a_fresh_broker_release_is_returned_when_the_request_exceeds_an_hour(self):
        # A caller asking for more than GitHub's hour gets the fresh token, not a loop.
        with redirect_stderr(io.StringIO()):
            self.assertEqual(
                gh_app_token.get_token("agent", "sjawhar", 10**9, refused=frozenset()),
                "broker-token-sjawhar",
            )
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))

    def test_a_malformed_min_lifetime_refuses(self):
        with patch.dict(os.environ, {gh_app_token.MIN_LIFETIME_ENV: "an hour"}):
            rc, stdout, stderr = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=sjawhar/repo.git\n\n"
            )
        self.assertEqual(rc, 1)
        self.assertIn("quit=1", stdout)
        self.assertIn("GH_APP_TOKEN_MIN_LIFETIME must be a whole number", stderr)

    def test_cache_directory_created_with_mode_0700(self):
        gh_app_token.write_cache_file(gh_app_token.token_cache_file("agent", "sjawhar"), {})
        self.assertEqual(gh_app_token.CACHE_DIR.stat().st_mode & 0o777, 0o700)

    def test_a_repo_read_only_token_asks_the_broker_for_exactly_that_narrowing(self):
        # 2026-10-25 (INFRA-322): the Lean VMs get a token that can only read one repository.
        # The broker mints it with GitHub's access_tokens body; the shim names the narrowing.
        self.set_broker_response(NARROWED_RESPONSE)
        rc, stdout, _ = self.run_main(
            ["agent", "--owner", "sjawhar", "--repo", "sjawhar/proofs-internal", "--read-only"]
        )
        self.assertEqual((rc, stdout), (0, "broker-token-sjawhar-narrowed\n"))
        self.assertEqual(
            self.secret_run_calls(),
            self.broker_command(
                "sjawhar",
                ("--github-owner", "sjawhar", "--github-repo", "proofs-internal", "--github-read-only"),
            ),
        )
        self.assertEqual(self.secrets_calls(), [])

    def test_a_narrowed_token_and_the_owner_wide_token_never_share_a_cache_entry(self):
        owner_wide = {
            "token": "owner-wide-token",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        gh_app_token.write_cache_file(gh_app_token.token_cache_file("agent", "sjawhar"), owner_wide)
        self.set_broker_response(NARROWED_RESPONSE)
        narrowed = ["agent", "--owner", "sjawhar", "--repo", "proofs-internal", "--read-only"]

        scoped = self.run_main(narrowed)
        wide = self.run_main(["agent", "--owner", "sjawhar"])
        again = self.run_main(narrowed)

        # The narrowed request asked the broker once and cached its token apart; the
        # owner-wide cache was neither used for it nor replaced by it.
        self.assertEqual(scoped[:2], (0, "broker-token-sjawhar-narrowed\n"))
        self.assertEqual(wide[:2], (0, "owner-wide-token\n"))
        self.assertEqual(again[:2], (0, "broker-token-sjawhar-narrowed\n"))
        self.assertEqual(len([arg for arg in self.secret_run_calls() if arg == "--reason"]), 1)
        self.assertEqual(gh_app_token.get_cached_token("agent", "sjawhar"), "owner-wide-token")

    def test_a_narrowed_request_never_falls_back_to_the_personal_token(self):
        # The personal token cannot be narrowed: an owner the broker will not narrow a
        # token for (it refuses one github-owners does not list) and a release that
        # names no installation for the owner both refuse, never reading the PAT.
        with patch.dict(os.environ, {"SECRET_RUN_STUB_EXIT": "1"}):
            rc, stdout, stderr = self.run_main(
                ["agent", "get", "--read-only"], stdin="host=github.com\npath=METR/hawk.git\n\n"
            )
        self.assertEqual((rc, stdout), (1, "quit=1\n"))
        self.assertIn("a --repo/--read-only token has no fallback", stderr)

        self.set_broker_response(release())
        rc, stdout, stderr = self.run_main(["agent", "--owner", "METR", "--repo", "hawk"])
        self.assertEqual((rc, stdout), (1, ""))
        self.assertIn("a --repo/--read-only token has no fallback", stderr)
        self.assertEqual(self.secrets_calls(), [])

    def cache_entry(self, owner, token, checked_seconds_ago, scope=None, left=timedelta(minutes=50)):
        """Cache token for owner (and scope) with left before it lapses, its last check at GitHub
        checked_seconds_ago seconds back: negative is in the future, and None is an entry with no
        check, which a shim from before the check wrote."""
        now = datetime.now(timezone.utc)
        entry = {"token": token, "expires_at": (now + left).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if checked_seconds_ago is not None:
            entry["checked_at"] = (now - timedelta(seconds=checked_seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache_file = gh_app_token.token_cache_file("agent", owner, gh_app_token.scope_key(scope))
        gh_app_token.write_cache_file(cache_file, entry)
        return cache_file

    def assert_checked_between(self, cache_file, before, after):
        """cache_file's checked_at is a time between before and after (it is kept to the second)."""
        checked = datetime.fromisoformat(json.loads(cache_file.read_text())["checked_at"].replace("Z", "+00:00"))
        self.assertLessEqual(before.replace(microsecond=0), checked)
        self.assertLessEqual(checked, after)

    def broker_mints(self):
        return len([arg for arg in self.secret_run_calls() if arg == "--reason"])

    def test_a_cached_token_github_revoked_is_dropped_and_minted_once_afresh(self):
        # Given: a cached token GitHub has revoked since (2026-11-22: a leaked token's revoke left
        # every call for its repository with the dead token and a 401 until it lapsed), its last
        # check 61 s back, past the 60 s interval; never checked; or checked "in the future".
        cases = (
            ("narrowed", ["agent", "--owner", "sjawhar", "--repo", "oh-my-pi"], {"repositories": ["oh-my-pi"]},
             NARROWED_RESPONSE, "broker-token-sjawhar-narrowed"),
            ("owner-wide", ["agent", "--owner", "sjawhar"], None, BROKER_RESPONSE, "broker-token-sjawhar"),
        )
        for name, argv, scope, response, fresh in cases:
            for checked in (61, None, -120):
                with self.subTest(token=name, checked_seconds_ago=checked):
                    self.secret_run_log.unlink(missing_ok=True)
                    self.github.requests.clear()
                    cache_file = self.cache_entry("sjawhar", "revoked-token", checked, scope)
                    self.github.revoked.add("revoked-token")
                    self.set_broker_response(response)

                    rc, stdout, stderr = self.run_main(argv)

                    # Then: GitHub is asked once, the dead token is not served, and one fresh
                    # token is minted and cached in its place.
                    self.assertEqual((rc, stdout), (0, fresh + "\n"), stderr)
                    self.assertEqual(self.github.requests, [("/rate_limit", "revoked-token")])
                    self.assertIn("GitHub answered 401 for the cached token", stderr)
                    self.assertNotIn("revoked-token", stderr)
                    self.assertEqual(self.broker_mints(), 1)
                    self.assertEqual(json.loads(cache_file.read_text())["token"], fresh)
                    # And the next call is served the fresh token from the cache: it was checked
                    # when minted, so neither GitHub nor the broker is asked again.
                    self.assertEqual(self.run_main(argv), (0, fresh + "\n", ""))
                    self.assertEqual(len(self.github.requests), 1)
                    self.assertEqual(self.broker_mints(), 1)

    def test_a_cached_token_checked_inside_the_interval_is_served_without_asking_github(self):
        # Given: a cached token last checked 30 s back, inside the 60 s interval, that GitHub
        # would answer 401 for if asked.
        self.cache_entry("sjawhar", "revoked-token", 30)
        self.github.revoked.add("revoked-token")

        rc, stdout, stderr = self.run_main(["agent", "--owner", "sjawhar"])

        # Then: it is served as cached, and neither GitHub nor the broker is asked: a revoke is
        # seen at the next check, at most a minute late, and a call in between costs nothing.
        self.assertEqual((rc, stdout, stderr), (0, "revoked-token\n", ""))
        self.assertEqual(self.github.requests, [])
        self.assertEqual(self.secret_run_calls(), [])

    def test_a_live_cached_token_is_checked_once_an_interval_and_the_check_recorded(self):
        cache_file = self.cache_entry("sjawhar", "live-token", 61)

        before = datetime.now(timezone.utc)
        first = self.run_main(["agent", "--owner", "sjawhar"])
        second = self.run_main(["agent", "get"], stdin="host=github.com\npath=sjawhar/repo.git\n\n")
        after = datetime.now(timezone.utc)

        self.assertEqual(first, (0, "live-token\n", ""))
        self.assertEqual(second[0], 0)
        self.assertIn("password=live-token", second[1])
        self.assertEqual(self.github.requests, [("/rate_limit", "live-token")])
        self.assertEqual(self.secret_run_calls(), [])
        self.assert_checked_between(cache_file, before, after)

    def test_any_answer_but_401_or_none_serves_the_cached_token_and_records_the_check(self):
        # GitHub refusing for another reason (a rate limit, an outage), a reply that is not HTTP,
        # or no answer at all says nothing about the token: it is served as before, and the check
        # is not repeated inside the interval, so an outage costs one wait a minute, not one a call.
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            nothing_listens = f"http://127.0.0.1:{closed.getsockname()[1]}"
        for answer in (403, 429, 500, 503, "not-http", None):
            with self.subTest(answer=answer):
                self.github.requests.clear()
                cache_file = self.cache_entry("sjawhar", "cached-token", 61)
                api = self.github.url
                if answer is None:
                    api = nothing_listens
                else:
                    self.github.answers["cached-token"] = answer
                before = datetime.now(timezone.utc)
                with patch.object(gh_app_token, "GITHUB_API", api):
                    first = self.run_main(["agent", "--owner", "sjawhar"])
                    second = self.run_main(["agent", "--owner", "sjawhar"])
                after = datetime.now(timezone.utc)
                self.assertEqual(first, (0, "cached-token\n", ""))
                self.assertEqual(second, (0, "cached-token\n", ""))
                self.assertEqual(len(self.github.requests), 0 if answer is None else 1)
                self.assertEqual(self.secret_run_calls(), [])
                self.assert_checked_between(cache_file, before, after)

    def test_a_github_that_does_not_answer_holds_the_call_only_for_the_timeout(self):
        # GitHub accepts the connection and says nothing for 3 s; the shim waits LIVENESS_TIMEOUT
        # (here 0.5 s) for it, then serves the cached token and records the check.
        cache_file = self.cache_entry("sjawhar", "cached-token", 61)
        self.github.during = lambda: time.sleep(3)

        before = datetime.now(timezone.utc)
        with patch.object(gh_app_token, "LIVENESS_TIMEOUT", 0.5):
            answer = self.run_main(["agent", "--owner", "sjawhar"])
        after = datetime.now(timezone.utc)

        self.assertEqual(answer, (0, "cached-token\n", ""))
        self.assertLess((after - before).total_seconds(), 2.5)
        self.assertEqual(self.secret_run_calls(), [])
        self.assert_checked_between(cache_file, before, after)

    def test_a_check_never_replaces_an_entry_another_call_wrote_while_it_was_out(self):
        # Given: a stale entry whose check is out when another call caches a fresh token there
        # (its mint after a 401, or a GH_APP_TOKEN_MIN_LIFETIME caller's).
        cache_file = self.cache_entry("sjawhar", "cached-token", 61)
        fresh = {"token": "fresh-token", "expires_at": "2099-01-01T00:00:00Z", "checked_at": checked_now()}
        self.github.during = lambda: gh_app_token.write_cache_file(cache_file, fresh)

        answer = self.run_main(["agent", "--owner", "sjawhar"])

        # Then: the checked token is served, and the record of its check does not put the older
        # token back over the fresh one.
        self.assertEqual(answer, (0, "cached-token\n", ""))
        self.assertEqual(json.loads(cache_file.read_text()), fresh)

    def test_a_drop_waits_for_the_token_cache_lock_and_keeps_what_was_written_under_it(self):
        # A drop's compare and remove run under the lock every cache write of a mint takes, so a
        # fresh token written between the compare and the remove is never removed.
        cache_file = self.cache_entry("sjawhar", "revoked-token", 61)
        fresh = {"token": "fresh-token", "expires_at": "2099-01-01T00:00:00Z", "checked_at": checked_now()}
        drop = threading.Thread(target=gh_app_token.settle_checked_entry, args=(cache_file, "revoked-token", None))
        with gh_app_token.token_cache_locked():
            drop.start()
            drop.join(0.5)
            self.assertTrue(drop.is_alive(), "the drop did not wait for the token-cache lock")
            gh_app_token.write_cache_file(cache_file, fresh)
        drop.join(10)
        self.assertFalse(drop.is_alive())
        self.assertEqual(json.loads(cache_file.read_text()), fresh)

    def test_a_cached_token_whose_check_outlasts_the_life_asked_for_is_not_served(self):
        # Given: a cached token with about 1810 s left, a caller that needs 1800 s, and a check
        # during which the shim's clock moves on 20 s.
        class Clock(datetime):
            offset = timedelta(0)

            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + cls.offset

        self.cache_entry("sjawhar", "cached-token", 61, left=timedelta(seconds=1811))
        self.github.during = lambda: setattr(Clock, "offset", timedelta(seconds=20))

        with patch.object(gh_app_token, "datetime", Clock), \
                patch.dict(os.environ, {gh_app_token.MIN_LIFETIME_ENV: "1800"}):
            rc, stdout, stderr = self.run_main(["agent", "--owner", "sjawhar"])

        # Then: the token was checked, and, now short of the life asked for, is not served; a
        # fresh one is.
        self.assertEqual(self.github.requests, [("/rate_limit", "cached-token")])
        self.assertEqual((rc, stdout), (0, "broker-token-sjawhar\n"), stderr)
        self.assertEqual(self.broker_mints(), 1)

class BoxEnrollmentWait(unittest.TestCase):
    """A new agent box has no broker identity for its first ~100 s, so its first GitHub call
    waits for the enrollment instead of failing it (dispatch://INFRA-702). The wait is keyed on
    the enrollment.pending marker scripts/agentbox writes and keeps fresh, and on nothing else:
    a host session has no key dir of that shape, so it never waits at all.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.key_dir = Path(self.temp_dir.name) / "agent-secrets"
        self.key_dir.mkdir()
        self.marker = self.key_dir / gh_app_token.ENROLLMENT_MARKER

    def wait(self, sleep, environment=None):
        """wait_for_box_enrollment with `sleep` standing in for time.sleep, in environment (by
        default one naming this test's key dir); returns its stderr."""
        if environment is None:
            environment = {"AGENT_SECRETS_KEY_DIR": str(self.key_dir)}
        err = io.StringIO()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(gh_app_token.time, "sleep", sleep),
            redirect_stderr(err),
        ):
            gh_app_token.wait_for_box_enrollment()
        return err.getvalue()

    def never_sleeps(self, seconds):
        self.fail(f"waited {seconds}s where nothing says an enrollment is under way")

    def test_a_host_session_never_waits(self):
        """A host session's key dir is its runtime dir's agent-secrets, which holds the helper's
        socket and no marker; a marker in another dir holds nothing."""
        self.marker.touch()
        host = Path(self.temp_dir.name) / "host-runtime"
        (host / "agent-secrets").mkdir(parents=True)
        (host / "agent-secrets" / "helper.sock").touch()
        self.assertEqual(self.wait(self.never_sleeps, environment={"XDG_RUNTIME_DIR": str(host)}), "")

    def test_a_box_whose_enrollment_has_ended_never_waits(self):
        self.assertEqual(self.wait(self.never_sleeps), "")

    def test_a_marker_no_launcher_is_renewing_never_waits(self):
        """The launcher renews the marker while it works; one older than the client's own trust
        window means the launcher is gone, and waiting for it would hang the call."""
        self.marker.touch()
        stale = gh_app_token.time.time() - gh_app_token.ENROLLMENT_MARKER_FRESH_SECONDS - 10
        os.utime(self.marker, (stale, stale))
        self.assertEqual(self.wait(self.never_sleeps), "")

    def test_it_waits_until_the_enrollment_ends(self):
        self.marker.touch()
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            if len(slept) == 3:
                self.marker.unlink()  # The launcher enrolled the box, or gave up.

        stderr = self.wait(sleep)
        self.assertEqual(len(slept), 3)
        # Said once, not once per second.
        self.assertEqual(stderr.count("still being enrolled"), 1)

    def test_the_wait_is_bounded_even_while_the_marker_stays_fresh(self):
        self.marker.touch()
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds
            os.utime(self.marker, None)  # A launcher that never finishes keeps it fresh.

        with patch.object(gh_app_token.time, "monotonic", lambda: clock[0]):
            self.wait(sleep)
        self.assertEqual(clock[0], gh_app_token.ENROLLMENT_WAIT_SECONDS)
        # Long enough for the 116 s a box took to enroll on 2026-11-01.
        self.assertGreaterEqual(gh_app_token.ENROLLMENT_WAIT_SECONDS, 160)

    def test_an_environment_without_the_key_dir_variable_waits_on_the_clients_default(self):
        """An environment that dropped AGENT_SECRETS_* (omp's eval kernel) still reaches the
        box's key dir: the agent-secrets client falls back to $XDG_RUNTIME_DIR/agent-secrets, so
        the wait does too, or the client would meet the marker with only its own 20 s."""
        self.marker.touch()
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            self.marker.unlink()

        stderr = self.wait(sleep, environment={"XDG_RUNTIME_DIR": self.temp_dir.name})
        self.assertEqual(len(slept), 1)
        self.assertEqual(stderr.count("still being enrolled"), 1)

    def test_a_named_key_dir_is_the_only_one_read(self):
        """The client reads the default only when AGENT_SECRETS_KEY_DIR is unset, so a marker in
        the default dir does not hold a call whose named key dir has none."""
        self.marker.touch()
        named = Path(self.temp_dir.name) / "named"
        named.mkdir()
        environment = {"AGENT_SECRETS_KEY_DIR": str(named), "XDG_RUNTIME_DIR": self.temp_dir.name}
        self.assertEqual(self.wait(self.never_sleeps, environment=environment), "")

    def test_with_neither_variable_the_key_dir_is_the_clients_last_default(self):
        """The client's last default is helper.DefaultSocket's directory under /run/user/<uid>."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(gh_app_token.box_key_dir(), Path(f"/run/user/{os.getuid()}") / "agent-secrets")



class CacheOffTheRootDisk(unittest.TestCase):
    """The cache and decisions.log live on /mnt/nvme/<user> where that disk is, seeded once from the old
    ~/.cache/gh-app-token; and a credential answer never waits for the decision log. On 2026-11-03 at
    09:50Z 428 helper processes sat writing decisions.log on the stalled root disk and every push hung."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_the_old_cache_is_copied_once_with_its_modes_and_a_note_is_left(self):
        old, new = self.root / "old", self.root / "nvme" / "gh-app-token"
        old.mkdir()
        for name in ("token-agent--app-1--acme.json", "covered-1.json"):
            (old / name).write_text("{}")
            (old / name).chmod(0o600)
        (old / "decisions.log").write_text("history\n")
        (old / "covered-1.lock").write_text("")
        gh_app_token.seed_cache_dir(new, old)
        self.assertEqual(sorted(p.name for p in new.iterdir()), ["covered-1.json", "token-agent--app-1--acme.json"])
        self.assertEqual((new / "covered-1.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(new.stat().st_mode & 0o777, 0o700)
        self.assertIn(str(new), (old / gh_app_token.MOVED_NOTE).read_text())
        self.assertEqual((old / "decisions.log").read_text(), "history\n")
        (old / "covered-1.json").write_text('{"changed": 1}')
        gh_app_token.seed_cache_dir(new, old)
        self.assertEqual((new / "covered-1.json").read_text(), "{}", "a published cache was replaced")

    def test_the_cache_is_on_nvme_only_when_that_directory_is_writable(self):
        # Inside an agent box that does not bind /mnt/nvme/<user>, the path exists as an empty
        # root-owned directory; choosing it failed every push from the box (LINUX-1274, 2026-11-04).
        from unittest import mock
        home_cache = Path.home() / ".cache" / "gh-app-token"
        with mock.patch.dict(os.environ, {"USER": "someone"}), \
                mock.patch.object(Path, "is_dir", return_value=True):
            with mock.patch.object(gh_app_token.os, "access", return_value=True):
                self.assertEqual(gh_app_token.default_cache_dir(), Path("/mnt/nvme/someone/gh-app-token"))
            with mock.patch.object(gh_app_token.os, "access", return_value=False):
                self.assertEqual(gh_app_token.default_cache_dir(), home_cache)
        with mock.patch.dict(os.environ, {"USER": "someone"}), \
                mock.patch.object(Path, "is_dir", return_value=False):
            self.assertEqual(gh_app_token.default_cache_dir(), home_cache)

    def test_the_answer_comes_while_the_decision_log_cannot_be_written(self):
        home = self.root / "home"
        cache = home / ".cache" / "gh-app-token"
        cache.mkdir(parents=True, mode=0o700)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        (cache / "token-agent--app-123--acme.json").write_text(
            json.dumps({"token": "ghs_cached", "expires_at": expires, "checked_at": checked_now()})
        )
        gitconfig = self.root / "gitconfig"
        gitconfig.write_text('[gh-app "agent"]\n\tapp-id = 123\n')
        log = cache / "decisions.log"
        os.mkfifo(log)  # opening it to append blocks until a reader comes: a log write that cannot finish
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "USER": "no-such-user-gh-app-token-test",
               "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}
        try:
            result = subprocess.run([sys.executable, str(MODULE_PATH), "agent", "--owner", "acme"], env=env,
                                    capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual((result.returncode, result.stdout), (0, "ghs_cached\n"), result.stderr)
            request = "protocol=https\nhost=github.com\npath=acme/repo.git\n\n"
            result = subprocess.run([sys.executable, str(MODULE_PATH), "agent", "get"], env=env, input=request,
                                    capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("password=ghs_cached", result.stdout)
        finally:
            # Unblock the detached log writers: each opens the FIFO, writes its line and exits.
            reader = os.open(log, os.O_RDONLY | os.O_NONBLOCK)
            deadline = time.monotonic() + 30
            lines = b""
            while time.monotonic() < deadline and lines.count(b"\n") < 2:
                try:
                    lines += os.read(reader, 65536)
                except BlockingIOError:
                    pass
                time.sleep(0.05)
            os.close(reader)
        self.assertEqual(lines.count(b"\n"), 2, "the decision lines were not written once the log could be")

    def test_the_decision_log_writer_does_not_keep_a_lock_its_caller_passed_down(self):
        # A mise that holds .dotfiles-install.lock on an inherited descriptor runs this command as
        # its GitHub credential command; the detached log writer must not keep that lock while its
        # write waits (dispatch://INFRA-4798). The pipe behind decisions.log is full and nobody
        # reads it, so the writer waits in its write with the log open: the lock is probed then,
        # and only after that is the pipe read, so the writer still writes its line.
        import fcntl
        home = self.root / "home"
        cache = home / ".cache" / "gh-app-token"
        cache.mkdir(parents=True, mode=0o700)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        (cache / "token-agent--app-123--acme.json").write_text(
            json.dumps({"token": "ghs_cached", "expires_at": expires, "checked_at": checked_now()})
        )
        gitconfig = self.root / "gitconfig"
        gitconfig.write_text('[gh-app "agent"]\n\tapp-id = 123\n')
        log = cache / "decisions.log"
        os.mkfifo(log)
        # The test's own end, read-write so its open does not wait; filled, so a write waits.
        pipe = os.open(log, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, pipe)
        try:
            while True:
                os.write(pipe, b"x" * 4096)
        except BlockingIOError:
            pass
        fifo = os.stat(log)
        lock = self.root / "install.lock"
        held = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(held, fcntl.LOCK_EX)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "USER": "no-such-user-gh-app-token-test",
               "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"}

        def writer():
            """The pid, other than this one, that has decisions.log open. Only processes of this
            test are looked at: each has the test's HOME in its environment."""
            marker = f"HOME={home}".encode()
            for pid in filter(str.isdigit, os.listdir("/proc")):
                if int(pid) == os.getpid():
                    continue
                try:
                    if marker not in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
                        continue
                    fds = os.listdir(f"/proc/{pid}/fd")
                except OSError:
                    continue
                for fd in fds:
                    try:
                        target = os.stat(f"/proc/{pid}/fd/{fd}")
                    except OSError:
                        continue
                    if (target.st_dev, target.st_ino) == (fifo.st_dev, fifo.st_ino):
                        return int(pid)
            return None

        lines = b""
        try:
            try:
                result = subprocess.run([sys.executable, str(MODULE_PATH), "agent", "--owner", "acme"], env=env,
                                        pass_fds=(held,), capture_output=True, text=True, timeout=60, check=False)
            finally:
                os.close(held)
            self.assertEqual((result.returncode, result.stdout), (0, "ghs_cached\n"), result.stderr)
            deadline = time.monotonic() + 60
            while (pid := writer()) is None:
                self.assertLess(time.monotonic(), deadline, "waited 60 s for the decision-log writer to open decisions.log")
                time.sleep(0.05)
            probe = os.open(lock, os.O_RDWR)
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                free = True
            except BlockingIOError:
                free = False
            finally:
                os.close(probe)
            self.assertTrue(free, f"the decision-log writer (pid {pid}), waiting on decisions.log, still holds the lock its caller passed down")
        finally:
            # Drain the pipe: the writer's write ends, it writes its line and exits.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and lines.count(b"\n") < 1:
                try:
                    lines += os.read(pipe, 65536)
                except BlockingIOError:
                    time.sleep(0.05)
        self.assertEqual(lines.count(b"\n"), 1, "the decision line was not written once the log could be")


class RefuseOwnerReadsOnlyTheMachinesOwnConfig(unittest.TestCase):
    """refuse-owner counts in ~/.config/git/config alone, read by the real git in a real process.
    Legion panes set XDG_CONFIG_HOME to a directory with no git config, and a plain `git config`
    then reads that directory's file in place of ~/.config/git/config: every refusal was skipped
    there (HOME-216)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.cache = self.home / ".cache" / "gh-app-token"
        self.cache.mkdir(parents=True, mode=0o700)
        self.machine_config = self.home / ".config" / "git" / "config"
        self.machine_config.parent.mkdir(parents=True)
        # The profile where the dotfiles put it, in ~/.gitconfig. A plain `git config` reads that
        # file whatever XDG_CONFIG_HOME is, and does not read ~/.config/git/config while it is set.
        (self.home / ".gitconfig").write_text(
            '[gh-app "agent"]\n'
            "\tapp-id = 123\n"
            "\tbroker-secret = GITHUB_APP_SJAWHAR\n"
            "\tfallback-secret = GH_PUBLIC_REPO_PAT\n"
        )
        # A Legion pane's XDG_CONFIG_HOME: a directory with no git/config in it.
        self.legion_xdg_config_home = self.root / "legion-xdg-config"
        self.legion_xdg_config_home.mkdir()
        stubs = self.root / "bin"
        stubs.mkdir()
        for name, contents in {"secrets": SECRETS_STUB, "secret-run": SECRET_RUN_STUB}.items():
            (stubs / name).write_text(contents)
            (stubs / name).chmod(0o700)
        self.secrets_log = self.root / "secrets.log"
        self.secret_run_log = self.root / "secret-run.log"
        self.env = {
            "PATH": f"{stubs}{os.pathsep}/usr/bin:/bin",
            "HOME": str(self.home),
            "USER": "no-such-user-gh-app-token-test",
            "GIT_CONFIG_NOSYSTEM": "1",
            "AGENT_SECRETS_KEY_DIR": str(self.root / "agent-secrets"),
            "SECRETS_STUB_LOG": str(self.secrets_log),
            "SECRET_RUN_STUB_LOG": str(self.secret_run_log),
            "SECRET_RUN_STUB_RESPONSE": BROKER_RESPONSE,
        }

    def cache_token(self, owner, token):
        (self.cache / f"token-agent--app-123--{owner}.json").write_text(
            json.dumps({"token": token, "expires_at": "2099-01-01T00:00:00Z", "checked_at": checked_now()})
        )

    def run_shim(self, env, *argv, stdin="", cwd=None):
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "agent", *argv],
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            cwd=self.root if cwd is None else cwd,
        )
        return result.returncode, result.stdout, result.stderr

    @staticmethod
    def calls(log):
        return log.read_text().splitlines() if log.exists() else []

    def test_the_machines_own_config_refuses_whether_or_not_xdg_config_home_is_set(self):
        self.machine_config.write_text(
            '[gh-app "agent"]\n'
            "\tapp-id = 123\n"
            "\trefuse-owner = trajectory-labs-pbc\n"
            "\trefuse-owner = Another-Refused-Owner\n"
        )
        for owner, token in {
            "trajectory-labs-pbc": "ghs_cached_refused",
            "another-refused-owner": "ghs_cached_second_refused",
            "allowed-owner": "ghs_cached_allowed",
        }.items():
            self.cache_token(owner, token)

        for name, env in {
            "XDG_CONFIG_HOME set, as in a Legion pane": {
                **self.env, "XDG_CONFIG_HOME": str(self.legion_xdg_config_home)
            },
            "XDG_CONFIG_HOME unset": self.env,
        }.items():
            with self.subTest(name):
                for owner in ("trajectory-labs-pbc", "another-refused-owner"):
                    self.assertEqual(
                        self.run_shim(env, "--owner", owner),
                        (1, "", GhAppTokenTests.refusal_detail("agent", owner) + "\n"),
                    )
                self.assertEqual(self.run_shim(env, "--owner", "allowed-owner"), (0, "ghs_cached_allowed\n", ""))
        self.assertEqual(self.calls(self.secrets_log), [])
        self.assertEqual(self.calls(self.secret_run_log), [])

    def test_a_refuse_owner_only_the_global_config_holds_does_not_refuse(self):
        # GIT_CONFIG_GLOBAL stands in for ~/.gitconfig, which every machine with the dotfiles reads;
        # this machine has no ~/.config/git/config, so nothing refuses the owner.
        global_config = self.root / "global-gitconfig"
        global_config.write_text(
            '[gh-app "agent"]\n'
            "\tapp-id = 123\n"
            "\trefuse-owner = trajectory-labs-pbc\n"
        )
        self.cache_token("trajectory-labs-pbc", "ghs_cached_trajectory")

        self.assertEqual(
            self.run_shim({**self.env, "GIT_CONFIG_GLOBAL": str(global_config)}, "--owner", "trajectory-labs-pbc"),
            (0, "ghs_cached_trajectory\n", ""),
        )

    def test_a_machine_config_git_cannot_read_refuses_every_owner(self):
        # The section header never closes, so git exits 128 on the file. XDG_CONFIG_HOME is set, as
        # in a Legion pane, so the profile's other keys still come from ~/.gitconfig: reading the
        # broken file as "nothing refused" would go on to the broker for sjawhar.
        self.machine_config.write_text('[gh-app "agent"\n\trefuse-owner = trajectory-labs-pbc\n')
        env = {**self.env, "XDG_CONFIG_HOME": str(self.legion_xdg_config_home)}
        failure = (
            f"gh-app-token: failed to read gh-app.agent.refuse-owner from {self.machine_config}, "
            "so every owner is refused\n"
        )

        direct = self.run_shim(env, "--owner", "sjawhar")
        credential = self.run_shim(
            env, "get", stdin="protocol=https\nhost=github.com\npath=sjawhar/dotfiles.git\n\n"
        )

        self.assertEqual(direct, (1, "", failure))
        self.assertEqual(credential, (1, "quit=1\n", failure))
        self.assertEqual(self.calls(self.secrets_log), [])
        self.assertEqual(self.calls(self.secret_run_log), [])
        self.assertEqual([path.name for path in self.cache.iterdir() if path.name != "decisions.log"], [])

    @unittest.skipIf(os.geteuid() == 0, "root reads a file whatever its mode")
    def test_a_machine_config_the_agent_cannot_read_refuses_every_owner(self):
        # A well-formed file that names the refused owner, but that this user may not read. git
        # warns "unable to access … Permission denied" and exits 1 with no output, the same status
        # as a missing file or key, so reading that status as "nothing refused" served the refused
        # owner's cached token. The docstring promises that a ~/.config/git/config git cannot read
        # refuses every owner (HOME-216 test round 2).
        self.machine_config.write_text('[gh-app "agent"]\n\trefuse-owner = trajectory-labs-pbc\n')
        self.machine_config.chmod(0)
        self.addCleanup(self.machine_config.chmod, 0o600)
        self.cache_token("trajectory-labs-pbc", "ghs_cached_refused")
        env = {**self.env, "XDG_CONFIG_HOME": str(self.legion_xdg_config_home)}
        failure = (
            f"gh-app-token: failed to read gh-app.agent.refuse-owner from {self.machine_config}, "
            "so every owner is refused\n"
        )

        direct = self.run_shim(env, "--owner", "trajectory-labs-pbc")
        credential = self.run_shim(
            env, "get", stdin="protocol=https\nhost=github.com\npath=trajectory-labs-pbc/agent-c.git\n\n"
        )

        self.assertEqual(direct, (1, "", failure))
        self.assertEqual(credential, (1, "quit=1\n", failure))
        self.assertEqual(self.calls(self.secrets_log), [])
        self.assertEqual(self.calls(self.secret_run_log), [])

    def test_a_machine_config_this_process_cannot_see_refuses_every_owner(self):
        # git exits 1 with nothing on stdout for a dangling link at the path, and for a file or a
        # directory it may not open (with only a warning on stderr): the same status as no file at
        # all. So each is decided before git runs. A file git cannot parse is its exit 128. In each
        # case the config names the refused owner and that owner's token is cached, so reading the
        # case as "nothing refused" serves it.
        refusing = '[gh-app "agent"]\n\trefuse-owner = trajectory-labs-pbc\n'
        git_dir = self.machine_config.parent

        def unreadable_file():
            self.machine_config.write_text(refusing)
            self.machine_config.chmod(0)

        def unsearchable_directory():
            self.machine_config.write_text(refusing)
            git_dir.chmod(0)

        def dangling_link():
            # The file the link named, which held the refusal, is gone.
            self.machine_config.symlink_to(self.root / "removed-config")

        def parse_error():
            self.machine_config.write_text('[gh-app "agent"\n\trefuse-owner = trajectory-labs-pbc\n')

        self.cache_token("trajectory-labs-pbc", "ghs_cached_refused")
        self.cache_token("allowed-owner", "ghs_cached_allowed")
        env = {**self.env, "XDG_CONFIG_HOME": str(self.legion_xdg_config_home)}
        failure = (
            f"gh-app-token: failed to read gh-app.agent.refuse-owner from {self.machine_config}, "
            "so every owner is refused\n"
        )

        for name, (arrange, needs_file_modes) in {
            "a file this user may not read (mode 000)": (unreadable_file, True),
            "an unsearchable ~/.config/git (mode 000)": (unsearchable_directory, True),
            "a dangling link at ~/.config/git/config": (dangling_link, False),
            "a file git cannot parse": (parse_error, False),
        }.items():
            with self.subTest(name):
                if needs_file_modes and os.geteuid() == 0:
                    self.skipTest("root reads a file whatever its mode")
                try:
                    arrange()
                    answers = {
                        "refused owner": self.run_shim(env, "--owner", "trajectory-labs-pbc"),
                        "allowed owner": self.run_shim(env, "--owner", "allowed-owner"),
                        "credential mode": self.run_shim(
                            env,
                            "get",
                            stdin="protocol=https\nhost=github.com\npath=trajectory-labs-pbc/agent-c.git\n\n",
                        ),
                    }
                finally:
                    git_dir.chmod(0o700)
                    self.machine_config.unlink(missing_ok=True)
                self.assertEqual(
                    answers,
                    {
                        "refused owner": (1, "", failure),
                        "allowed owner": (1, "", failure),
                        "credential mode": (1, "quit=1\n", failure),
                    },
                )
        self.assertEqual(self.calls(self.secrets_log), [])
        self.assertEqual(self.calls(self.secret_run_log), [])

    def test_without_a_refuse_owner_for_the_profile_no_owner_is_refused(self):
        # Two machine configs refuse no owner: none at all, which is decided before git runs, and one
        # that refuses an owner for another profile only, for which git exits 1 with nothing on stdout.
        # What git writes on stderr then says nothing about the file: GIT_TRACE traces every call
        # there, and a git wrapper on PATH may write there too. A repository the caller stands in is
        # not the machine, so a refuse-owner in its config refuses nothing.
        key = "gh-app.agent.refuse-owner"
        self.cache_token("trajectory-labs-pbc", "ghs_cached_trajectory")
        self.cache_token("allowed-owner", "ghs_cached_allowed")
        real_git = shutil.which("git", path=self.env["PATH"])
        self.assertIsNotNone(real_git)
        noisy_bin = self.root / "noisy-git-bin"
        noisy_bin.mkdir()
        (noisy_bin / "git").write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' 'noisy-git: a wrapper line on stderr' >&2\n"
            f'exec "{real_git}" "$@"\n'
        )
        (noisy_bin / "git").chmod(0o700)
        repository = self.root / "repository"
        subprocess.run([real_git, "init", "--quiet", str(repository)], env=self.env, capture_output=True, check=True)
        subprocess.run(
            [real_git, "config", "--file", str(repository / ".git" / "config"), "--add", key, "trajectory-labs-pbc"],
            env=self.env,
            capture_output=True,
            check=True,
        )
        # A plain `git config` in the repository reads the refusal: the repository is valid.
        in_repository = subprocess.run(
            [real_git, "config", "--get-all", key], env=self.env, cwd=repository, capture_output=True, text=True
        )
        self.assertEqual((in_repository.returncode, in_repository.stdout), (0, "trajectory-labs-pbc\n"))

        cases = {
            "plain environment": (self.env, self.root, False),
            "GIT_TRACE=1": ({**self.env, "GIT_TRACE": "1"}, self.root, True),
            "a git on PATH that writes on stderr": (
                {**self.env, "PATH": f"{noisy_bin}{os.pathsep}{self.env['PATH']}"}, self.root, True
            ),
            "run in a repository whose config refuses the owner": (self.env, repository, False),
        }
        for config, contents in {
            "no ~/.config/git/config": None,
            "a ~/.config/git/config that refuses the owner for another profile only": (
                '[gh-app "review"]\n\trefuse-owner = trajectory-labs-pbc\n'
            ),
        }.items():
            if contents is None:
                self.assertFalse(os.path.lexists(self.machine_config))
            else:
                self.machine_config.write_text(contents)
            for name, (env, cwd, git_writes_stderr) in cases.items():
                with self.subTest(config=config, case=name):
                    # The lookup the shim makes, by the git the shim finds: it writes on stderr or not.
                    lookup = subprocess.run(
                        ["git", "config", "--file", str(self.machine_config), "--get-all", key],
                        env=env,
                        cwd=cwd,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        (lookup.returncode, lookup.stdout, bool(lookup.stderr)),
                        (1, "", git_writes_stderr),
                        lookup.stderr,
                    )
                    self.assertEqual(
                        self.run_shim(env, "--owner", "trajectory-labs-pbc", cwd=cwd),
                        (0, "ghs_cached_trajectory\n", ""),
                    )
                    self.assertEqual(
                        self.run_shim(env, "--owner", "allowed-owner", cwd=cwd), (0, "ghs_cached_allowed\n", "")
                    )
        self.assertEqual(self.calls(self.secrets_log), [])
        self.assertEqual(self.calls(self.secret_run_log), [])


if __name__ == "__main__":
    unittest.main()
