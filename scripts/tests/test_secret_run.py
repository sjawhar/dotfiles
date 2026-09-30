#!/usr/bin/env python3
"""scripts/secret-run: the broker for a process with a broker identity, secretsd for any other.

The choice is made by `agent-secrets identity` (exit 0: this process has a broker identity),
never by failure: once the broker path is taken, its exit is secret-run's exit and secretsd is
never tried. Everything else (identity exits non-zero, an old client without `identity`, no
agent-secrets at all) runs `secrets` exactly as before, without touching the broker.

Technique: stub `agent-secrets` and `secrets` first on PATH; each prints the argv it got and,
for the exec forms, execs the command after `--` with a marker variable set.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SECRET_RUN = DOTFILES / "scripts" / "secret-run"
PLAN_B_URL = "https://secrets.internal.trajectorylabs.com"


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# identity exits $STUB_IDENTITY (printing to both streams, as a client may); every other call
# prints its argv and URL, then either exits $STUB_BROKER_EXIT or execs the command after `--`.
AGENT_SECRETS_STUB = r"""
if [[ "$1" == identity ]]; then
    echo "identity-stdout"
    echo "identity: ${STUB_IDENTITY_MSG:-no broker identity}" >&2
    exit "${STUB_IDENTITY:-1}"
fi
echo "BROKER $* URL=${AGENT_SECRETS_URL:-unset}"
[[ -n "${STUB_BROKER_EXIT:-}" ]] && exit "$STUB_BROKER_EXIT"
while (( $# )) && [[ "$1" != -- ]]; do shift; done
(( $# )) || exit 0
shift
VIA=broker exec "$@"
"""

SECRETS_STUB = r"""
echo "SECRETSD $*"
while (( $# )) && [[ "$1" != -- ]]; do shift; done
(( $# )) || exit 0
shift
VIA=secretsd exec "$@"
"""


class SecretRun(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.stub_dir = Path(self.temp_dir.name)
        write_stub(self.stub_dir, "secrets", SECRETS_STUB)
        self.env = {"PATH": f"{self.stub_dir}:/usr/bin:/bin", "HOME": self.temp_dir.name}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def with_client(self) -> None:
        write_stub(self.stub_dir, "agent-secrets", AGENT_SECRETS_STUB)

    def run_secret_run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SECRET_RUN), *args],
            capture_output=True,
            text=True,
            env={**self.env, **extra},
            check=False,
            timeout=30,
        )

    EXEC_ARGS = ("--reason", "skill: smoke", "--wait", "0", "AIRTABLE_TOKEN", "DD_API_KEY")
    CMD = ("--", "sh", "-c", 'echo "via=$VIA"')

    def test_broker_identity_execs_agent_secrets(self) -> None:
        self.with_client()
        result = self.run_secret_run(*self.EXEC_ARGS, *self.CMD, STUB_IDENTITY="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                (
                    "BROKER AIRTABLE_TOKEN DD_API_KEY --reason skill: smoke --wait 0 -- "
                    f'sh -c echo "via=$VIA" URL={PLAN_B_URL}'
                ),
                "via=broker",
            ],
        )
        self.assertNotIn("identity-stdout", result.stdout)

    def test_caller_url_is_kept(self) -> None:
        self.with_client()
        result = self.run_secret_run(
            "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0", AGENT_SECRETS_URL="https://b.example"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("URL=https://b.example", result.stdout)

    def test_no_identity_runs_secretsd_and_never_the_broker(self) -> None:
        self.with_client()
        result = self.run_secret_run(
            *self.EXEC_ARGS, *self.CMD, STUB_IDENTITY="1", STUB_IDENTITY_MSG="no key dir"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ['SECRETSD AIRTABLE_TOKEN DD_API_KEY -- sh -c echo "via=$VIA"', "via=secretsd"],
        )
        self.assertNotIn("BROKER", result.stdout)
        self.assertIn("identity: no key dir", result.stderr)

    def test_old_client_without_identity_runs_secretsd(self) -> None:
        self.with_client()
        result = self.run_secret_run("AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "via=secretsd")

    def test_no_client_runs_secretsd(self) -> None:
        result = self.run_secret_run(*self.EXEC_ARGS, *self.CMD)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ['SECRETSD AIRTABLE_TOKEN DD_API_KEY -- sh -c echo "via=$VIA"', "via=secretsd"],
        )

    def test_broker_failure_is_final(self) -> None:
        self.with_client()
        for code in ("69", "75", "77"):
            with self.subTest(exit=code):
                result = self.run_secret_run(
                    "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0", STUB_BROKER_EXIT=code
                )
                self.assertEqual(result.returncode, int(code))
                self.assertNotIn("SECRETSD", result.stdout)

    def test_request_on_the_broker(self) -> None:
        self.with_client()
        result = self.run_secret_run(
            "request",
            "--reason",
            "task: start",
            "DEEL_API_KEY",
            "GH_PUBLIC_REPO_PAT",
            STUB_IDENTITY="0",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"BROKER request DEEL_API_KEY GH_PUBLIC_REPO_PAT --reason task: start URL={PLAN_B_URL}"
            ],
        )

    def test_request_on_secretsd_is_a_grant_request(self) -> None:
        self.with_client()
        result = self.run_secret_run(
            "request",
            "--reason",
            "task: start",
            "DEEL_API_KEY",
            "GH_PUBLIC_REPO_PAT",
            STUB_IDENTITY="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(), ["SECRETSD get DEEL_API_KEY GH_PUBLIC_REPO_PAT"]
        )

    def test_usage_errors(self) -> None:
        self.with_client()
        cases = {
            "no keys": ("--", "true"),
            "no command": ("AIRTABLE_TOKEN", "--"),
            "no separator": ("AIRTABLE_TOKEN", "true"),
            "request without keys": ("request", "--reason", "x"),
            "unknown option": ("--bogus", "AIRTABLE_TOKEN", "--", "true"),
        }
        for name, args in cases.items():
            with self.subTest(case=name):
                result = self.run_secret_run(*args, STUB_IDENTITY="0")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("usage: secret-run", result.stderr)
                self.assertNotIn("BROKER", result.stdout)
                self.assertNotIn("SECRETSD", result.stdout)


if __name__ == "__main__":
    unittest.main()
