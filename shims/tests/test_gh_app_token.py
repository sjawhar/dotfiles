#!/usr/bin/env python3
import base64
import importlib.util
import io
import json
import os
import sys
import threading
import subprocess
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

# A `secrets` stand-in serves the upstream fallback and, for a machine that set key-secret,
# the App key, and records every request. Without key-secret the App key must never be
# requested from this command.
SECRETS_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$SECRETS_STUB_LOG"
case "$2" in
  GH_PUBLIC_REPO_PAT) printf '%s\\n' ghp_stub_upstream_token ;;
  GH_AGENT_APP_PRIVATE_KEY_B64) cat "$APP_KEY_B64_FILE" ;;
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


def fake_github(installations, fail=None):
    """A urlopen stand-in for GitHub's App API: the installation list and token minting.
    Records (method, url, body, Authorization) for every request."""

    def urlopen(request, timeout=None):
        urlopen.calls.append(
            (request.get_method(), request.full_url, request.data, request.get_header("Authorization"))
        )
        if fail:
            raise gh_app_token.urllib.error.HTTPError(request.full_url, fail, "error", {}, None)
        if "/app/installations?" in request.full_url:
            return io.BytesIO(
                json.dumps([{"id": i, "account": {"login": login}} for login, i in installations.items()]).encode()
            )
        installation_id = request.full_url.rsplit("/", 2)[-2]
        return io.BytesIO(json.dumps({"token": f"minted-{installation_id}", "expires_at": "2099-01-01T00:00:00Z"}).encode())

    urlopen.calls = []
    return urlopen


def b64url_decode(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class GhAppTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.git_config = dict(GIT_CONFIG)
        self.cache_dir = Path(self.temp_dir.name) / "cache"
        self.stub_dir = Path(self.temp_dir.name) / "bin"
        self.stub_dir.mkdir()
        for name, contents in {"secrets": SECRETS_STUB, "secret-run": SECRET_RUN_STUB}.items():
            stub = self.stub_dir / name
            stub.write_text(contents)
            stub.chmod(0o700)
        self.secrets_log = Path(self.temp_dir.name) / "secrets.log"
        self.secret_run_log = Path(self.temp_dir.name) / "secret-run.log"
        self.patches = [
            patch.object(gh_app_token, "CACHE_DIR", self.cache_dir),
            patch.object(gh_app_token, "git_config", side_effect=self.config_value),
            patch.object(gh_app_token, "git_config_optional", side_effect=self.git_config.get),
            patch.dict(
                os.environ,
                {
                    "PATH": f"{self.stub_dir}{os.pathsep}{os.environ['PATH']}",
                    "SECRETS_STUB_LOG": str(self.secrets_log),
                    "SECRET_RUN_STUB_LOG": str(self.secret_run_log),
                    "SECRET_RUN_STUB_RESPONSE": BROKER_RESPONSE,
                },
            ),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp_dir.cleanup()

    def config_value(self, key):
        value = self.git_config.get(key)
        if value is None:
            raise gh_app_token.AppTokenError(f"missing git config: {key}")
        return value

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

    def test_owner_never_covered_uses_only_the_fallback_pat(self):
        # This catches treating a missing installation as an App failure or
        # consulting secretsd for the broker-only App key.
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
                read_started.wait(timeout=1)
            except threading.BrokenBarrierError:
                return original_covered_owners(app_id)
            covered = original_covered_owners(app_id)
            try:
                reads_complete.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
            return covered

        def raced_write_cache_file(path, data):
            if path == coverage_file and not read_started.broken:
                if first_owner in data:
                    if not second_write.wait(timeout=1):
                        raise RuntimeError("second coverage write did not run")
                else:
                    original_write_cache_file(path, data)
                    second_write.set()
                    return
            original_write_cache_file(path, data)

        results, errors = {}, []

        def cache_miss(owner):
            try:
                results[owner] = gh_app_token.get_token("agent", owner)
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
            first.join(timeout=5)
            second.join(timeout=5)

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
                gh_app_token.get_token("agent", "sjawhar", 10**9),
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

    def use_secretsd_key(self):
        """Opt this machine in to minting with an App key from secretsd (a fresh RSA key)."""
        key = Path(self.temp_dir.name) / "app.pem"
        subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
        key_b64 = Path(self.temp_dir.name) / "app.pem.b64"
        key_b64.write_text(base64.b64encode(key.read_bytes()).decode())
        os.environ["APP_KEY_B64_FILE"] = str(key_b64)
        self.git_config["gh-app.agent.key-secret"] = "GH_AGENT_APP_PRIVATE_KEY_B64"
        return key

    def test_a_machine_with_key_secret_mints_from_secretsd_and_never_asks_the_broker(self):
        # 2026-10-31: the laptop runs no agent-secrets helper, so the broker refused every
        # session there and nothing could push. key-secret in that machine's git config mints
        # with the App key secretsd holds instead.
        key = self.use_secretsd_key()
        github = fake_github({"sjawhar": 111, "trajectory-labs-pbc": 222})
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=github):
            rc, stdout, _ = self.run_main(["agent", "--owner", "sjawhar"])

        self.assertEqual((rc, stdout), (0, "minted-111\n"))
        self.assertEqual(self.secret_run_calls(), [])
        self.assertEqual(self.secrets_calls(), ["get GH_AGENT_APP_PRIVATE_KEY_B64 --value"])
        # The bearer is a JWT for this App that verifies against the key's public half.
        jwt = github.calls[0][3].removeprefix("Bearer ")
        header, claims, signature = jwt.split(".")
        self.assertEqual(json.loads(b64url_decode(claims))["iss"], "3202636")
        public = Path(self.temp_dir.name) / "app.pub"
        subprocess.run(["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(public)], check=True, capture_output=True)
        signature_file = Path(self.temp_dir.name) / "sig"
        signature_file.write_bytes(b64url_decode(signature))
        verify = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(signature_file)],
            input=f"{header}.{claims}".encode(), capture_output=True, check=False,
        )
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        # Every installation the App lists is remembered, so a later list that drops one refuses.
        self.assertEqual(gh_app_token.covered_owners("3202636"), {"sjawhar": 111, "trajectory-labs-pbc": 222})

    def test_key_secret_mints_exactly_the_narrowing_and_an_uninstalled_owner_gets_the_fallback(self):
        self.use_secretsd_key()
        github = fake_github({"sjawhar": 111})
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=github):
            narrowed = self.run_main(["agent", "--owner", "sjawhar", "--repo", "proofs-internal", "--read-only"])
            upstream = self.run_main(["agent", "--owner", "METR"])

        self.assertEqual(narrowed[:2], (0, "minted-111\n"))
        posts = [(url, json.loads(body)) for method, url, body, _ in github.calls if method == "POST"]
        self.assertEqual(
            posts,
            [(
                "https://api.github.com/app/installations/111/access_tokens",
                {"repositories": ["proofs-internal"], "permissions": {"contents": "read", "metadata": "read"}},
            )],
        )
        self.assertEqual(upstream[:2], (0, "ghp_stub_upstream_token\n"))
        self.assertEqual(self.secret_run_calls(), [])

    def test_key_secret_github_failure_quits_and_never_reads_the_fallback(self):
        self.use_secretsd_key()
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=fake_github({}, fail=500)):
            rc, stdout, stderr = self.run_main(["agent", "get"], stdin="host=github.com\npath=sjawhar/dotfiles.git\n\n")
        self.assertEqual((rc, stdout), (1, "quit=1\n"))
        self.assertIn("returned 500", stderr)
        self.assertEqual(self.secrets_calls(), ["get GH_AGENT_APP_PRIVATE_KEY_B64 --value"])


if __name__ == "__main__":
    unittest.main()
