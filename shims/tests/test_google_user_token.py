#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-auth>=2.38", "requests>=2.32", "boto3>=1.34"]
# ///
import importlib.machinery
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from botocore.exceptions import ClientError

SHIM = Path(__file__).resolve().parent.parent / "google-user-token"
spec = importlib.util.spec_from_loader(
    "google_user_token", importlib.machinery.SourceFileLoader("google_user_token", str(SHIM))
)
gut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gut)

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
