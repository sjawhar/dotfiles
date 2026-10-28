#!/usr/bin/env python3
"""scripts/secret-run: the broker for a process with a broker identity, secretsd for any other.

The choice is made by `agent-secrets identity` (exit 0: this process has a broker identity),
never by failure: once the broker path is taken, its exit is secret-run's exit and secretsd is
never tried. Everything else (identity exits non-zero, an old client without `identity`, no
agent-secrets at all) runs `secrets` exactly as before, without touching the broker.

On the broker path the client gets the URL agent-secrets/broker-url.sh gives every consumer of
agent-secrets/broker.env: broker.env's, or env.local's over it, exported with its record
DOTFILES_AGENT_SECRETS_URL, when the caller has no URL or has the one a dotfiles load recorded (a
stale default); a URL the caller chose itself stays.

Technique: stub `agent-secrets` and `secrets` first on PATH; each prints the argv it got and,
for the exec forms, execs the command after `--` with a marker variable set. The `secrets` stub
refuses `get` with more than one key, as the real CLI does.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SECRET_RUN = DOTFILES / "scripts" / "secret-run"
BROKER_URL = next(
    line.split("=", 1)[1].strip()
    for line in (DOTFILES / "agent-secrets" / "broker.env").read_text().splitlines()
    if line.startswith("AGENT_SECRETS_URL=")
)
STALE_URL = "https://stale.example"
CHOSEN_URL = "http://127.0.0.1:8443"
LOCAL_URL = "https://local.example"
# case -> (the caller's environment, env.local's URL or None, the URL and record the broker call
# gets; "unset" is no record). A stale default is a URL a dotfiles load recorded before broker.env
# changed. A chosen URL is one exported from outside the dotfiles (Legion's dev-broker.sh prints
# one for a local broker), also in a shell whose own load recorded the default.
URL_CASES: dict[str, tuple[dict[str, str], str | None, tuple[str, str]]] = {
    "unset": ({}, None, (BROKER_URL, BROKER_URL)),
    "stale default": (
        {"AGENT_SECRETS_URL": STALE_URL, "DOTFILES_AGENT_SECRETS_URL": STALE_URL},
        None,
        (BROKER_URL, BROKER_URL),
    ),
    "chosen URL": ({"AGENT_SECRETS_URL": CHOSEN_URL}, None, (CHOSEN_URL, "unset")),
    "chosen URL over a recorded default": (
        {"AGENT_SECRETS_URL": CHOSEN_URL, "DOTFILES_AGENT_SECRETS_URL": BROKER_URL},
        None,
        (CHOSEN_URL, BROKER_URL),
    ),
    "env.local": ({}, LOCAL_URL, (LOCAL_URL, LOCAL_URL)),
    "env.local over a stale default": (
        {"AGENT_SECRETS_URL": STALE_URL, "DOTFILES_AGENT_SECRETS_URL": STALE_URL},
        LOCAL_URL,
        (LOCAL_URL, LOCAL_URL),
    ),
}


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
echo "BROKER $* URL=${AGENT_SECRETS_URL:-unset} MARK=${DOTFILES_AGENT_SECRETS_URL:-unset}"
[[ -n "${STUB_BROKER_EXIT:-}" ]] && exit "$STUB_BROKER_EXIT"
while (( $# )) && [[ "$1" != -- ]]; do shift; done
(( $# )) || exit 0
shift
VIA=broker exec "$@"
"""

# `secrets get` takes exactly one KEY (forward crates/secrets/tests/client/command_forms.rs);
# $STUB_GET_FAIL names a key whose get fails.
SECRETS_STUB = r"""
if [[ "$1" == get ]]; then
    (( $# == 2 )) || { echo "error: unexpected argument '$3' found" >&2; exit 2; }
    echo "SECRETSD get $2"
    [[ "$2" != "${STUB_GET_FAIL:-}" ]] || exit 1
    exit 0
fi
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
        self.env = {
            "PATH": f"{self.stub_dir}:/usr/bin:/bin",
            "HOME": self.temp_dir.name,
            "DOTFILES_DIR": str(DOTFILES),
        }

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
                    f'sh -c echo "via=$VIA" URL={BROKER_URL} MARK={BROKER_URL}'
                ),
                "via=broker",
            ],
        )
        self.assertNotIn("identity-stdout", result.stdout)

    def test_equals_forms_and_a_duration_wait_reach_the_broker(self) -> None:
        self.with_client()
        result = self.run_secret_run(
            "--reason=skill: smoke", "--wait=30s", "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[0],
            "BROKER AIRTABLE_TOKEN --reason skill: smoke --wait 30s -- "
            f'sh -c echo "via=$VIA" URL={BROKER_URL} MARK={BROKER_URL}',
        )

    def test_caller_url_is_kept(self) -> None:
        self.with_client()
        result = self.run_secret_run(
            "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0", AGENT_SECRETS_URL="https://b.example"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("URL=https://b.example", result.stdout)

    def test_url_follows_broker_env_unless_the_caller_chose_one(self) -> None:
        self.with_client()
        env_local = Path(self.temp_dir.name) / ".config" / "agent-secrets" / "env.local"
        env_local.parent.mkdir(parents=True)
        for case, (caller, local, (url, mark)) in URL_CASES.items():
            with self.subTest(case=case):
                if local is None:
                    env_local.unlink(missing_ok=True)
                else:
                    env_local.write_text(f"AGENT_SECRETS_URL={local}\n", encoding="utf-8")
                result = self.run_secret_run("AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0", **caller)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.splitlines()[0],
                    f'BROKER AIRTABLE_TOKEN -- sh -c echo "via=$VIA" URL={url} MARK={mark}',
                )

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

    def test_old_client_without_identity_runs_secretsd_quietly(self) -> None:
        """A client older than `identity` exits 2 with its usage; that text is not shown."""
        self.with_client()
        result = self.run_secret_run(
            "AIRTABLE_TOKEN",
            *self.CMD,
            STUB_IDENTITY="2",
            STUB_IDENTITY_MSG="usage: agent-secrets …",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "via=secretsd")
        self.assertNotIn("usage: agent-secrets", result.stderr)

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
                "BROKER request DEEL_API_KEY GH_PUBLIC_REPO_PAT --reason task: start "
                f"URL={BROKER_URL} MARK={BROKER_URL}"
            ],
        )

    def test_ttl_reaches_the_broker_for_exec_and_request(self) -> None:
        self.with_client()
        result = self.run_secret_run("--ttl", "2m", "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[0],
            f'BROKER AIRTABLE_TOKEN --ttl 2m -- sh -c echo "via=$VIA" URL={BROKER_URL} MARK={BROKER_URL}',
        )
        result = self.run_secret_run("request", "--ttl=5m", "DEEL_API_KEY", STUB_IDENTITY="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"BROKER request DEEL_API_KEY --ttl 5m URL={BROKER_URL} MARK={BROKER_URL}"],
        )

    def test_ttl_is_dropped_for_secretsd(self) -> None:
        self.with_client()
        result = self.run_secret_run("--ttl", "2m", "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(), ['SECRETSD AIRTABLE_TOKEN -- sh -c echo "via=$VIA"', "via=secretsd"]
        )

    def test_request_on_secretsd_asks_for_each_key(self) -> None:
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
            result.stdout.splitlines(),
            ["SECRETSD get DEEL_API_KEY", "SECRETSD get GH_PUBLIC_REPO_PAT"],
        )

    def test_request_on_secretsd_stops_at_the_first_failure(self) -> None:
        self.with_client()
        result = self.run_secret_run(
            "request",
            "DEEL_API_KEY",
            "GH_PUBLIC_REPO_PAT",
            STUB_IDENTITY="1",
            STUB_GET_FAIL="DEEL_API_KEY",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.splitlines(), ["SECRETSD get DEEL_API_KEY"])

    def test_usage_errors(self) -> None:
        self.with_client()
        cases = {
            "no keys": ("--", "true"),
            "no command": ("AIRTABLE_TOKEN", "--"),
            "no separator": ("AIRTABLE_TOKEN", "true"),
            "request without keys": ("request", "--reason", "x"),
            "request takes no wait": ("request", "--wait", "0", "AIRTABLE_TOKEN"),
            "request takes no wait=": ("request", "--wait=0", "AIRTABLE_TOKEN"),
            "ttl without a value": ("AIRTABLE_TOKEN", "--ttl"),
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
