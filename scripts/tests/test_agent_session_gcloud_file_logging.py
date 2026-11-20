#!/usr/bin/env python3
"""Every agent session runs gcloud with file logging off, the shim's path or not (dispatch://INFRA-4824).

gcloud copies each command's output into a file under its configuration directory's logs/, so a
token it prints lands on disk. shims/gcloud and installers/gcloud-sdk.sh turn that off for the shim's
callers and for ~/.gcloud's SDK; a gcloud run by its path past both, such as a Google VM's snap
/snap/bin/gcloud, read neither. scripts/agent-session (every host launcher) and session_env in
scripts/agentbox (every box) export CLOUDSDK_CORE_DISABLE_FILE_LOGGING=true, which gcloud reads before
any configuration file. A value the caller set is kept, as the shim keeps it.

Technique: as test_agent_session_gotmp.py, agent-session launches a stand-in agent that prints its
environment; where a real gcloud outside ~/.gcloud is installed (/snap/bin/gcloud), the agent runs it
by its path against a made-up token in a scratch CLOUDSDK_CONFIG and lists the log files it wrote.
session_env is sourced from scripts/agentbox and its `-e` arguments read, as test_agentbox_storage.py
does.
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "scripts" / "agent-session"
AGENTBOX = DOTFILES / "scripts" / "agentbox"
USER = "agent-session-gcloud-logging-test-user"
SNAP_GCLOUD = Path("/snap/bin/gcloud")
TOKEN = "file-logging-test-not-a-credential"


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentSessionGcloudFileLogging(unittest.TestCase):
    root: Path
    base: dict[str, str]

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-session-gcloud-logging-"))
        self.addCleanup(shutil.rmtree, self.root)
        dotfiles = self.root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        (dotfiles / "shims").mkdir()
        (dotfiles / "agent-spend").mkdir()
        executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        if (DOTFILES / "scripts" / "agent-spend-env.sh").exists():
            shutil.copy(DOTFILES / "scripts" / "agent-spend-env.sh", dotfiles / "scripts" / "agent-spend-env.sh")
        nvme = self.root / "nvme" / USER
        nvme.mkdir(parents=True)
        self.base = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "USER": USER,
            "DOTFILES_DIR": str(dotfiles),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "AGENT_SESSION_NVME_HOME": str(nvme),
        }

    def launch(self, body: str, env: dict[str, str] | None = None) -> str:
        agent = self.root / "omp"
        executable(agent, body)
        base = dict(self.base)
        result = subprocess.run([str(SCRIPT), str(agent)], env={**base, **(env or {})}, cwd=self.root,
                                capture_output=True, text=True, timeout=300, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_the_session_exports_file_logging_off(self) -> None:
        out = self.launch('printf "LOGGING=%s\\n" "${CLOUDSDK_CORE_DISABLE_FILE_LOGGING-<unset>}"')
        self.assertIn("LOGGING=true", out.splitlines())

    def test_a_value_the_caller_set_is_kept(self) -> None:
        out = self.launch('printf "LOGGING=%s\\n" "${CLOUDSDK_CORE_DISABLE_FILE_LOGGING-<unset>}"',
                          {"CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "false"})
        self.assertIn("LOGGING=false", out.splitlines())

    @unittest.skipUnless(SNAP_GCLOUD.exists(), "no /snap/bin/gcloud")
    def test_a_direct_call_to_the_snap_gcloud_writes_no_log_file(self) -> None:
        config = self.root / "gcloud-config"
        config.mkdir()
        token = self.root / "token"
        token.write_text(TOKEN, encoding="utf-8")
        body = (
            f'CLOUDSDK_CONFIG={shlex.quote(str(config))} CLOUDSDK_AUTH_ACCESS_TOKEN_FILE={shlex.quote(str(token))} '
            f'{SNAP_GCLOUD} auth print-access-token > {shlex.quote(str(self.root / "printed"))}\n'
            f'find {shlex.quote(str(config))} -path "*/logs/*" -type f -printf "LOG=%p\\n"'
        )
        out = self.launch(body)
        self.assertEqual((self.root / "printed").read_text(encoding="utf-8").strip(), TOKEN,
                         "the snap gcloud printed no token, so nothing ran")
        self.assertEqual([line for line in out.splitlines() if line.startswith("LOG=")], [])


class AgentboxSessionEnvGcloudFileLogging(unittest.TestCase):
    def run_session_env(self, **env: str) -> list[str]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        base = {**os.environ, "AGENTBOX_IDENTITY_FILE": str(root / "not-a-box"), "AGENTBOX_BOX": "",
                "AGENTBOX_HOST": "sami-devbox", "AGENTBOX_BOXES": str(root / "boxes"),
                "AGENTBOX_DISKS": str(root / "disks"), "AGENTBOX_CACHE_ROOT": str(root / "box-cache"),
                "XDG_CONFIG_HOME": str(root / "config"), "DOTFILES_DIR": str(AGENTBOX.parents[1])}
        base.pop("CLOUDSDK_CORE_DISABLE_FILE_LOGGING", None)
        body = ("NETWORK_GATEWAY=172.31.0.1; forward_channels() { :; }; loopback_services() { :; }; "
                "session_env agentbox-loggingtest")
        result = subprocess.run(["bash", "-c", f"source {shlex.quote(str(AGENTBOX))}\n{body}"],
                                env={**base, **env}, text=True, capture_output=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_a_box_session_gets_file_logging_off(self) -> None:
        self.assertIn("CLOUDSDK_CORE_DISABLE_FILE_LOGGING=true", self.run_session_env())

    def test_the_launchers_own_value_is_kept(self) -> None:
        args = self.run_session_env(CLOUDSDK_CORE_DISABLE_FILE_LOGGING="false")
        self.assertIn("CLOUDSDK_CORE_DISABLE_FILE_LOGGING=false", args)
        self.assertNotIn("CLOUDSDK_CORE_DISABLE_FILE_LOGGING=true", args)


if __name__ == "__main__":
    unittest.main()
