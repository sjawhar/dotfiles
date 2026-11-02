#!/usr/bin/env python3
"""scripts/agent-session points Google's application default credentials (ADC) at the box identity.

Without GOOGLE_APPLICATION_CREDENTIALS a client library reads the well-known file
application_default_credentials.json, which holds whoever last ran an ADC login: on sami-rocqperf
the user's own login since 2026-11-01T02:53Z. A session gets the machine's external_account
configuration (box_credential_config.json, installers/gcloud-box.sh) unless its caller named a
credential file of its own; the well-known file is not such a choice.

Technique: a scratch DOTFILES_DIR whose agent-secrets-session execs its argv (as
test_agent_session_spend.py does), a scratch gcloud config dir (CLOUDSDK_CONFIG) holding a synthetic
external_account configuration and a synthetic user login as the well-known file, and an agent that
is a Python with google-auth calling google.auth.default(), which builds both kinds offline. The
Google-VM cases run the real scripts/on-google-vm against gce_metadata_stub.py, a stand-in metadata
server that GCE_METADATA_HOST and GCE_METADATA_IP name (every other case names a closed port, so no
case depends on the machine the suite runs on). The live case runs the same agent against this
machine's own gcloud config dir and mints a token, printing only the account Google's tokeninfo
names; it is skipped where the machine has no box identity.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from gce_metadata_stub import MetadataStub

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "agent-session"
BOX_ACCOUNT = "agent@example-project.iam.gserviceaccount.com"
LIVE_ACCOUNT = "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
LIVE_GCLOUD = Path(os.environ.get("HOME", "/nonexistent")) / ".config" / "gcloud"

AGENT = """
import json, os, sys
import google.auth
# Scopes only for the live refresh: given scopes, default() asks STS for an external_account's
# project, a network call the offline cases must not make.
scopes = ["https://www.googleapis.com/auth/cloud-platform",
          "https://www.googleapis.com/auth/userinfo.email"] if "--refresh" in sys.argv else None
creds, _ = google.auth.default(scopes=scopes)
out = {"kind": type(creds).__module__ + "." + type(creds).__name__,
       "file": os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
       "service_account_email": getattr(creds, "service_account_email", None)}
# Mint only as a machine identity: a user login here would be the user's own.
if "--refresh" in sys.argv and out["kind"] == "google.auth.identity_pool.Credentials":
    import requests
    from google.auth.transport.requests import Request
    creds.refresh(Request())
    info = requests.get("https://oauth2.googleapis.com/tokeninfo",
                        params={"access_token": creds.token}, timeout=60).json()
    out["tokeninfo_email"] = info.get("email")
print(json.dumps(out))
"""
EXTERNAL = "google.auth.identity_pool.Credentials"
USER = "google.oauth2.credentials.Credentials"
GCE = "google.auth.compute_engine.credentials.Credentials"


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def user_login(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "authorized_user", "client_id": "fake.apps.googleusercontent.com",
                                "client_secret": "fake", "refresh_token": "fake"}))


class AgentSessionGoogleAdcTest(unittest.TestCase):
    python: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.venv = Path(tempfile.mkdtemp(prefix="agent-session-adc-venv-"))
        uv = shutil.which("uv")
        if uv is None:
            raise unittest.SkipTest("no uv to build a Python with google-auth")
        subprocess.run([uv, "venv", "-q", str(cls.venv / "py")], check=True)
        cls.python = cls.venv / "py" / "bin" / "python3"
        subprocess.run([uv, "pip", "install", "-q", "--python", str(cls.python), "google-auth[requests]"], check=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.venv, ignore_errors=True)

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-session-adc-"))
        self.addCleanup(shutil.rmtree, self.root)
        dotfiles = self.root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        (dotfiles / "shims").mkdir()
        executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        executable(dotfiles / "scripts" / "gh-agent-config", "echo \"$HOME/.config/gh-agent\"")
        (dotfiles / "scripts" / "on-google-vm").symlink_to(DOTFILES / "scripts" / "on-google-vm")
        shutil.copy(DOTFILES / "scripts" / "agent-spend-env.sh", dotfiles / "scripts" / "agent-spend-env.sh")
        self.gcloud = self.root / "gcloud"
        self.gcloud.mkdir()
        subject = self.root / "subject-token"
        subject.write_text("fake")
        self.box = self.gcloud / "box_credential_config.json"
        self.box.write_text(json.dumps({
            "type": "external_account",
            "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/p/providers/q",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "token_url": "https://sts.googleapis.com/v1/token",
            "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/"
                                                 f"serviceAccounts/{BOX_ACCOUNT}:generateAccessToken",
            "credential_source": {"file": str(subject)},
        }))
        self.well_known = self.gcloud / "application_default_credentials.json"
        user_login(self.well_known)
        self.base = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "DOTFILES_DIR": str(dotfiles),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "CLOUDSDK_CONFIG": str(self.gcloud),
            # Not a Google VM unless a case says so: a closed port (scripts/on-google-vm).
            "GCE_METADATA_HOST": "127.0.0.1:9",
            "GCE_METADATA_IP": "127.0.0.1:9",
            "GCE_METADATA_TIMEOUT": "1",
        }

    def agent(self, env: dict[str, str], *args: str, through_session: bool = True) -> dict[str, str | None]:
        argv = [str(self.python), "-c", AGENT, *args]
        if through_session:
            argv = [str(SCRIPT), *argv]
        result = subprocess.run(argv, env={**self.base, **env}, cwd=self.root, capture_output=True, text=True,
                                timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.splitlines()[-1])

    def test_without_the_session_the_well_known_file_is_the_users_login(self) -> None:
        # Control: what an agent process got before this change.
        self.assertEqual(self.agent({}, through_session=False)["kind"], USER)

    def test_a_session_acts_as_the_box_identity(self) -> None:
        out = self.agent({})
        self.assertEqual((out["kind"], out["service_account_email"]), (EXTERNAL, BOX_ACCOUNT))
        self.assertEqual(out["file"], str(self.box))

    def test_the_well_known_file_named_by_a_caller_is_replaced(self) -> None:
        for named in (str(self.well_known), f"{self.gcloud}/./application_default_credentials.json"):
            out = self.agent({"GOOGLE_APPLICATION_CREDENTIALS": named})
            self.assertEqual((out["kind"], out["file"]), (EXTERNAL, str(self.box)), named)

    def test_a_callers_own_file_is_kept(self) -> None:
        own = self.gcloud / "legacy_credentials" / "someone@example.com" / "adc.json"
        user_login(own)
        out = self.agent({"GOOGLE_APPLICATION_CREDENTIALS": str(own)})
        self.assertEqual((out["kind"], out["file"]), (USER, str(own)))

    def test_a_machine_without_the_box_identity_is_left_alone(self) -> None:
        self.box.unlink()
        out = self.agent({})
        self.assertEqual((out["kind"], out["file"]), (USER, None))

    def google_vm(self) -> dict[str, str]:
        """The environment of a session on a Google VM: the stub is its metadata server."""
        stub = MetadataStub.start()
        self.addCleanup(stub.stop)
        return {"GCE_METADATA_HOST": stub.host, "GCE_METADATA_IP": stub.host}

    def test_a_google_vm_session_exports_no_credential_file(self) -> None:
        """A box_credential_config.json carried over from an Azure machine fetches its token from
        Azure's IMDS, which a Google VM has not; the session leaves the variable unset, and with no
        well-known file (installers/gcloud-box.sh removes it there) ADC is the metadata server."""
        self.well_known.unlink()
        out = self.agent(self.google_vm())
        self.assertEqual((out["kind"], out["file"]), (GCE, None))

    def test_a_google_vm_session_keeps_a_callers_own_file(self) -> None:
        own = self.gcloud / "legacy_credentials" / "someone@example.com" / "adc.json"
        user_login(own)
        out = self.agent({**self.google_vm(), "GOOGLE_APPLICATION_CREDENTIALS": str(own)})
        self.assertEqual((out["kind"], out["file"]), (USER, str(own)))

    @unittest.skipUnless((LIVE_GCLOUD / "box_credential_config.json").is_file(), "this machine has no box identity")
    def test_live_this_machines_session_mints_as_the_box_account(self) -> None:
        # The machine's own gcloud config dir, read only: google-auth writes nothing there.
        out = self.agent({"CLOUDSDK_CONFIG": str(LIVE_GCLOUD)}, "--refresh")
        self.assertEqual(out["kind"], EXTERNAL)
        self.assertEqual((out["service_account_email"], out["tokeninfo_email"]), (LIVE_ACCOUNT, LIVE_ACCOUNT))


if __name__ == "__main__":
    unittest.main()
