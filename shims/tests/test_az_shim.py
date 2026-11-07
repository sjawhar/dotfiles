#!/usr/bin/env python3
"""shims/az points az at the box identity in an agent session and nowhere else.

Technique: a stub `az` in a dir after the shims dir on PATH, printing the AZURE_CONFIG_DIR it
was given, and a fake HOME holding a box identity profile and a user login profile. The Google-VM
cases run the real scripts/on-google-vm against scripts/tests/gce_metadata_stub.py, a stand-in
metadata server named by GCE_METADATA_HOST; every other case names a closed port there, so no case
depends on the machine the suite runs on. Their az stub records what `az login` got, argv and
stdin, and writes a service-principal profile.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SHIMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SHIMS.parent / "scripts" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gce_metadata_stub import MetadataStub, id_token  # noqa: E402 - found through the path above
from stdin_cases import SSH_CONNECTION, SSH_TTY, run_with_tty, run_without_tty  # noqa: E402

SESSION = "01a0fa64-b3a5-7415-b143-c2a338b5da8b"
AGENT_MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION",
                 "CLAUDECODE", "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "LEGION_ROLE", "LEGION_TREE",
                 "LEGION_CONTROLLER", "LEGION_DAEMON_URL")
TIMEOUT = 20

STUB = 'echo "config=${AZURE_CONFIG_DIR-unset} args=$*"'
# A machine whose managed identity signs in: `az login --identity` writes the profile.
LOGIN_STUB = """
if [ "${1-}" = login ]; then
    printf '%s' "$BOX_PROFILE" > "$AZURE_CONFIG_DIR/azureProfile.json"
    exit 0
fi
echo "config=${AZURE_CONFIG_DIR-unset} args=$*"
"""

USER_PROFILE = '{"subscriptions": [{"user": {"name": "Sami@Theorem.dev", "type": "user"}}]}'
BOX_PROFILE = '{"subscriptions": [{"user": {"name": "systemAssignedIdentity", "type": "servicePrincipal"}}]}'
# The agents' own Azure identity (theorem-labs/agent-infra#1146): a made-up client id, the real tenant.
CLIENT_ID = "00000000-1111-2222-3333-444444444444"
TENANT = "aaa6ad34-51af-41e7-891f-f16f5c53c396"
# `az login --service-principal --federated-token @/dev/stdin`: record argv and stdin, write the profile.
FEDERATED_STUB = """
if [ "${1-}" = login ]; then
    { printf '%s\\n' "$@"; printf 'STDIN=%s\\n' "$(cat)"; } >> "$LOGIN_LOG"
    [ -z "${LOGIN_FAILS-}" ] || { echo "AADSTS700213: No matching federated identity record found" >&2; exit 1; }
    printf '%s' "$SP_PROFILE" > "$AZURE_CONFIG_DIR/azureProfile.json"
    exit 0
fi
echo "config=${AZURE_CONFIG_DIR-unset} args=$*"
"""
SP_PROFILE = json.dumps({"subscriptions": [{"user": {"name": CLIENT_ID, "type": "servicePrincipal"}}]})


def write_stub(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class AzShim(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.stub_dir = self.root / "bin"
        write_stub(self.stub_dir, "az", STUB)
        self.home = self.root / "home"
        (self.home / ".azure").mkdir(parents=True)
        (self.home / ".azure" / "azureProfile.json").write_text(USER_PROFILE, encoding="utf-8")
        self.box = self.home / ".azure-box-identity"
        self.box.mkdir()
        (self.box / "azureProfile.json").write_text(BOX_PROFILE, encoding="utf-8")
        self.plain = {
            "HOME": str(self.home),
            "PATH": f"{SHIMS}{os.pathsep}{self.stub_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        }
        self.agent = {**self.plain, "OMP_SESSION_ID": SESSION}
        # Not a Google VM unless a case says so: a closed port (scripts/on-google-vm).
        self.plain["GCE_METADATA_HOST"] = self.agent["GCE_METADATA_HOST"] = "127.0.0.1:9"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def az(self, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SHIMS / "az"), *args],
            capture_output=True,
            text=True,
            cwd=self.root,
            env=env,
            check=False,
            timeout=TIMEOUT,
        )

    def test_an_agent_session_gets_the_box_identity(self) -> None:
        for marker in AGENT_MARKERS:
            with self.subTest(marker=marker):
                result = self.az({**self.plain, marker: SESSION}, "account", "show")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"config={self.box} args=account show\n")

    def test_the_gh_app_routing_include_counts_as_an_agent_session(self) -> None:
        env = {
            **self.plain,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "include.path",
            "GIT_CONFIG_VALUE_0": "/x/gh-app-routes.gitconfig",
        }
        self.assertEqual(self.az(env, "account", "show").stdout, f"config={self.box} args=account show\n")

    def test_a_plain_shell_is_left_alone(self) -> None:
        result = self.az(self.plain, "account", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "config=unset args=account show\n")

    def test_an_explicit_config_dir_is_kept(self) -> None:
        chosen = str(self.root / "chosen")
        for env in (self.plain, self.agent):
            with self.subTest(agent="OMP_SESSION_ID" in env):
                result = self.az({**env, "AZURE_CONFIG_DIR": chosen}, "account", "show")
                self.assertEqual(result.stdout, f"config={chosen} args=account show\n")
        result = self.az({**self.agent, "AZURE_CONFIG_DIR": ""}, "account", "show")
        self.assertEqual(result.stdout, "config= args=account show\n")

    def test_a_machine_with_no_box_identity_yet_logs_its_managed_identity_in(self) -> None:
        write_stub(self.stub_dir, "az", LOGIN_STUB)
        shutil.rmtree(self.box)
        result = self.az({**self.agent, "BOX_PROFILE": BOX_PROFILE}, "account", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"config={self.box} args=account show\n")
        self.assertEqual((self.box / "azureProfile.json").read_text(encoding="utf-8"), BOX_PROFILE)

    def test_no_box_identity_to_log_in_and_a_user_login_is_refused(self) -> None:
        (self.box / "azureProfile.json").unlink()
        result = self.az(self.agent, "account", "show")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("holds no box identity", result.stderr)

    def test_no_box_identity_and_no_user_login_passes_through(self) -> None:
        (self.box / "azureProfile.json").unlink()
        (self.home / ".azure" / "azureProfile.json").unlink()
        result = self.az(self.agent, "account", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "config=unset args=account show\n")

    def test_it_never_execs_itself(self) -> None:
        other = self.root / "other-shims" / "shims"
        other.mkdir(parents=True)
        (other / "az").symlink_to(SHIMS / "az")
        env = {
            **self.agent,
            "PATH": f"{SHIMS}{os.pathsep}{other}{os.pathsep}{self.stub_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        }
        result = self.az(env, "version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"config={self.box} args=version\n")

    def google_vm(self, **extra: str) -> dict[str, str]:
        """An agent session on a Google VM whose az signs in as agent-azure (FEDERATED_STUB)."""
        stub = MetadataStub.start()
        self.addCleanup(stub.stop)
        write_stub(self.stub_dir, "az", FEDERATED_STUB)
        self.login_log = self.root / "login.log"
        return {**self.agent, "GCE_METADATA_HOST": stub.host, "LOGIN_LOG": str(self.login_log),
                "SP_PROFILE": SP_PROFILE, **extra}

    def logins(self) -> list[list[str]]:
        if not self.login_log.exists():
            return []
        calls, call = [], []
        for line in self.login_log.read_text(encoding="utf-8").splitlines():
            call.append(line)
            if line.startswith("STDIN="):
                calls.append(call)
                call = []
        return calls

    def test_a_google_vm_signs_agent_azure_in_with_the_metadata_id_token(self) -> None:
        """No managed identity on a Google VM: `az login --service-principal --federated-token`
        with the VM account's Google ID token for audience api://AzureADTokenExchange, read from the
        metadata server and passed on stdin, so no token is ever on a command line. The box dir
        left by the Azure machine's managed identity (BOX_PROFILE) is replaced, not used."""
        env = self.google_vm(AGENT_AZURE_CLIENT_ID=CLIENT_ID)
        result = self.az(env, "account", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"config={self.box} args=account show\n")
        token = id_token("api://AzureADTokenExchange")
        self.assertEqual(self.logins(), [[
            "login", "--service-principal", "--username", CLIENT_ID, "--tenant", TENANT,
            "--federated-token", "@/dev/stdin", "--only-show-errors", "--output", "none", f"STDIN={token}",
        ]])
        self.assertEqual((self.box / "azureProfile.json").read_text(encoding="utf-8"), SP_PROFILE)

    def test_the_real_az_parser_reads_the_token_the_shim_passes_on_stdin(self) -> None:
        """The stub above reads stdin itself, so it cannot tell `@-` from a working argument. Run
        the installed Azure CLI's own `@file` expansion (knack's CLICommandParser) on the argv the
        shim gave `az login`, with the token on stdin: it must come out as the token."""
        python = real_az_python()
        if python is None:
            self.skipTest("no installed Azure CLI with knack on PATH")
        env = self.google_vm(AGENT_AZURE_CLIENT_ID=CLIENT_ID)
        self.assertEqual(self.az(env, "account", "show").returncode, 0)
        argv = self.logins()[0][:-1]
        token = id_token("api://AzureADTokenExchange")
        code = ("import json, sys\n"
                "from knack.parser import CLICommandParser\n"
                "print(json.dumps(CLICommandParser._expand_prefixed_files(sys.argv[1:])))\n")
        out = subprocess.run([python, "-c", code, *argv], input=token, capture_output=True, text=True,
                             timeout=60, check=True).stdout
        expanded = json.loads(out)
        self.assertEqual(expanded[expanded.index("--federated-token") + 1], token)

    def test_a_google_vm_signs_in_again_once_the_last_sign_in_is_20_minutes_old(self) -> None:
        env = self.google_vm(AGENT_AZURE_CLIENT_ID=CLIENT_ID)
        for _ in range(3):
            self.assertEqual(self.az(env, "account", "show").returncode, 0)
        self.assertEqual(len(self.logins()), 1)
        stamp = self.box / ".agent-azure-login"
        old = time.time() - 21 * 60
        os.utime(stamp, (old, old))
        self.assertEqual(self.az(env, "account", "show").returncode, 0)
        self.assertEqual(len(self.logins()), 2)

    def test_a_google_vm_reads_the_client_id_file(self) -> None:
        id_file = self.home / ".config" / "agent-azure" / "client-id"
        id_file.parent.mkdir(parents=True)
        id_file.write_text(CLIENT_ID + "\n", encoding="utf-8")
        result = self.az(self.google_vm(), "account", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.logins()[0][3], CLIENT_ID)

    def test_a_google_vm_without_the_client_id_stops_and_says_where_it_goes(self) -> None:
        result = self.az(self.google_vm(), "account", "show")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("AGENT_AZURE_CLIENT_ID", result.stderr)
        self.assertIn(str(self.home / ".config" / "agent-azure" / "client-id"), result.stderr)
        self.assertNotIn("No such file", result.stderr)
        self.assertEqual(self.logins(), [])

    def test_a_google_vm_whose_sign_in_fails_stops_with_azures_reason(self) -> None:
        result = self.az(self.google_vm(AGENT_AZURE_CLIENT_ID=CLIENT_ID, LOGIN_FAILS="1"), "account", "show")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("AADSTS700213", result.stderr)
        self.assertFalse((self.box / ".agent-azure-login").exists())

    def test_a_google_vm_plain_shell_is_left_alone(self) -> None:
        env = {**self.google_vm(AGENT_AZURE_CLIENT_ID=CLIENT_ID)}
        del env["OMP_SESSION_ID"]
        result = self.az(env, "account", "show")
        self.assertEqual(result.stdout, "config=unset args=account show\n")
        self.assertEqual(self.logins(), [])


class AzShimNonInteractiveSsh(unittest.TestCase):
    """INFRA-1538: `ssh sami-devbox az account show` is an agent's command, not Sami's. The shim
    takes the box identity for it and names the config dir it ended on; a person's own SSH
    session (sshd gave it a terminal, so SSH_TTY is set, whatever one command reads on stdin) —
    and a person who sets AGENT_SHIM_PERSON=1 — keep their own login."""

    setUp = AzShim.setUp
    tearDown = AzShim.tearDown
    NOTICE = "az shim: non-interactive SSH, so an agent's call: AZURE_CONFIG_DIR="

    def az(self, env: dict[str, str], *args: str, tty: bool = False) -> subprocess.CompletedProcess[str]:
        run = run_with_tty if tty else run_without_tty
        return run([str(SHIMS / "az"), *args], cwd=self.root, env=env, timeout=TIMEOUT)

    def ssh(self, **extra: str) -> dict[str, str]:
        return {**self.plain, "SSH_CONNECTION": SSH_CONNECTION, **extra}

    def test_a_command_over_ssh_with_no_terminal_gets_the_box_identity(self) -> None:
        result = self.az(self.ssh(), "account", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"config={self.box} args=account show\n")
        # The notice names the directory the call ended on, not a machine identity it has not
        # selected (review of INFRA-1538).
        self.assertIn(f"{self.NOTICE}{self.box}", result.stderr)
        self.assertIn("AGENT_SHIM_PERSON=1", result.stderr)

    def test_a_persons_ssh_session_is_the_person(self) -> None:
        for stdin_is_a_pipe in (True, False):
            with self.subTest(piped=stdin_is_a_pipe):
                result = self.az(self.ssh(SSH_TTY=SSH_TTY), "account", "show", tty=not stdin_is_a_pipe)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "config=unset args=account show\n")
                self.assertNotIn(self.NOTICE, result.stderr)

    def test_agent_shim_person_keeps_the_persons_own_login(self) -> None:
        result = self.az(self.ssh(AGENT_SHIM_PERSON="1"), "account", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "config=unset args=account show\n")
        self.assertNotIn(self.NOTICE, result.stderr)

    def test_a_session_marker_needs_no_notice(self) -> None:
        result = self.az({**self.agent, "SSH_CONNECTION": SSH_CONNECTION}, "account", "show")
        self.assertEqual(result.stdout, f"config={self.box} args=account show\n")
        self.assertNotIn(self.NOTICE, result.stderr)


def real_az_python() -> str | None:
    """The Python of the first real (non-shim) Azure CLI on PATH, when it has knack."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        az = Path(d) / "az"
        if not az.is_file() or az.resolve() == (SHIMS / "az").resolve():
            continue
        try:
            first = az.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except (OSError, IndexError):
            continue
        if first.startswith("#!") and "python" in first:
            python = first[2:].split()[0]
            if subprocess.run([python, "-c", "import knack.parser"], capture_output=True).returncode == 0:
                return python
    return None


if __name__ == "__main__":
    unittest.main()
