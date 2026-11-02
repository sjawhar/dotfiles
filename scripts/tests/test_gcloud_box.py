#!/usr/bin/env python3
"""installers/gcloud-box.sh on a Google VM: gcloud and ADC use the attached account, no file.

Technique: the real installer and the real Google Cloud SDK, against a scratch gcloud config dir
(CLOUDSDK_CONFIG) and gce_metadata_stub.py, a stand-in metadata server that GCE_METADATA_HOST
(the installer, scripts/on-google-vm), GCE_METADATA_ROOT (gcloud) and GCE_METADATA_IP name. The
Azure IMDS endpoint (GCLOUD_BOX_IMDS_URL) is a closed port, so the Azure branch never runs here and
the machine's own identity is never read. A config dir carried over from an Azure machine is
seeded the way gcloud stores it: credentials.db rows of the shape store.py writes, the
external_account box_credential_config.json and the same file as the well-known ADC. Skipped
where the SDK is not installed.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from gce_metadata_stub import ACCOUNT, STUB_BEARER, MetadataStub

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "gcloud-box.sh"
SDK_BIN = Path(os.environ.get("HOME", "/nonexistent")) / ".gcloud" / "google-cloud-sdk" / "bin"
PERSON = "someone@example.com"
EXTERNAL = {"type": "external_account", "audience": "//iam.googleapis.com/projects/1/locations/global/"
            "workloadIdentityPools/azure/providers/entra", "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "token_url": "https://sts.googleapis.com/v1/token",
            "credential_source": {"url": "http://169.254.169.254/metadata/identity/oauth2/token",
                                  "headers": {"Metadata": "True"},
                                  "format": {"type": "json", "subject_token_field_name": "access_token"}}}
USER_LOGIN = {"type": "authorized_user", "client_id": "fake.apps.googleusercontent.com",
              "client_secret": "fake", "refresh_token": "fake"}


@unittest.skipUnless((SDK_BIN / "gcloud").is_file(), "no Google Cloud SDK at ~/.gcloud")
class GcloudBoxGoogleVm(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="gcloud-box-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.config = self.root / "gcloud"
        self.stub = MetadataStub.start()
        self.addCleanup(self.stub.stop)
        self.env = {
            "HOME": str(self.root / "home"),
            "PATH": f"{SDK_BIN}:/usr/bin:/bin",
            "CLOUDSDK_CONFIG": str(self.config),
            "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "1",
            "GCE_METADATA_HOST": self.stub.host,
            "GCE_METADATA_ROOT": self.stub.host,
            "GCE_METADATA_IP": self.stub.host,
            "GCLOUD_BOX_IMDS_URL": "http://127.0.0.1:9",
        }

    def run_installer(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(INSTALLER)], env={**self.env, **extra}, capture_output=True,
                              text=True, timeout=600, check=False)

    def gcloud(self, *args: str) -> str:
        result = subprocess.run(["gcloud", *args], env=self.env, capture_output=True, text=True, timeout=300,
                                check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def stored(self) -> dict[str, str]:
        db = sqlite3.connect(self.config / "credentials.db")
        try:
            return {account: json.loads(value)["type"]
                    for account, value in db.execute("SELECT account_id, value FROM credentials")}
        finally:
            db.close()

    def carried_over_from_azure(self) -> None:
        """What a copy of an Azure box's config dir holds after gcloud-box.sh's Azure branch."""
        self.config.mkdir(parents=True)
        for name in ("box_credential_config.json", "application_default_credentials.json"):
            (self.config / name).write_text(json.dumps(EXTERNAL), encoding="utf-8")
        db = sqlite3.connect(self.config / "credentials.db")
        with db:
            db.execute('CREATE TABLE "credentials" (account_id TEXT PRIMARY KEY, value BLOB)')
            db.execute("INSERT INTO credentials VALUES (?, ?)", (ACCOUNT, json.dumps(EXTERNAL)))
            db.execute("INSERT INTO credentials VALUES (?, ?)", (PERSON, json.dumps(USER_LOGIN)))
        db.close()
        tokens = sqlite3.connect(self.config / "access_tokens.db")
        with tokens:
            tokens.execute('CREATE TABLE "access_tokens" (account_id TEXT PRIMARY KEY, access_token TEXT, '
                           'token_expiry TIMESTAMP, rapt_token TEXT, id_token TEXT)')
            tokens.execute("INSERT INTO access_tokens VALUES (?, 'azure-federated', '2099-01-01 00:00:00', NULL, NULL)",
                           (ACCOUNT,))
        tokens.close()

    def assert_metadata_identity(self) -> None:
        self.assertFalse((self.config / "box_credential_config.json").exists())
        self.assertFalse((self.config / "application_default_credentials.json").exists())
        self.assertEqual((self.config / "active_config").read_text(encoding="utf-8").strip(), "box")
        box = (self.config / "configurations" / "config_box").read_text(encoding="utf-8")
        self.assertIn(f"account = {ACCOUNT}", box)
        self.assertIn("project = autoformalization", box)
        self.assertNotIn("credential_file_override", box)
        # No configuration for a person's login (INFRA-801).
        self.assertFalse((self.config / "configurations" / "config_theorem").exists())
        # gcloud acts as the VM's account with the metadata server's token.
        self.assertEqual(self.gcloud("auth", "list", "--filter=status:ACTIVE", "--format=value(account)"), ACCOUNT)
        self.assertEqual(self.gcloud("auth", "print-access-token"), STUB_BEARER)

    def test_a_fresh_vm_gets_the_metadata_identity_and_no_file(self) -> None:
        first = self.run_installer()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("gcloud-box: Google VM:", first.stdout)
        self.assertIn("configuration box account -> " + ACCOUNT, first.stdout)
        self.assertNotIn("Azure", first.stdout)
        self.assert_metadata_identity()

        again = self.run_installer()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn("->", again.stdout)
        self.assertNotIn("created", again.stdout)
        self.assertNotIn("removed", again.stdout)

    def test_a_config_dir_carried_over_from_azure_loses_only_the_box_credential(self) -> None:
        self.carried_over_from_azure()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed " + str(self.config / "box_credential_config.json"), result.stdout)
        self.assertIn("removed the stored external_account credential of " + ACCOUNT, result.stdout)
        self.assertIn("removed " + str(self.config / "application_default_credentials.json"), result.stdout)
        # The person's stored login is not the installer's to remove.
        self.assertEqual(self.stored().get(PERSON), "authorized_user")
        self.assert_metadata_identity()
        self.assertEqual(self.stored().get(ACCOUNT), "gce")

    def test_a_persons_login_as_the_well_known_file_fails_the_run_and_is_kept(self) -> None:
        self.config.mkdir(parents=True)
        adc = self.config / "application_default_credentials.json"
        adc.write_text(json.dumps(USER_LOGIN), encoding="utf-8")
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("type 'authorized_user'", result.stderr)
        self.assertEqual(json.loads(adc.read_text(encoding="utf-8")), USER_LOGIN)

    def test_a_machine_that_is_neither_changes_nothing(self) -> None:
        result = self.run_installer(GCE_METADATA_HOST="127.0.0.1:9")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not a Google VM, and no box-sjawhar managed identity", result.stdout)
        self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()
