#!/usr/bin/env python3
"""scripts/agent-access: reports what logins/credentials THIS session holds.

Stubs every external CLI on PATH, then checks: the script never hangs past its
budget, every row has the required fields, `--json` is valid JSON, and no
secret-shaped string (a JWT, an AWS key, a GitHub token) ever reaches stdout -
the whole point of the script is to report status without exposing values.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENT_ACCESS = DOTFILES / "scripts" / "agent-access"


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def make_jwt(payload: dict) -> str:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = b64(json.dumps(payload).encode())
    return f"{header}.{body}.sig"


# Secret-shaped substrings this script must never print, however a stub or a
# real CLI answers: full JWTs (hawk, taiga) and token-prefixed credentials.
SECRET_MARKERS = [
    "eyJhbGciOiAibm9uZSIsICJ0eXAiOiAiSldUIn0",  # this suite's JWT header b64
    "ghs_should_never_print",
    "sk-ant-should-never-print",
]


class AgentAccess(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.stub_dir = self.root / "bin"
        self.stub_dir.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.addCleanup(self.temp_dir.cleanup)

    def run_access(self, *args: str, timeout: float = 20) -> subprocess.CompletedProcess:
        # Only stub_dir plus the directories holding core utils the script
        # itself relies on (python3, jq, awk, timeout, grep, ...) - never the
        # host's real PATH, or a real aws/gcloud/vercel install would answer
        # instead of the stub and leak this machine's actual identity.
        core_dirs = []
        for tool in ("python3", "jq", "awk", "timeout", "grep", "date", "head",
                      "tr", "paste", "bash", "ls", "cat"):
            found = shutil.which(tool)
            self.assertIsNotNone(found, f"test host is missing required tool: {tool}")
            d = str(Path(found).parent)
            if d not in core_dirs:
                core_dirs.append(d)
        env = {
            "PATH": ":".join([str(self.stub_dir), *core_dirs]),
            "HOME": str(self.home),
        }
        start = time.monotonic()
        result = subprocess.run(
            [str(AGENT_ACCESS), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        self.elapsed = time.monotonic() - start
        return result

    def assert_no_secrets(self, *texts: str) -> None:
        for text in texts:
            for marker in SECRET_MARKERS:
                self.assertNotIn(marker, text)
            # No full three-part JWT of any shape ever reaches the output.
            self.assertNotRegex(
                text, r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{2,}"
            )

    def test_all_commands_missing_reports_status_without_crashing(self) -> None:
        """Nothing stubbed: every credential is missing/unknown, script still exits 0."""
        result = self.run_access()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("aws", result.stdout)
        self.assertIn("missing", result.stdout)
        self.assert_no_secrets(result.stdout, result.stderr)
        self.assertLess(self.elapsed, 15, "agent-access must finish in about 10s")

    def test_json_output_is_valid_with_required_fields(self) -> None:
        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 10)
        names = {row["name"] for row in rows}
        for required in ("aws", "github_app", "hawk", "dispatch", "modal"):
            self.assertIn(required, names)
        for row in rows:
            for field in ("name", "status", "identity", "expiry", "command", "detail"):
                self.assertIn(field, row)
            self.assertIn(row["status"], {"live", "expired", "missing", "unknown"})
        self.assert_no_secrets(result.stdout)

    def test_rejects_unknown_flag(self) -> None:
        result = self.run_access("--bogus")
        self.assertEqual(result.returncode, 2)

    def test_stubbed_clis_report_live_identity_without_leaking_token(self) -> None:
        hawk_jwt = make_jwt(
            {"email": "sami@trajectorylabs.net", "exp": int(time.time()) + 3600}
        )
        write_stub(self.stub_dir, "hawk-token", f'echo "{hawk_jwt}"')
        write_stub(
            self.stub_dir,
            "aws",
            'echo \'{"Arn":"arn:aws:sts::123:assumed-role/test/i-abc"}\'',
        )
        write_stub(
            self.stub_dir,
            "ant",
            r"""
echo "  Logged in to Example Org as sami@trajectorylabs.net"
echo "               expires:        2099-01-01T00:00:00Z"
""",
        )
        write_stub(self.stub_dir, "vercel", 'echo "noise"; echo "sami-2219"')
        write_stub(
            self.stub_dir,
            "pup",
            r"""
if [[ "$1 $2" == "auth status" ]]; then
    echo "Authenticated for site: us5.datadoghq.com"
    echo "Token expires in: 24m"
    exit 0
fi
""",
        )
        write_stub(
            self.stub_dir,
            "gcloud",
            'echo \'[{"account":"sami@trajectorylabs.net","status":"ACTIVE"}]\'',
        )
        write_stub(
            self.stub_dir,
            "gws",
            'echo \'{"user":{"emailAddress":"sami@trajectorylabs.net"}}\'',
        )
        write_stub(self.stub_dir, "fleetctl", 'echo "{}"')
        write_stub(
            self.stub_dir,
            "sentry-cli",
            'echo "  User: sami@trajectorylabs.net"',
        )
        write_stub(
            self.stub_dir,
            "kubectl",
            r"""
if [[ "$1 $2" == "config current-context" ]]; then echo production; exit 0; fi
if [[ "$1 $2" == "auth whoami" ]]; then
    echo "Username   arn:aws:sts::123:assumed-role/test/i-abc"
    exit 0
fi
""",
        )
        write_stub(
            self.stub_dir,
            "secrets",
            r"""
if [[ "$1" == grants ]]; then
    printf 'KEY\tSCOPE\tAGE\nTEST_KEY\tsession\t10s\n'
    exit 0
fi
""",
        )
        write_stub(self.stub_dir, "agent-secrets", 'exit 0')

        # github_app cache: covered owners
        cache = self.home / ".cache" / "gh-app-token"
        cache.mkdir(parents=True)
        (cache / "covered-123.json").write_text(json.dumps({"sjawhar": 1}))

        # taiga config with a (deliberately expired) id_token
        tl_dir = self.home / ".config" / "trajectory-labs"
        tl_dir.mkdir(parents=True)
        taiga_jwt = make_jwt({"exp": int(time.time()) - 10})
        (tl_dir / "taiga.json").write_text(
            json.dumps({"id_token": taiga_jwt, "refresh_token": "rt"})
        )

        result = self.run_access("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["name"]: row for row in json.loads(result.stdout)}

        self.assertEqual(rows["hawk"]["status"], "live")
        self.assertEqual(rows["hawk"]["identity"], "sami@trajectorylabs.net")
        self.assertEqual(rows["aws"]["status"], "live")
        self.assertIn("assumed-role/test", rows["aws"]["identity"])
        self.assertEqual(rows["anthropic_ant"]["status"], "live")
        self.assertEqual(rows["github_app"]["status"], "live")
        self.assertIn("sjawhar", rows["github_app"]["identity"])
        self.assertEqual(rows["taiga"]["status"], "expired")
        self.assertEqual(rows["vercel"]["identity"], "sami-2219")
        self.assertEqual(rows["datadog_pup"]["status"], "live")
        self.assertEqual(rows["gcloud"]["status"], "live")
        self.assertEqual(rows["google_workspace"]["status"], "live")
        self.assertEqual(rows["sentry"]["status"], "live")
        self.assertEqual(rows["kubectl"]["status"], "live")
        self.assertEqual(rows["secretsd_grants"]["identity"], "TEST_KEY")
        self.assertEqual(rows["agent_secrets_broker"]["status"], "live")

        self.assert_no_secrets(result.stdout)
        self.assertNotIn(hawk_jwt, result.stdout)
        self.assertNotIn(taiga_jwt, result.stdout)


if __name__ == "__main__":
    unittest.main()
