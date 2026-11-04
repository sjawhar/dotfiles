#!/usr/bin/env python3
"""The local metadata service supplies tokens, not a Google VM identity."""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gcloud-metadata-emulator"


def load():
    loader = importlib.machinery.SourceFileLoader("metadata_emulator", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class MetadataEmulatorTest(unittest.TestCase):
    def setUp(self):
        self.module = load()
        self.server = self.module.server(0)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.base = f"http://{self.host}/computeMetadata/v1/instance/service-accounts/default/"

    def get(self, suffix, headers=None):
        request = urllib.request.Request(self.base + suffix, headers=headers or {"Metadata-Flavor": "Google"})
        return urllib.request.urlopen(request, timeout=2)

    def test_token_is_json_and_never_a_subprocess_argument(self):
        with patch.object(self.module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "synthetic-token\n")) as run:
            with self.get("token") as response:
                self.assertEqual(response.headers["Metadata-Flavor"], "Google")
                token = json.load(response)
                self.assertEqual(token["access_token"], "synthetic-token")
                self.assertEqual(token["token_type"], "Bearer")
                self.assertGreater(token["expires_in"], 0)
                self.assertLessEqual(token["expires_in"], 1200)
            self.assertEqual(run.call_args.args[0], [str(ROOT / "scripts" / "gcloud-token-fast")])

    def test_email_and_index_have_metadata_header(self):
        with patch.object(self.module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "box@example.iam.gserviceaccount.com\n")) as run:
            with self.get("email") as response:
                self.assertEqual(response.headers["Metadata-Flavor"], "Google")
                self.assertEqual(response.read(), b"box@example.iam.gserviceaccount.com\n")
            self.assertEqual(run.call_args.args[0], ["gcloud", "config", "get-value", "account"])
        with self.get("") as response:
            self.assertEqual(response.headers["Metadata-Flavor"], "Google")
            self.assertEqual(response.read(), b"email\ntoken\n")

    def test_missing_credential_is_an_http_error(self):
        with patch.object(self.module.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["token"])):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get("token")
            self.assertEqual(caught.exception.code, 503)
            self.assertNotIn(b"access_token", caught.exception.read())

    def test_unusable_success_output_is_a_credential_error(self):
        for leaf in ("token", "email"):
            for value in ("", "\n", "(unset)\n"):
                with self.subTest(leaf=leaf, value=value), patch.object(
                    self.module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, value)):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        self.get(leaf)
                    self.assertEqual(caught.exception.code, 503)
                    self.assertNotIn(b"access_token", caught.exception.read())

    def test_missing_command_and_timeout_are_credential_errors(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("synthetic-command", 30)):
            with self.subTest(error=type(error).__name__), patch.object(self.module.subprocess, "run", side_effect=error):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.get("token")
                self.assertEqual(caught.exception.code, 503)

    def test_parent_index_lists_the_default_account(self):
        request = urllib.request.Request(
            f"http://{self.host}/computeMetadata/v1/instance/service-accounts/",
            headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(request, timeout=2) as response:
            self.assertEqual(response.headers["Metadata-Flavor"], "Google")
            self.assertEqual(response.read(), b"default/\n")

    def test_missing_metadata_header_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("token", {"Accept": "application/json"})
        self.assertEqual(caught.exception.code, 403)

    def test_it_binds_only_loopback_and_does_not_claim_to_be_a_vm(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        result = subprocess.run([str(ROOT / "scripts" / "on-google-vm")], env={**os.environ, "GCE_METADATA_HOST": self.host}, capture_output=True)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
