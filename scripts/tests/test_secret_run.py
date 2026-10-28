#!/usr/bin/env python3
"""scripts/secret-run: the broker for a process with a broker identity, secretsd for any other.

The choice is made by `agent-secrets identity` (exit 0: this process has a broker identity),
never by failure: once the broker path is taken, its exit is secret-run's exit and secretsd is
never tried. A client that answers "no identity" (identity exits 1) and no agent-secrets at all
run `secrets` exactly as before, without touching the broker. A client on PATH that cannot run
(mise's shim unresolvable, a resolved path that is not an executable file, any identity exit
other than 0 or 1, 2 included: every installable client has `identity`, so 2 is a usage error or
a Go panic) is an error, and neither store is touched.

mise's shim is resolved with `mise which` under MISE_DATA_DIR, else $HOME/.mise, reading only its
stdout, so a warning mise prints on stderr with exit 0 changes nothing: in an `env -i`
scope the shim itself looks in mise's default data dir, which on 2026-10-28 held a broken install,
and the old secret-run read that failure as "no identity" and silently used secretsd.

On the broker path the client gets the URL agent-secrets/broker-url.sh gives every consumer of
agent-secrets/broker.env: broker.env's, or env.local's over it, exported with its record
DOTFILES_AGENT_SECRETS_URL, when the caller has no URL or has the one a dotfiles load recorded (a
stale default); a URL the caller chose itself stays.

Technique: stub `agent-secrets` and `secrets` first on PATH; each prints the argv it got and,
for the exec forms, execs the command after `--` with a marker variable set. The `secrets` stub
refuses `get` with more than one key, as the real CLI does. The `mise` stub does what mise does
for a symlinked shim: run as `agent-secrets` it execs the install under MISE_DATA_DIR, or under
mise's default data dir ($HOME/.local/share/mise) when that is unset, and fails when there is
none there; run as `mise which -C / agent-secrets --tool agent-secrets` it prints that path.
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

MISE_STUB = r"""
data="${MISE_DATA_DIR:-$HOME/.local/share/mise}"
bin="$data/installs/agent-secrets/1.0.0/bin/agent-secrets"
if [[ "${0##*/}" == agent-secrets ]]; then
    [[ -e "$bin" ]] || { echo "mise ERROR No executable found for configured tool: agent-secrets" >&2; exit 1; }
    exec "$bin" "$@"
fi
[[ "$*" == "which -C / agent-secrets --tool agent-secrets" ]] || { echo "unexpected mise $*" >&2; exit 99; }
[[ -z "${STUB_MISE_WARN:-}" ]] || echo "mise WARN unknown field in ~/.config/mise/config.toml: settings.bogus" >&2
[[ -e "$bin" ]] || { echo "mise ERROR agent-secrets is not installed in $data" >&2; exit 1; }
echo "$bin"
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

    def with_mise_client(self, *, executable: bool = True, data_dir: str = ".mise") -> Path:
        """mise's shim dir on PATH ahead of the stubs, its shim a symlink to mise, and the client
        installed only under $HOME/<data_dir> ($HOME/.mise by default, as on sami-rocqperf)."""
        home = Path(self.temp_dir.name)
        mise_dir = home / "mise-bin"
        mise_dir.mkdir()
        write_stub(mise_dir, "mise", MISE_STUB)
        shims = home / ".mise" / "shims"
        shims.mkdir(parents=True)
        (shims / "agent-secrets").symlink_to(mise_dir / "mise")
        install = home / data_dir / "installs" / "agent-secrets" / "1.0.0" / "bin"
        install.mkdir(parents=True)
        write_stub(install, "agent-secrets", AGENT_SECRETS_STUB)
        if not executable:
            (install / "agent-secrets").chmod(0o644)
        self.env["PATH"] = f"{shims}:{self.env['PATH']}"
        return install / "agent-secrets"

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
        self.assertNotIn("cannot run", result.stderr)

    def test_no_client_runs_secretsd(self) -> None:
        result = self.run_secret_run(*self.EXEC_ARGS, *self.CMD)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ['SECRETSD AIRTABLE_TOKEN DD_API_KEY -- sh -c echo "via=$VIA"', "via=secretsd"],
        )
        self.assertEqual(result.stderr, "")

    def test_mise_shim_in_a_bare_environment_reaches_the_broker(self) -> None:
        """The 2026-10-28 outage: no MISE_DATA_DIR, so the shim itself fails; secret-run asks mise
        for the binary under $HOME/.mise and runs that, for identity and for the broker call."""
        self.with_mise_client()
        self.assertNotIn("MISE_DATA_DIR", self.env)
        result = self.run_secret_run("AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f'BROKER AIRTABLE_TOKEN -- sh -c echo "via=$VIA" URL={BROKER_URL} MARK={BROKER_URL}',
                "via=broker",
            ],
        )

    def test_mise_warning_on_stderr_with_exit_0_still_reaches_the_broker(self) -> None:
        """mise prints a WARN for an unknown config key (or MISE_VERBOSE/MISE_DEBUG output) on
        stderr and still exits 0: only its stdout is the path, and the warning reaches stderr."""
        self.with_mise_client()
        result = self.run_secret_run("AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0", STUB_MISE_WARN="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "via=broker")
        self.assertIn("mise WARN unknown field", result.stderr)
        self.assertNotIn("cannot run", result.stderr)

    def test_caller_mise_data_dir_is_used(self) -> None:
        """A caller's own MISE_DATA_DIR wins over $HOME/.mise: the client is installed only there."""
        self.with_mise_client(data_dir="other-mise")
        result = self.run_secret_run(
            "AIRTABLE_TOKEN",
            *self.CMD,
            STUB_IDENTITY="0",
            MISE_DATA_DIR=str(Path(self.temp_dir.name) / "other-mise"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "via=broker")

    def test_mise_shim_with_no_identity_still_runs_secretsd(self) -> None:
        """The resolved client answering "no identity" keeps the secretsd path the lanes use."""
        self.with_mise_client()
        result = self.run_secret_run("GH_AGENT_APP_PRIVATE_KEY_B64", *self.CMD, STUB_IDENTITY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ['SECRETSD GH_AGENT_APP_PRIVATE_KEY_B64 -- sh -c echo "via=$VIA"', "via=secretsd"],
        )
        self.assertNotIn("cannot run", result.stderr)

    def assert_cannot_run(self, result: subprocess.CompletedProcess[str], detail: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("SECRETSD", result.stdout)
        self.assertNotIn("BROKER", result.stdout)
        self.assertNotIn("via=", result.stdout)
        self.assertIn("secret-run: the agent-secrets client cannot run (", result.stderr)
        self.assertIn(detail, result.stderr)
        self.assertIn("take agent-secrets off PATH to use secretsd", result.stderr)

    def test_mise_shim_mise_cannot_resolve_is_an_error(self) -> None:
        client = self.with_mise_client()
        client.unlink()
        result = self.run_secret_run("AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0")
        self.assert_cannot_run(result, "mise could not resolve it; its error is above")
        self.assertIn("mise ERROR agent-secrets is not installed", result.stderr)

    def test_resolved_client_not_executable_is_an_error(self) -> None:
        client = self.with_mise_client(executable=False)
        result = self.run_secret_run("AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0")
        self.assert_cannot_run(result, f"{client} is not an executable file")

    def test_client_whose_exec_fails_is_an_error(self) -> None:
        """A client on PATH whose exec fails (here a missing interpreter) gets the shell's 126 or
        127, which is no answer from identity."""
        path = self.stub_dir / "agent-secrets"
        path.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
        path.chmod(0o755)
        result = self.run_secret_run("AIRTABLE_TOKEN", *self.CMD)
        self.assert_cannot_run(result, f"`{path} identity` exited 12")

    def test_identity_exit_other_than_0_or_1_is_an_error(self) -> None:
        """2 included: every client mise.toml can install has `identity`, so 2 is the client's
        usage error or a Go panic, not an old client."""
        self.with_client()
        for code in ("2", "3", "139"):
            with self.subTest(exit=code):
                result = self.run_secret_run("AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY=code)
                self.assert_cannot_run(result, f"identity` exited {code}: ")

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
