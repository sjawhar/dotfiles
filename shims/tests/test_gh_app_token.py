#!/usr/bin/env python3
import importlib.util
import io
import json
import os
import sys
import threading
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
BROKER_RESPONSE = json.dumps(
    {
        "installations": {
            "sjawhar": {
                "installation_id": 111,
                "token": "broker-token-sjawhar",
                "expires_at": "2099-01-01T00:00:00Z",
            },
            "trajectory-labs-pbc": {
                "installation_id": 222,
                "token": "broker-token-trajectory",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        }
    }
)
BROKER_RESPONSE_WITHOUT_TRAJECTORY = json.dumps(
    {
        "installations": {
            "sjawhar": {
                "installation_id": 111,
                "token": "broker-token-sjawhar",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        }
    }
)

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
# cannot pass unnoticed.
SECRET_RUN_STUB = """#!/bin/sh
for arg do
  printf '%s\\n' "$arg" >> "$SECRET_RUN_STUB_LOG"
done
if [ -n "${SECRET_RUN_STUB_EXIT:-}" ]; then
  exit "$SECRET_RUN_STUB_EXIT"
fi
printf '%s' "$SECRET_RUN_STUB_RESPONSE"
"""


class GhAppTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.git_config = dict(GIT_CONFIG)
        self.cache_dir = Path(self.temp_dir.name) / "cache"
        stub_dir = Path(self.temp_dir.name) / "bin"
        stub_dir.mkdir()
        for name, contents in {"secrets": SECRETS_STUB, "secret-run": SECRET_RUN_STUB}.items():
            stub = stub_dir / name
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
                    "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
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
    def broker_command(owner):
        return [
            "--reason",
            f"gh-app-token {owner}",
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
        self.assertEqual(stderr, "")
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))
        self.assertEqual(gh_app_token.get_cached_token("agent", "trajectory-labs-pbc"), "broker-token-trajectory")
        self.assertEqual(
            gh_app_token.covered_owners("3202636"),
            {"sjawhar": 111, "trajectory-labs-pbc": 222},
        )
        self.assertEqual(self.secrets_calls(), [])
        self.assertNotIn("broker-token-sjawhar", self.secret_run_calls())
        self.assertNotIn("broker-token-trajectory", self.secret_run_calls())
        self.assertNotIn(
            "broker-token-sjawhar",
            (self.cache_dir / "decisions.log").read_text(),
        )

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
        self.assertIn("METR", stderr)
        self.assertIn("personal", stderr)
        self.assertEqual(self.secrets_calls(), ["get GH_PUBLIC_REPO_PAT --value"])
        self.assertNotIn("GH_AGENT_APP_PRIVATE_KEY_B64", "\n".join(self.secrets_calls()))
        self.assertEqual(self.secret_run_calls(), self.broker_command("metr"))
        self.assertEqual(
            gh_app_token.get_cached_token("agent", "trajectory-labs-pbc"),
            "broker-token-trajectory",
        )
        self.assertEqual(
            gh_app_token.covered_owners("3202636"),
            {"sjawhar": 111, "trajectory-labs-pbc": 222},
        )

    def test_covered_owner_missing_from_later_broker_release_refuses_without_fallback(self):
        # This catches shrinking the historical coverage map and acting as the
        # human when a previously covered owner is omitted by the broker.
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

    def test_concurrent_cache_misses_never_drop_historical_coverage(self):
        # Two cache misses that each receive a different release must serialize
        # their coverage read-merge-write. The barriers make the old unlocked
        # code perform both reads before either write.
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
                side_effect=lambda secret_name, owner: releases[owner],
            ),
            patch.object(gh_app_token, "covered_owners", side_effect=raced_covered_owners),
            patch.object(gh_app_token, "write_cache_file", side_effect=raced_write_cache_file),
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
        self.assertEqual(stderr, "")
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))
        self.assertEqual(self.secrets_calls(), [])

    def test_broker_pending_or_denied_names_machine_login(self):
        # This catches propagating a broker refusal without the actionable
        # machine-login remedy.
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
        rc, stdout, stderr = self.run_main(
            ["agent", "get"], stdin="host=github.com\npath=sjawhar/repo.git\n\n"
        )

        self.assertEqual(rc, 0)
        self.assertIn("username=x-access-token", stdout)
        self.assertIn("password=broker-token-sjawhar", stdout)
        self.assertEqual(stderr, "")

    def test_token_cache_is_partitioned_by_profile_and_owner(self):
        token_data = {
            "token": "cached-token",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        gh_app_token.write_cache_file(gh_app_token.token_cache_file("agent", "sjawhar"), token_data)
        self.assertEqual(gh_app_token.get_cached_token("agent", "sjawhar"), "cached-token")
        self.assertIsNone(gh_app_token.get_cached_token("agent", "trajectory-labs-pbc"))
        self.assertIsNone(gh_app_token.get_cached_token("other-profile", "sjawhar"))

    def test_a_cached_token_is_reused_only_with_the_life_the_caller_asks_for(self):
        # This catches letting a long-running gh command receive an almost-expired
        # token from cache.
        token_data = {
            "token": "cached-token",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat(),
        }
        gh_app_token.write_cache_file(gh_app_token.token_cache_file("agent", "sjawhar"), token_data)
        request = "host=github.com\npath=sjawhar/repo.git\n\n"

        _, default_answer, _ = self.run_main(["agent", "get"], stdin=request)
        with patch.dict(os.environ, {gh_app_token.MIN_LIFETIME_ENV: "1800"}):
            _, long_answer, _ = self.run_main(["agent", "get"], stdin=request)

        self.assertIn("password=cached-token", default_answer)
        self.assertIn("password=broker-token-sjawhar", long_answer)
        self.assertEqual(self.secret_run_calls(), self.broker_command("sjawhar"))
        self.assertEqual(gh_app_token.get_cached_token("agent", "sjawhar"), "broker-token-sjawhar")

    def test_a_fresh_broker_release_is_returned_when_the_request_exceeds_an_hour(self):
        # This catches looping or refusing when gh asks for longer than GitHub's
        # one-hour token lifetime.
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


if __name__ == "__main__":
    unittest.main()
