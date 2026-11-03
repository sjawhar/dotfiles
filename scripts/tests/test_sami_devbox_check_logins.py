#!/usr/bin/env python3
"""Tests for devbox/sami-devbox-check's legacy_is_keyless: which gcloud legacy_credentials
directories are a note rather than a FAIL in A2's login check.

A directory passes only when every credential in it is a keyless external_account: an adc.json of
type external_account with no private_key and no refresh_token, and a .boto that only names such a
file. A person's login (authorized_user, a .boto with a refresh token), a service account key
(service_account, with a private_key) and any other file fail (dispatch://INFRA-993). The function
is run from the real script, with a `sudo` on PATH that runs its command as the caller.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "sami-devbox-check"
FAKE_SUDO = """#!/bin/sh
[ "$1" = -n ] && shift
exec "$@"
"""
EXTERNAL = {
    "type": "external_account",
    "audience": "//iam.googleapis.com/projects/805240912354/locations/global/workloadIdentityPools/azure/providers/entra",
    "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
    "token_url": "https://sts.googleapis.com/v1/token",
    "credential_source": {"file": "/run/token", "format": {"type": "json", "subject_token_field_name": "access_token"}},
    "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/x:generateAccessToken",
}
EXTERNAL_BOTO = "[Credentials]\ngs_external_account_file = /home/sami/x/adc.json\n"


class LegacyIsKeylessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "sudo").write_text(FAKE_SUDO)
        (bin_dir / "sudo").chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")

    def legacy(self, files: dict[str, str]) -> Path:
        d = self.root / f"case{len(list(self.root.iterdir()))}" / "legacy_credentials"
        acct = d / "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
        acct.mkdir(parents=True)
        for name, text in files.items():
            (acct / name).write_text(text)
        return d

    def keyless(self, d: Path) -> bool:
        body = f'source <(sed \'/^case "${{1:-}}" in/,$d\' {SCRIPT}); legacy_is_keyless "$1"'
        return subprocess.run(["bash", "-c", body, "test", str(d)], env=self.env, timeout=30).returncode == 0

    def test_keyless_external_account_passes(self) -> None:
        self.assertTrue(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL)})))
        self.assertTrue(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL), ".boto": EXTERNAL_BOTO})))

    def test_service_account_key_fails(self) -> None:
        key = {"type": "service_account", "client_email": "x@p.iam.gserviceaccount.com", "private_key": "-----BEGIN PRIVATE KEY-----\n"}
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(key)})))
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps({"type": "service_account"})})))
        boto = "[Credentials]\ngs_service_key_file = /x/key.json\n"
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL), ".boto": boto})))

    def test_person_login_fails(self) -> None:
        user = {"type": "authorized_user", "client_id": "c", "client_secret": "s", "refresh_token": "1//x"}
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(user)})))
        boto = "[Credentials]\ngs_oauth2_refresh_token = 1//x\n"
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL), ".boto": boto})))

    def test_external_account_with_a_key_or_token_fails(self) -> None:
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(dict(EXTERNAL, private_key="k"))})))
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(dict(EXTERNAL, refresh_token="t"))})))

    def test_any_other_file_fails(self) -> None:
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL), "key.p12": "x"})))
        self.assertFalse(self.keyless(self.legacy({"adc.json": json.dumps(EXTERNAL), ".boto": EXTERNAL_BOTO + "gs_json_host = x\n"})))


if __name__ == "__main__":
    unittest.main()
