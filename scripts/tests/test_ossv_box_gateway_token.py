#!/usr/bin/env python3
"""scripts/ossv-box-gateway-token: write only the shared developer-box identity token.

Technique: `gcloud` and `logger` are stubs first on PATH. The gcloud stub records its
arguments and prints $STUB_OUTPUT, or exits $STUB_EXIT. HOME and XDG_RUNTIME_DIR are scratch
directories, so the test never reads the box's Google login or writes its runtime token.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ossv-box-gateway-token"

GCLOUD_STUB = r"""
printf '%s\n' "$@" >"$STUB_ARGS"
if [ "${STUB_EXIT:-0}" != 0 ]; then
    exit "$STUB_EXIT"
fi
printf '%s\n' "$STUB_OUTPUT"
"""


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class GatewayTokenWriter(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        stub_dir = root / "bin"
        stub_dir.mkdir()
        write_stub(stub_dir, "gcloud", GCLOUD_STUB)
        write_stub(stub_dir, "logger", "exit 0")
        self.args = root / "gcloud.args"
        self.runtime = root / "runtime"
        self.token = self.runtime / "ossv-box" / "gateway-token"
        self.env = {
            "PATH": f"{stub_dir}:/usr/bin:/bin",
            "HOME": str(root / "home"),
            "XDG_RUNTIME_DIR": str(self.runtime),
            "STUB_ARGS": str(self.args),
            "STUB_OUTPUT": "header.payload.signature",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_writer(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT)],
            capture_output=True,
            text=True,
            env={**self.env, **extra},
            check=False,
            timeout=30,
        )

    def test_writes_a_private_identity_token_for_the_shared_account(self) -> None:
        result = self.run_writer()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.token.read_text(encoding="utf-8"), "header.payload.signature")
        self.assertEqual(stat.S_IMODE(self.token.stat().st_mode), 0o600)
        self.assertEqual(
            self.args.read_text(encoding="utf-8").splitlines(),
            [
                "auth",
                "print-identity-token",
                "--impersonate-service-account=dev-box@theorem-agent-infra.iam.gserviceaccount.com",
                "--audiences=model-gateway-gce",
                "--include-email",
                "--format=value(id_token)",
            ],
        )

    def test_a_failed_mint_writes_no_token(self) -> None:
        result = self.run_writer(STUB_EXIT="1")

        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.token.exists())
        self.assertIn("no identity token from gcloud", result.stderr)

    def test_output_that_is_not_a_jwt_writes_no_token(self) -> None:
        result = self.run_writer(STUB_OUTPUT='{"id_token": "missing-structure"}')

        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.token.exists())


if __name__ == "__main__":
    unittest.main()
