#!/usr/bin/env python3
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
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

INSTALLED = {"sjawhar": 111, "trajectory-labs-pbc": 222}
GIT_CONFIG = {
    "gh-app.agent.app-id": "3202636",
    "gh-app.agent.key-secret": "KEY",
    "gh-app.agent.fallback-secret": "GH_PUBLIC_REPO_PAT",
}

# A `secrets` CLI stand-in: knows one token, refuses every other name the way
# the real CLI does, and logs each invocation so tests can assert it was
# never consulted.
SECRETS_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$SECRETS_STUB_LOG"
case "$2" in
  GH_PUBLIC_REPO_PAT) echo ghp_stub_upstream_token ;;
  *) echo "secrets: secret '$2' not found" >&2; exit 1 ;;
esac
"""


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def http_404(url):
    return gh_app_token.urllib.error.HTTPError(url, 404, "Not Found", {}, None)


def fake_github(installed=INSTALLED, mint_404_ids=()):
    """A urlopen stub serving installation discovery and token minting."""

    def urlopen(request):
        url = request.full_url
        urlopen.calls.append(url)
        if "access_tokens" in url:
            installation_id = int(url.split("/installations/")[1].split("/")[0])
            if installation_id in mint_404_ids:
                raise http_404(url)
            return FakeResponse(
                {"token": f"token-{installation_id}", "expires_at": "2099-01-01T00:00:00Z"}
            )
        return FakeResponse([
            {"id": inst_id, "account": {"login": login}} for login, inst_id in installed.items()
        ])

    urlopen.calls = []
    return urlopen


class GhAppTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.git_config = dict(GIT_CONFIG)
        stub_dir = Path(self.temp_dir.name) / "bin"
        stub_dir.mkdir()
        (stub_dir / "secrets").write_text(SECRETS_STUB)
        (stub_dir / "secrets").chmod(0o700)
        self.secrets_log = Path(self.temp_dir.name) / "secrets.log"
        self.patches = [
            patch.object(gh_app_token, "CACHE_DIR", Path(self.temp_dir.name) / "cache"),
            patch.object(gh_app_token, "generate_jwt", return_value="jwt"),
            patch.object(gh_app_token, "resolve_private_key", return_value="private-key"),
            patch.object(gh_app_token, "git_config", side_effect=lambda key: self.git_config[key]),
            patch.object(gh_app_token, "git_config_optional", side_effect=self.git_config.get),
            patch.dict(
                os.environ,
                {
                    "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
                    "SECRETS_STUB_LOG": str(self.secrets_log),
                },
            ),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp_dir.cleanup()

    def secrets_calls(self):
        return self.secrets_log.read_text().splitlines() if self.secrets_log.exists() else []

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

    def test_discovery_is_cached_and_case_insensitive(self):
        urlopen = fake_github()
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=urlopen):
            self.assertEqual(
                gh_app_token.resolve_installation("3202636", "pk", "sjawhar"), (111, False)
            )
            self.assertEqual(
                gh_app_token.resolve_installation("3202636", "pk", "SJAWHAR"), (111, True)
            )
        self.assertEqual(len(urlopen.calls), 1)

    def test_stale_installation_cache_refreshes_once_before_uninstalled_result(self):
        urlopen = fake_github()
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=urlopen):
            gh_app_token.discover_installations("3202636", "pk")
            cache_file = gh_app_token.installation_cache_file("3202636")
            data = json.loads(cache_file.read_text())
            data["fetched_at"] = time.time() - gh_app_token.INSTALLATION_CACHE_TTL - 1
            cache_file.write_text(json.dumps(data))
            with self.assertRaises(gh_app_token.UninstalledOwnerError):
                gh_app_token.resolve_installation("3202636", "pk", "missing-owner")
        self.assertEqual(len(urlopen.calls), 2)

    def test_uninstalled_owner_answers_fallback_secret_in_credential_mode(self):
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=fake_github()):
            rc, stdout, stderr = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=METR/hawk.git\n\n"
            )
        self.assertEqual(rc, 0)
        self.assertIn("username=x-access-token", stdout)
        self.assertIn("password=ghp_stub_upstream_token", stdout)
        self.assertNotIn("quit=1", stdout)
        self.assertEqual(self.secrets_calls(), ["get GH_PUBLIC_REPO_PAT --value"])
        # The fallback is static: no token-cache entry is written for it.
        self.assertFalse(gh_app_token.token_cache_file("agent", "metr").exists())

    def test_uninstalled_owner_without_fallback_secret_quits(self):
        del self.git_config["gh-app.agent.fallback-secret"]
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=fake_github()):
            rc, stdout, stderr = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=METR/hawk.git\n\n"
            )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "quit=1\n")
        self.assertIn(
            "no App installation for owner METR and no fallback-secret on profile agent", stderr
        )
        self.assertEqual(self.secrets_calls(), [])

    def test_covered_owner_missing_from_a_later_discovery_refuses_rather_than_falls_back(self):
        # 2026-09-17: two review verdicts on trajectory-labs-pbc/agent-c posted as
        # the human. The App covers that owner, yet gh-app-token handed out the
        # fallback PAT - the only path there is a discovery that omits an owner
        # it listed before. That is a contradiction, not a routing decision.
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=fake_github()):
            gh_app_token.discover_installations("3202636", "pk")
        # Expire the installations cache so the next call rediscovers.
        cache = gh_app_token.installation_cache_file("3202636")
        data = json.loads(cache.read_text())
        data["fetched_at"] = 0
        cache.write_text(json.dumps(data))

        gone = fake_github(installed={"sjawhar": 111})
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=gone):
            rc, stdout, stderr = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=trajectory-labs-pbc/agent-c.git\n\n"
            )
        self.assertEqual((rc, stdout), (1, "quit=1\n"))
        self.assertIn("trajectory-labs-pbc", stderr)
        self.assertIn("refusing", stderr)
        self.assertEqual(self.secrets_calls(), [], "the fallback secret was read for a covered owner")

        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=gone):
            rc, stdout, stderr = self.run_main(["agent", "--owner", "trajectory-labs-pbc"])
        self.assertEqual((rc, stdout), (1, ""))
        self.assertEqual(self.secrets_calls(), [])

    def test_a_real_fallback_announces_the_identity_it_hands_out(self):
        # An uncovered owner legitimately gets the personal token, but never
        # silently: the caller must be able to see, on stderr, that the identity
        # about to act is the human's and not the App's.
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=fake_github()):
            rc, stdout, stderr = self.run_main(["agent", "--owner", "METR"])
        self.assertEqual((rc, stdout), (0, "ghp_stub_upstream_token\n"))
        self.assertIn("METR", stderr)
        self.assertIn("GH_PUBLIC_REPO_PAT", stderr)
        self.assertIn("personal", stderr)

    def test_uninstalled_owner_with_unreadable_fallback_secret_quits(self):
        self.git_config["gh-app.agent.fallback-secret"] = "GH_MISSING_TOKEN"
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=fake_github()):
            rc, stdout, stderr = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=METR/hawk.git\n\n"
            )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "quit=1\n")
        self.assertIn("failed to fetch secret GH_MISSING_TOKEN", stderr)
        self.assertIn("secret 'GH_MISSING_TOKEN' not found", stderr)

    def test_covered_owner_mint_failure_never_reads_fallback_secret(self):
        # A 500 on the mint (not a 404) is a genuine App failure for a covered
        # owner: quit=1, and identity never swaps to the fallback token.
        def urlopen(request):
            if "access_tokens" in request.full_url:
                raise gh_app_token.urllib.error.HTTPError(
                    request.full_url, 500, "Server Error", {}, io.BytesIO(b"boom")
                )
            return FakeResponse([{"id": 111, "account": {"login": "sjawhar"}}])

        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=urlopen):
            rc, stdout, stderr = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=sjawhar/dotfiles.git\n\n"
            )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "quit=1\n")
        self.assertIn("GitHub API 500", stderr)
        self.assertEqual(self.secrets_calls(), [])

    def test_uninstalled_owner_prints_fallback_secret_in_cli_mode(self):
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=fake_github()):
            rc, stdout, stderr = self.run_main(["agent", "--owner", "METR"])
        self.assertEqual((rc, stdout), (0, "ghp_stub_upstream_token\n"))

        del self.git_config["gh-app.agent.fallback-secret"]
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=fake_github()):
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
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=fake_github()):
            rc, stdout, _ = self.run_main(
                ["agent", "get"], stdin="host=github.com\npath=sjawhar/repo.git\n\n"
            )
        self.assertEqual(rc, 0)
        self.assertIn("username=x-access-token", stdout)
        self.assertIn("password=token-111", stdout)

    def test_token_cache_is_partitioned_by_profile_and_owner(self):
        token_data = {
            "token": "cached-token",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        gh_app_token.write_cache_file(gh_app_token.token_cache_file("agent", "sjawhar"), token_data)
        self.assertEqual(gh_app_token.get_cached_token("agent", "sjawhar"), "cached-token")
        self.assertIsNone(gh_app_token.get_cached_token("agent", "trajectory-labs-pbc"))
        self.assertIsNone(gh_app_token.get_cached_token("other-profile", "sjawhar"))

    def test_stale_cached_installation_404_rediscovers_once(self):
        # Prime the cache with an installation id that no longer mints.
        with patch.object(
            gh_app_token.urllib.request, "urlopen", side_effect=fake_github({"sjawhar": 999})
        ):
            gh_app_token.discover_installations("3202636", "pk")
        urlopen = fake_github(mint_404_ids={999})
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=urlopen):
            with redirect_stderr(io.StringIO()):
                token = gh_app_token.get_token("agent", "sjawhar")
        self.assertEqual(token, "token-111")
        # failed mint (cached 999) -> rediscovery -> successful mint (fresh 111)
        self.assertEqual(len(urlopen.calls), 3)

    def test_fresh_discovery_404_is_a_real_error(self):
        urlopen = fake_github(mint_404_ids={111})
        with patch.object(gh_app_token.urllib.request, "urlopen", side_effect=urlopen):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(gh_app_token.ApiError):
                    gh_app_token.get_token("agent", "sjawhar")

    def test_cache_directory_created_with_mode_0700(self):
        gh_app_token.write_cache_file(gh_app_token.token_cache_file("agent", "sjawhar"), {})
        self.assertEqual(gh_app_token.CACHE_DIR.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
