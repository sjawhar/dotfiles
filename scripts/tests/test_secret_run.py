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

import json
import re
import socket
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


# identity exits $STUB_IDENTITY (printing to both streams, as a client may); `machine login-status`
# prints $STUB_LOGIN (default issued); `register [--wait N] --exec -- CMD` prints its argv and execs
# CMD as a registered session, whose identity is then $STUB_REGISTERED_IDENTITY (default 0); every
# other call prints its argv less `--reason R` and its URL, then either exits $STUB_BROKER_EXIT or
# execs the command after `--`. R goes to stderr as `REASON R`, beside `SESSION_ID S`, the session
# id the real client sends (OMP_SESSION_ID, else ENVOY_SESSION_ID, else CLAUDE_CODE_SESSION_ID).
AGENT_SECRETS_STUB = r"""
if [[ "$1" == identity ]]; then
    echo "identity-stdout"
    echo "identity: ${STUB_IDENTITY_MSG:-no broker identity}" >&2
    exit "${STUB_IDENTITY:-1}"
fi
if [[ "$1 ${2:-}" == "machine login-status" ]]; then
    echo "${STUB_LOGIN-issued}"
    exit 0
fi
if [[ "$1" == register ]]; then
    echo "REGISTER ${*:2}"
    while (( $# )) && [[ "$1" != -- ]]; do shift; done
    shift
    STUB_IDENTITY="${STUB_REGISTERED_IDENTITY:-0}" exec "$@"
fi
printed=()
for (( i = 1; i <= $#; i++ )); do
    if [[ "${!i}" == -- ]]; then printed+=("${@:i}"); break; fi
    if [[ "${!i}" == --reason ]]; then
        (( i++ ))
        echo "REASON ${!i}" >&2
        continue
    fi
    printed+=("${!i}")
done
echo "SESSION_ID ${OMP_SESSION_ID:-${ENVOY_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-none}}}" >&2
echo "BROKER ${printed[*]} URL=${AGENT_SECRETS_URL:-unset} MARK=${DOTFILES_AGENT_SECRETS_URL:-unset}"
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
                    "BROKER AIRTABLE_TOKEN DD_API_KEY --wait 0 -- "
                    f'sh -c echo "via=$VIA" URL={BROKER_URL} MARK={BROKER_URL}'
                ),
                "via=broker",
            ],
        )
        self.assertNotIn("identity-stdout", result.stdout)
        self.assertRegex(result.stderr, r"(?m)^REASON skill: smoke \| session: no omp session; pid \d+, cwd ")

    def test_equals_forms_and_a_duration_wait_reach_the_broker(self) -> None:
        self.with_client()
        result = self.run_secret_run(
            "--reason=skill: smoke", "--wait=30s", "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[0],
            "BROKER AIRTABLE_TOKEN --wait 30s -- "
            f'sh -c echo "via=$VIA" URL={BROKER_URL} MARK={BROKER_URL}',
        )
        self.assertRegex(result.stderr, r"(?m)^REASON skill: smoke \| session: ")

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
                f"BROKER request DEEL_API_KEY GH_PUBLIC_REPO_PAT URL={BROKER_URL} MARK={BROKER_URL}"
            ],
        )
        self.assertRegex(
            result.stderr,
            r"(?m)^REASON task: start \| session: no omp session; pid \d+, cwd .+ \| runs: request only, no command "
            r"\| host: \S+$",
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

    GITHUB_ARGS = (
        "--github-owner",
        "sjawhar",
        "--github-repo",
        "proofs",
        "--github-repo=lean",
        "--github-read-only",
    )

    def test_github_narrowing_reaches_the_broker(self) -> None:
        self.with_client()
        result = self.run_secret_run(*self.GITHUB_ARGS, "GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines()[0],
            "BROKER GITHUB_APP_SJAWHAR --github-owner sjawhar --github-repo proofs --github-repo lean "
            f'--github-read-only -- sh -c echo "via=$VIA" URL={BROKER_URL} MARK={BROKER_URL}',
        )

    def test_github_narrowing_reaches_the_resolved_client_in_a_bare_environment(self) -> None:
        """gh-app-token's narrowed call in an `env -i` unit (ossv-mcsl-ghtoken): the flags go to
        the client `mise which` resolved under $HOME/.mise, never to mise's shim, which looks in
        mise's default data dir and fails there."""
        self.with_mise_client()
        self.assertNotIn("MISE_DATA_DIR", self.env)
        result = self.run_secret_run(*self.GITHUB_ARGS, "GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "BROKER GITHUB_APP_SJAWHAR --github-owner sjawhar --github-repo proofs --github-repo lean "
                f'--github-read-only -- sh -c echo "via=$VIA" URL={BROKER_URL} MARK={BROKER_URL}',
                "via=broker",
            ],
        )

    def test_github_narrowing_is_refused_without_the_broker(self) -> None:
        # secretsd cannot narrow a GitHub App token: dropping the flags, as --ttl is dropped,
        # would hand the command a token for every repository of the account.
        for name, with_client in (("no client", False), ("no broker identity", True)):
            with self.subTest(case=name):
                if with_client:
                    self.with_client()
                result = self.run_secret_run(
                    "--github-owner=sjawhar", "--github-read-only", "GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY="1"
                )
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertNotIn("SECRETSD", result.stdout)
                self.assertNotIn("via=", result.stdout)
                self.assertIn("secrets-broker identity", result.stderr)

    def with_stale_install(self, *, pinned: bool = True) -> Path:
        """An old mise install of the client first on PATH directly, not through a shim, the way a
        long-lived `mise exec` process holds one; `mise` on PATH names the pin (1.0.0, installed only
        when pinned) under $HOME/.mise. The stale client prints STALE and exits 2 for any flag it
        does not know, as v17.5.0 did for --github-owner."""
        home = Path(self.temp_dir.name)
        mise_dir = home / "mise-bin"
        mise_dir.mkdir(exist_ok=True)
        write_stub(mise_dir, "mise", MISE_STUB)
        stale = home / ".mise" / "installs" / "agent-secrets" / "0.9.0" / "bin"
        stale.mkdir(parents=True)
        write_stub(stale, "agent-secrets", 'echo "STALE $*"; [[ "$*" != *--github-* ]] || exit 2; exit 0')
        if pinned:
            install = home / ".mise" / "installs" / "agent-secrets" / "1.0.0" / "bin"
            install.mkdir(parents=True)
            write_stub(install, "agent-secrets", AGENT_SECRETS_STUB)
        self.env["PATH"] = f"{stale}:{mise_dir}:{self.env['PATH']}"
        return stale / "agent-secrets"

    def test_a_stale_install_on_path_is_resolved_to_the_pin(self) -> None:
        """agent-open's relay, 2026-10-29: PATH held v17.5.0 directly; a narrowed call must reach the
        pinned client, never the old one."""
        self.with_stale_install()
        result = self.run_secret_run(*self.GITHUB_ARGS, "GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("STALE", result.stdout + result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "BROKER GITHUB_APP_SJAWHAR --github-owner sjawhar --github-repo proofs --github-repo lean "
                f'--github-read-only -- sh -c echo "via=$VIA" URL={BROKER_URL} MARK={BROKER_URL}',
                "via=broker",
            ],
        )

    def test_a_stale_install_whose_pin_mise_cannot_name_is_an_error_naming_it(self) -> None:
        stale = self.with_stale_install(pinned=False)
        result = self.run_secret_run("GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY="0")
        self.assert_cannot_run(result, f"PATH holds mise's agent-secrets 0.9.0 directly ({stale})")
        self.assertNotIn("STALE", result.stdout + result.stderr)

    def test_register_with_a_stale_install_registers_through_the_pin(self) -> None:
        self.with_stale_install()
        self.with_helper_unit()
        result = self.run_secret_run(
            "--register", *self.GITHUB_ARGS, "GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY="1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("STALE", result.stdout + result.stderr)
        self.assertEqual(len(self.register_lines(result)), 1, result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], "via=broker")

    def with_helper_unit(self) -> None:
        """The file agent-secrets-session reads as "this machine has the helper"."""
        unit = Path(self.temp_dir.name) / ".config" / "systemd" / "user" / "agent-secrets-helper.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("[Service]\n", encoding="utf-8")

    def register_lines(self, result: subprocess.CompletedProcess[str]) -> list[str]:
        return [line for line in result.stdout.splitlines() if line.startswith("REGISTER ")]

    def test_register_with_no_identity_registers_then_reaches_the_broker(self) -> None:
        """gh-app-token's call: the client answers no identity, so secret-run runs itself again under
        agent-secrets-session, which registers it (`register --wait 10 --exec`, the helper holding a
        launcher credential), and the second pass, now a session, takes the broker. The second pass
        carries no --register, so nothing can loop."""
        self.with_client()
        self.with_helper_unit()
        result = self.run_secret_run(
            "--register", *self.GITHUB_ARGS, "GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY="1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(self.register_lines(result)), 1, result.stdout)
        self.assertEqual(
            lines[0],
            f"REGISTER --wait 10 --exec -- {SECRET_RUN} --github-owner sjawhar --github-repo proofs "
            '--github-repo=lean --github-read-only GITHUB_APP_SJAWHAR -- sh -c echo "via=$VIA"',
        )
        self.assertEqual(
            lines[1],
            "BROKER GITHUB_APP_SJAWHAR --github-owner sjawhar --github-repo proofs --github-repo lean "
            f'--github-read-only -- sh -c echo "via=$VIA" URL={BROKER_URL} MARK={BROKER_URL}',
        )
        self.assertEqual(lines[2], "via=broker")
        self.assertNotIn("SECRETSD", result.stdout)

    def test_register_runs_the_resolved_client_in_a_bare_environment(self) -> None:
        """In an `env -i` scope mise's shim cannot resolve; registration runs the client secret-run
        resolved under $HOME/.mise, so the call still ends at the broker."""
        self.with_mise_client()
        self.with_helper_unit()
        self.assertNotIn("MISE_DATA_DIR", self.env)
        result = self.run_secret_run("--register", "GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.register_lines(result)), 1, result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], "via=broker")

    def test_register_never_hides_a_client_that_cannot_run(self) -> None:
        """8a21676's hard error stands: a client that cannot run is checked before --register."""
        self.with_client()
        self.with_helper_unit()
        for code in ("2", "127"):
            with self.subTest(exit=code):
                result = self.run_secret_run("--register", "GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY=code)
                self.assert_cannot_run(result, f"identity` exited {code}: ")
                self.assertEqual(self.register_lines(result), [])
        client = self.with_mise_client()
        client.unlink()
        result = self.run_secret_run("--register", "GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY="1")
        self.assert_cannot_run(result, "mise could not resolve it; its error is above")
        self.assertEqual(self.register_lines(result), [])

    def test_register_with_an_identity_goes_straight_to_the_broker(self) -> None:
        self.with_client()
        self.with_helper_unit()
        result = self.run_secret_run("--register", "GITHUB_APP_SJAWHAR", *self.CMD, STUB_IDENTITY="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.register_lines(result), [])
        self.assertEqual(result.stdout.splitlines()[-1], "via=broker")

    def test_register_without_a_client_is_secretsd_as_before(self) -> None:
        result = self.run_secret_run("--register", "AIRTABLE_TOKEN", *self.CMD)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(), ['SECRETSD AIRTABLE_TOKEN -- sh -c echo "via=$VIA"', "via=secretsd"]
        )

    def test_register_that_gives_no_identity_ends_where_it_would_have_once(self) -> None:
        """A helper with no launcher credential registers the session without enrolling it, so the
        second pass still has no identity: secretsd for a plain key, the narrowing refusal for a
        GitHub one, and exactly one registration either way."""
        self.with_client()
        self.with_helper_unit()
        extra = {"STUB_IDENTITY": "1", "STUB_LOGIN": "none", "STUB_REGISTERED_IDENTITY": "1"}
        result = self.run_secret_run("--register", "AIRTABLE_TOKEN", *self.CMD, **extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.register_lines(result), [f'REGISTER --exec -- {SECRET_RUN} AIRTABLE_TOKEN -- sh -c echo "via=$VIA"'])
        self.assertEqual(result.stdout.splitlines()[-1], "via=secretsd")
        result = self.run_secret_run("--register", "--github-owner=sjawhar", "GITHUB_APP_SJAWHAR", *self.CMD, **extra)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(len(self.register_lines(result)), 1)
        self.assertFalse([line for line in result.stdout.splitlines() if line.startswith("via=")], result.stdout)
        self.assertIn("secrets-broker identity", result.stderr)

    def test_register_on_a_machine_without_the_helper_runs_unregistered(self) -> None:
        self.with_client()
        result = self.run_secret_run("--register", "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.register_lines(result), [])
        self.assertEqual(result.stdout.splitlines()[-1], "via=secretsd")

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
            "github-owner without a value": ("AIRTABLE_TOKEN", "--github-owner"),
            "request takes no github-owner": ("request", "--github-owner", "sjawhar", "AIRTABLE_TOKEN"),
            "request takes no github-repo=": ("request", "--github-repo=proofs", "AIRTABLE_TOKEN"),
            "request takes no github-read-only": ("request", "--github-read-only", "AIRTABLE_TOKEN"),
            "request takes no register": ("request", "--register", "AIRTABLE_TOKEN"),
            "unknown option": ("--bogus", "AIRTABLE_TOKEN", "--", "true"),
        }
        for name, args in cases.items():
            with self.subTest(case=name):
                result = self.run_secret_run(*args, STUB_IDENTITY="0")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("usage: secret-run", result.stderr)
                self.assertNotIn("BROKER", result.stdout)
                self.assertNotIn("SECRETSD", result.stdout)

    # The approval card shows the secret names, the reason and the request's session id, and
    # nothing else (TASKCOACH's gws call, 2026-10-30: "gws theorem modify", "No session named").
    SESSION = "01a16a9e-a90d-7148-95ed-0e9e7839dbd2"
    GWS = (
        "--",
        "gws",
        "docs",
        "documents",
        "batchUpdate",
        "--params",
        '{"documentId":"1PTCUrEOLmYbHUx9Zf-ruQ5P5GhDAnpsEptQWOvRcKZk"}',
        "--json",
        '{"requests":[{"insertText":{"text":"a private paragraph ya29.not-a-real-token"}}]}',
    )

    def with_session(self, title: str = "TASKCOACH: RL task difficulty coach") -> Path:
        """An omp session's transcript, with omp's title slot first, and its subagent CarryCensus's
        transcript in the directory of the same name; returns the subagent's."""
        sessions = Path(self.temp_dir.name) / ".omp" / "agent" / "sessions" / "-src-taiga"
        root = sessions / f"2026-10-23T21-15-45-293Z_{self.SESSION}.jsonl"
        root.parent.mkdir(parents=True)
        slot = json.dumps({"type": "title", "v": 1, "title": title, "source": "user"})
        root.write_text(f"{slot}\n" + json.dumps({"type": "session", "id": self.SESSION}) + "\n", encoding="utf-8")
        subagent = sessions / root.stem / "CarryCensus.jsonl"
        subagent.parent.mkdir()
        subagent.write_text(json.dumps({"type": "title", "v": 1, "title": ""}) + "\n", encoding="utf-8")
        write_stub(self.stub_dir, "gws", 'echo "via=$VIA"')
        return subagent

    def reason_of(self, result: subprocess.CompletedProcess[str]) -> str:
        lines = [line for line in result.stderr.splitlines() if line.startswith("REASON ")]
        self.assertEqual(len(lines), 1, result.stderr)
        return lines[0].removeprefix("REASON ")

    def test_a_broker_request_names_why_its_session_its_command_and_its_host(self) -> None:
        self.with_client()
        subagent = self.with_session()
        result = self.run_secret_run(
            "--reason",
            "gws theorem modify",
            "--wait",
            "4m",
            "GWS_THEOREM_MODIFY_OAUTH",
            *self.GWS,
            STUB_IDENTITY="0",
            OMP_SESSION_ID=self.SESSION,
            PI_SESSION_FILE=str(subagent),
            SECRET_RUN_REASON="retitle the environment tabs",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"BROKER GWS_THEOREM_MODIFY_OAUTH --wait 4m {' '.join(self.GWS)} URL={BROKER_URL} MARK={BROKER_URL}",
                "via=broker",
            ],
        )
        reason = self.reason_of(result)
        self.assertRegex(
            reason,
            "^"
            + re.escape(
                f'gws theorem modify; retitle the environment tabs | session: omp {self.SESSION} "TASKCOACH: RL task '
                'difficulty coach" subagent CarryCensus | runs: gws docs documents batchUpdate '
                f"documentId=1PTCUrEOLmYbHUx9Zf-ruQ5P5GhDAnpsEptQWOvRcKZk +4 args | host: {socket.gethostname()} pid "
            )
            + r"\d+$",
        )
        self.assertNotIn("ya29", reason)
        self.assertNotIn("private", reason)
        self.assertIn(f"SESSION_ID {self.SESSION}", result.stderr.splitlines())

    def test_a_session_file_alone_names_the_session_to_the_client(self) -> None:
        """omp's eval kernel runs with PI_SESSION_FILE and no OMP_SESSION_ID: secret-run exports
        the session id the file names, so the request body names its session."""
        self.with_client()
        subagent = self.with_session()
        result = self.run_secret_run(
            "--reason", "gws theorem modify", "GWS_THEOREM_MODIFY_OAUTH", *self.GWS,
            STUB_IDENTITY="0", PI_SESSION_FILE=str(subagent),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"SESSION_ID {self.SESSION}", result.stderr.splitlines())
        self.assertIn(f'| session: omp {self.SESSION} "TASKCOACH: RL task difficulty coach" subagent CarryCensus |',
                      self.reason_of(result))

    def test_a_session_the_client_already_sends_is_not_replaced(self) -> None:
        self.with_client()
        subagent = self.with_session()
        result = self.run_secret_run(
            "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="0", PI_SESSION_FILE=str(subagent),
            CLAUDE_CODE_SESSION_ID="claude-session-1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SESSION_ID claude-session-1", result.stderr.splitlines())
        self.assertIn("| session: claude claude-session-1 |", self.reason_of(result))

    def test_dry_run_prints_the_request_and_requests_nothing(self) -> None:
        self.with_client()
        subagent = self.with_session()
        result = self.run_secret_run(
            "request", "--dry-run", "--reason", "test: card text check", "--ttl", "1m", "GWS_THEOREM_MODIFY_OAUTH",
            STUB_IDENTITY="0", PI_SESSION_FILE=str(subagent),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(
            lines[:5],
            [
                "secret-run --dry-run: nothing requested",
                "store: broker",
                "keys: GWS_THEOREM_MODIFY_OAUTH",
                "ttl: 1m",
                f"session_id: {self.SESSION}",
            ],
        )
        self.assertRegex(
            lines[5],
            "^"
            + re.escape(f'reason: test: card text check | session: omp {self.SESSION} "TASKCOACH: RL task difficulty '
                        "coach\" subagent CarryCensus | runs: request only, no command | host: ")
            + r"\S+ pid \d+$",
        )
        self.assertEqual(len(lines), 6, result.stdout)
        self.assertNotIn("REASON", result.stderr)

    def test_dry_run_on_secretsd_runs_nothing(self) -> None:
        self.with_client()
        result = self.run_secret_run("--dry-run", "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("store: secretsd (no broker identity)", result.stdout)
        self.assertIn("session_id: none", result.stdout)
        for absent in ("BROKER", "SECRETSD", "via="):
            self.assertNotIn(absent, result.stdout)

    def test_dry_run_never_registers_or_runs_the_command(self) -> None:
        self.with_client()
        result = self.run_secret_run("--dry-run", "--register", "AIRTABLE_TOKEN", *self.CMD, STUB_IDENTITY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("store: the broker once --register enrolls this process, else secretsd", result.stdout)
        self.assertIn("session_id: none", result.stdout)
        for absent in ("REGISTER", "BROKER", "SECRETSD", "via="):
            self.assertNotIn(absent, result.stdout)

    def test_a_failed_reason_builder_still_says_who_asks(self) -> None:
        """The broker path never stops on the reason: a builder that fails (here python3 itself)
        leaves the caller's reason, in printable ASCII, with the host and the pid."""
        self.with_client()
        write_stub(self.stub_dir, "python3", "exit 1")
        result = self.run_secret_run(
            "--reason", "gws theorem modify", "AIRTABLE_TOKEN", *self.CMD,
            STUB_IDENTITY="0", SECRET_RUN_REASON="line one\nline\u202etwo",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "via=broker")
        self.assertRegex(
            self.reason_of(result),
            r"^gws theorem modify; line one line two \| session: unknown, secret-run-reason failed "
            rf"\| host: {re.escape(socket.gethostname())} pid \d+$",
        )


if __name__ == "__main__":
    unittest.main()
