#!/usr/bin/env python3
"""installers/agent-secrets-signin.sh: the secrets broker's device sign-in client, for every process.

On a Google VM it reads the client's secret from Secret Manager as the account the metadata server
serves, keeps it in ~/.config/agent-secrets/signin-client-secret (0600), and sets the client's id
and that file in ~/.config/agent-secrets/config (0600), keeping every other line. A second run
changes nothing; a refused read changes nothing and exits 1; elsewhere it changes nothing. No
output carries the secret.

Technique: the real installer against gce_metadata_stub.py (GCE_METADATA_HOST) and a stand-in
Secret Manager (SIGNIN_SECRET_MANAGER_URL) that answers the one version only for the stub's bearer.
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from gce_metadata_stub import STUB_BEARER, MetadataStub

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "installers" / "agent-secrets-signin.sh"
VERSION = "/v1/projects/805240912354/secrets/broker-signin-device-client-secret/versions/1:access"
CLIENT_ID = "805240912354-85d2j2rgpbcmucack62fincm9i3jq73i.apps.googleusercontent.com"
# A planted value: the test fails if any output carries it.
SECRET = "planted-signin-secret-4f1c9a"


class SecretManagerStub(http.server.ThreadingHTTPServer):
    """Answers VERSION with SECRET for the metadata stub's bearer, and `refuse` (an HTTP status)
    for everything once it is set."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _SecretHandler)
        self.refuse = 0
        self.seen: list[tuple[str, str]] = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


class _SecretHandler(http.server.BaseHTTPRequestHandler):
    server: SecretManagerStub

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        auth = self.headers.get("Authorization", "")
        self.server.seen.append((self.path, auth))
        if self.server.refuse:
            status, body = self.server.refuse, {"error": {"code": self.server.refuse, "status": "PERMISSION_DENIED"}}
        elif self.path == VERSION and auth == f"Bearer {STUB_BEARER}":
            data = base64.b64encode(SECRET.encode()).decode()
            status, body = 200, {"name": VERSION, "payload": {"data": data}}
        else:
            status, body = 404, {"error": {"code": 404}}
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


class AgentSecretsSigninTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.conf = self.home / ".config" / "agent-secrets"
        self.config = self.conf / "config"
        self.secret_file = self.conf / "signin-client-secret"
        self.metadata = MetadataStub.start()
        self.addCleanup(self.metadata.stop)
        self.secrets = SecretManagerStub()
        self.addCleanup(self.secrets.stop)
        self.env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "GCE_METADATA_HOST": self.metadata.host,
            "SIGNIN_SECRET_MANAGER_URL": self.secrets.url,
        }

    def install(self) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["bash", str(INSTALLER)], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertNotIn(SECRET, result.stdout + result.stderr, "the output carries the secret")
        return result

    def mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_a_google_vm_gets_the_client_in_the_settings_file(self) -> None:
        self.conf.mkdir(parents=True)
        self.config.write_text("# mine\nAGENT_SECRETS_URL=https://broker.example\nAGENT_SECRETS_SIGNIN_CLIENT_ID = an-old-client\n")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.secret_file.read_text(), SECRET)
        self.assertEqual(self.mode(self.secret_file), 0o600)
        self.assertEqual(
            self.config.read_text().splitlines(),
            [
                "# mine",
                "AGENT_SECRETS_URL=https://broker.example",
                f"AGENT_SECRETS_SIGNIN_CLIENT_ID={CLIENT_ID}",
                f"AGENT_SECRETS_SIGNIN_CLIENT_SECRET_FILE={self.secret_file}",
            ],
        )
        self.assertEqual(self.mode(self.config), 0o600)
        self.assertEqual(self.mode(self.conf), 0o700)
        self.assertEqual(self.secrets.seen, [(VERSION, f"Bearer {STUB_BEARER}")])

    def test_a_second_run_changes_nothing(self) -> None:
        self.assertEqual(self.install().returncode, 0)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.config, self.secret_file)}
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}, before)
        self.assertNotIn("wrote", result.stdout)

    def test_a_refused_read_changes_nothing(self) -> None:
        self.conf.mkdir(parents=True)
        self.config.write_text("AGENT_SECRETS_URL=https://broker.example\n")
        self.secrets.refuse = 403
        result = self.install()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("403", result.stderr)
        self.assertFalse(self.secret_file.exists())
        self.assertEqual(self.config.read_text(), "AGENT_SECRETS_URL=https://broker.example\n")
        self.assertEqual(sorted(p.name for p in self.conf.iterdir()), ["config"], "a temporary file stayed behind")

    def test_elsewhere_it_changes_nothing(self) -> None:
        self.env["GCE_METADATA_HOST"] = "127.0.0.1:9"
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not a Google VM; nothing changed", result.stdout)
        self.assertFalse(self.conf.exists())
        self.assertEqual(self.secrets.seen, [])


if __name__ == "__main__":
    unittest.main()
