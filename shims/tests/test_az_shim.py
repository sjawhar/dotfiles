#!/usr/bin/env python3
"""shims/az points az at the box identity in an agent session and nowhere else.

Technique: a stub `az` in a dir after the shims dir on PATH, printing the AZURE_CONFIG_DIR it
was given, and a fake HOME holding a box identity profile and a user login profile.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).resolve().parents[1]
SESSION = "01a0fa64-b3a5-7415-b143-c2a338b5da8b"
AGENT_MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION")
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


if __name__ == "__main__":
    unittest.main()
