#!/usr/bin/env python3
"""google-user-token: scope expansion, config loading, and the WIF/DWD delegation.

The shim imports google-auth and boto3, which only its own `# /// script` header declares. Like
the browser-capture tests, these run with the interpreter of the environment uv builds from that
header: discovered or run directly, this file is one test that runs itself there, where it
tests the shim in-process.
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

SHIM = Path(__file__).resolve().parent.parent / "google-user-token"
# Set for the run in the shim's environment, so that run tests the shim instead of delegating.
IN_SHIM_ENVIRONMENT = "GOOGLE_USER_TOKEN_TESTS_IN_SHIM_ENV"

if os.environ.get(IN_SHIM_ENVIRONMENT):
    from botocore.exceptions import ClientError

    spec = importlib.util.spec_from_loader(
        "google_user_token", importlib.machinery.SourceFileLoader("google_user_token", str(SHIM))
    )
    gut = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gut)


def uv(*args: str) -> str:
    result = subprocess.run(["uv", *args], capture_output=True, text=True, timeout=300, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"uv {' '.join(args)} exited {result.returncode}: {result.stderr}")
    return result.stdout


def script_python(script: Path) -> str:
    """The interpreter uv builds for `script` from its `# /// script` header."""
    # `find` only locates the environment; with none built it names a bare interpreter.
    uv("sync", "--quiet", "--script", str(script))
    return uv("python", "find", "--script", str(script)).strip()


def run_in_shim_environment() -> None:
    result = subprocess.run(
        [script_python(SHIM), __file__, "-v"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env={**os.environ, IN_SHIM_ENVIRONMENT: "1"},
    )
    if result.returncode != 0:
        raise AssertionError(f"exited {result.returncode} in the shim's environment:\n{result.stderr}{result.stdout}")


def load_tests(loader, tests, pattern):
    if os.environ.get(IN_SHIM_ENVIRONMENT):
        return tests
    return unittest.TestSuite(
        [unittest.FunctionTestCase(run_in_shim_environment, description="google-user-token tests, in the shim's uv environment")]
    )


CONFIG = {
    "sa_email": "workspace-broker@example.test",
    "default_scopes": ["https://www.googleapis.com/auth/drive"],
    "adc": {"type": "external_account"},
}


class ScopeExpansion(unittest.TestCase):
    def test_bare_scope_expands(self):
        self.assertEqual(gut.expand_scopes("drive"), ("https://www.googleapis.com/auth/drive",))

    def test_full_https_scope_passes_through(self):
        scope = "https://example.test/custom"
        self.assertEqual(gut.expand_scopes(scope), (scope,))

    def test_non_https_scope_url_fails(self):
        with self.assertRaises(gut.UserTokenError):
            gut.expand_scopes("http://example.test/custom")

    def test_empty_scope_list_fails(self):
        with self.assertRaises(gut.UserTokenError):
            gut.expand_scopes(" , ")


class ConfigLoading(unittest.TestCase):
    def setUp(self):
        self.client = mock.Mock()
        self.boto3 = mock.Mock()
        self.boto3.client.return_value = self.client
        self.boto3_patch = mock.patch.object(gut, "boto3", self.boto3)
        self.boto3_patch.start()

    def tearDown(self):
        self.boto3_patch.stop()

    def test_invalid_config_shapes_fail(self):
        for invalid in (
            {**CONFIG, "sa_email": None},
            {**CONFIG, "default_scopes": []},
            {**CONFIG, "adc": {"type": "service_account"}},
        ):
            with self.subTest(invalid=invalid):
                self.client.get_secret_value.return_value = {"SecretString": json.dumps(invalid)}
                with self.assertRaises(gut.UserTokenError):
                    gut.load_config()

    def test_missing_secret_is_actionable(self):
        self.client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "GetSecretValue"
        )
        with self.assertRaisesRegex(gut.UserTokenError, "apply the production Pulumi stack"):
            gut.load_config()

    def test_access_denied_is_actionable(self):
        self.client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException"}}, "GetSecretValue"
        )
        with self.assertRaisesRegex(gut.UserTokenError, "admin-tier devbox"):
            gut.load_config()


class Delegation(unittest.TestCase):
    def test_main_defaults_subject_from_imds(self):
        with (
            mock.patch.object(gut, "load_config", return_value=CONFIG),
            mock.patch.object(gut, "_imds", side_effect=("imds-token", "owner@example.test")),
            mock.patch.object(gut, "mint_token", return_value="token") as mint_token,
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(gut.main([]), 0)
        mint_token.assert_called_once_with(
            "owner@example.test", tuple(CONFIG["default_scopes"]), CONFIG
        )

    def test_native_google_auth_delegation_is_used(self):
        source = mock.Mock()
        delegated = mock.Mock(token="token")
        with (
            mock.patch.object(gut.gauth_aws.Credentials, "from_info", return_value=source),
            mock.patch.object(gut.impersonated_credentials, "Credentials", return_value=delegated) as credentials,
            mock.patch.object(gut, "Request", return_value=mock.Mock()),
        ):
            self.assertEqual(gut.mint_token("owner@example.test", ("scope",), CONFIG), "token")
        credentials.assert_called_once_with(
            source_credentials=source,
            target_principal=CONFIG["sa_email"],
            target_scopes=("scope",),
            subject="owner@example.test",
        )
        delegated.refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
