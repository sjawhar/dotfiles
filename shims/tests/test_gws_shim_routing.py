#!/usr/bin/python3
"""Routing tests for the gws shim: which credential path fires, and fail-loud.

Technique: build a scratch dir containing (a) a stub `gws` that dumps its auth env to
stdout, (b) a stub `google-user-token` that prints a fixed token (or exits 1), put it
first on PATH, and invoke the shim by absolute path. This ensures both stubs win over
the parallel sibling shims while `find_real_gws` discovers the stub as real gws.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

SHIMS = Path(__file__).resolve().parent.parent


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class GwsRouting(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.stub_dir = Path(self.tmp.name)
        write_stub(
            self.stub_dir,
            "gws",
            'echo "TOKEN=${GOOGLE_WORKSPACE_CLI_TOKEN:-}"\n'
            'echo "FILE=${GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE:-}"\n'
            'if [[ -n "${GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE:-}" ]]; then\n'
            '  echo "MODE=$(stat -c %a "$GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE")"\n'
            '  cat "$GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"\n'
            'fi\n'
            'exit "${STUB_EXIT:-0}"',
        )
        write_stub(self.stub_dir, "google-user-token", "echo unexpected broker >&2; exit 99")
        write_stub(self.stub_dir, "uv", '[[ "$1" == "run" && "$2" == "--quiet" && "$3" == "--script" ]] || exit 99\n"$4"')
        self.env = {
            "PATH": f"{self.stub_dir}:{SHIMS}:/usr/bin:/bin",
            "HOME": os.environ["HOME"],
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_shim(
        self, extra_env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **(extra_env or {})}
        return subprocess.run(
            [str(SHIMS / "gws"), "drive", "files", "list"],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_broker_token_injected_by_default(self):
        write_stub(self.stub_dir, "google-user-token", "echo broker-token-xyz")
        result = self.run_shim()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=broker-token-xyz", result.stdout)

    def test_gmail_env_materializes_credentials_file(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN; exit 1")
        result = self.run_shim({"GMAIL_OAUTH_CREDENTIALS": '{"type":"authorized_user"}'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('{"type":"authorized_user"}', result.stdout)
        self.assertIn("TOKEN=\n", result.stdout)  # no broker token on the gated path
        self.assertIn("MODE=600", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        creds_file = Path(next(line[5:] for line in result.stdout.splitlines() if line.startswith("FILE=")))
        self.assertFalse(creds_file.exists())

    def test_gmail_env_cleans_credentials_file_after_real_gws_fails(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN; exit 1")
        result = self.run_shim(
            {
                "GMAIL_OAUTH_CREDENTIALS": '{"type":"authorized_user"}',
                "STUB_EXIT": "42",
            }
        )
        self.assertEqual(result.returncode, 42, result.stderr)
        creds_file = Path(next(line[5:] for line in result.stdout.splitlines() if line.startswith("FILE=")))
        self.assertFalse(creds_file.exists())

    def test_explicit_token_env_passes_through(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN; exit 1")
        result = self.run_shim({"GOOGLE_WORKSPACE_CLI_TOKEN": "preset-token"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=preset-token", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    def test_explicit_credentials_file_passes_through(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN; exit 1")
        credentials_file = self.stub_dir / "caller-creds.json"
        credentials_file.write_text('{"type":"authorized_user"}', encoding="utf-8")
        result = self.run_shim({"GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": str(credentials_file)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"FILE={credentials_file}", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    def test_explicit_token_wins_over_gmail_credentials(self):
        write_stub(self.stub_dir, "google-user-token", "echo SHOULD-NOT-RUN; exit 1")
        result = self.run_shim(
            {
                "GOOGLE_WORKSPACE_CLI_TOKEN": "preset-token",
                "GMAIL_OAUTH_CREDENTIALS": '{"type":"authorized_user"}',
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=preset-token", result.stdout)
        self.assertIn("FILE=\n", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)

    def test_broker_failure_is_loud(self):
        write_stub(self.stub_dir, "google-user-token", "echo broker down >&2; exit 1")
        result = self.run_shim()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broker down", result.stderr)

    def test_empty_broker_token_is_loud(self):
        write_stub(self.stub_dir, "google-user-token", "exit 0")
        result = self.run_shim()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("google-user-token returned an empty token", result.stderr)
        self.assertNotIn("TOKEN=", result.stdout)

    def test_find_real_gws_skips_sourced_shim(self):
        env = {
            **self.env,
            "PATH": f"{SHIMS}:{self.stub_dir}:/usr/bin:/bin",
            "GOOGLE_WORKSPACE_CLI_TOKEN": "unused",
        }
        result = subprocess.run(
            ["/bin/bash", "-c", f'source "{SHIMS / "gws"}"; find_real_gws'],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.stub_dir / "gws"))

    def test_mise_uv_runs_broker_when_uv_is_not_on_path(self):
        (self.stub_dir / "uv").unlink()
        mise_uv = self.stub_dir / "home" / ".mise" / "shims"
        mise_uv.mkdir(parents=True)
        write_stub(mise_uv, "uv", 'echo fallback-token')
        result = self.run_shim({"HOME": str(self.stub_dir / "home")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TOKEN=fallback-token", result.stdout)


if __name__ == "__main__":
    unittest.main()
