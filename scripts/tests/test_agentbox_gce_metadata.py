#!/usr/bin/env python3
"""scripts/agentbox points a box's Google clients at the metadata relay on a Google VM only.

The box reaches the host's metadata service through the agentbox-imds relay at the box network's
gateway; on a Google VM that service is Google's metadata server, and gcloud reads
GCE_METADATA_ROOT, google-auth GCE_METADATA_HOST (then GCE_METADATA_ROOT) and GCE_METADATA_IP, Go
GCE_METADATA_HOST. On any other machine none of them may be set.

Technique, as test_agentbox_envoy.py: source scripts/agentbox (its source guard runs nothing) with
`docker`, `git`, `agent-secrets`, `curl` and `sleep` stubbed first on PATH, and run cmd_new with the
steps that need a real host replaced by no-ops; session_env is the real one. The curl stub hands a
metadata-server URL to the real curl, so the real scripts/on-google-vm decides against
gce_metadata_stub.py (a Google VM) or a closed port (not one), both named by GCE_METADATA_HOST.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from gce_metadata_stub import MetadataStub

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
GATEWAY = "172.31.0.1"
RELAY = f"{GATEWAY}:8169"
GCE_VARS = ("GCE_METADATA_HOST", "GCE_METADATA_ROOT", "GCE_METADATA_IP")


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


DOCKER_STUB = r"""
[[ "$1" == run ]] && { shift; printf '%s\n' "$@" >"$STUB_RUN_ARGS"; }
exit 0
"""
CURL_STUB = r"""
case "$*" in *computeMetadata*) exec /usr/bin/curl "$@" ;; esac
exit 7
"""
NO_HOST = (
    "image_tag() { echo agentbox:test; }; build_image() { :; }; ensure_forwarders() { :; }\n"
    f"ensure_network() {{ NETWORK_GATEWAY={GATEWAY} NETWORK_SUBNET=172.31.0.0/16; }}\n"
    "mount_args() { :; }; clone_checkout() { :; }; close_box() { :; }\n"
)


class AgentboxGceMetadata(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        home = self.root / "home"
        (home / ".config").mkdir(parents=True)
        (self.root / "src" / "repo1").mkdir(parents=True)
        stubs = self.root / "bin"
        write_stub(stubs, "docker", DOCKER_STUB)
        write_stub(stubs, "git", "exit 0")
        write_stub(stubs, "curl", CURL_STUB)
        write_stub(stubs, "sleep", "exit 0")
        write_stub(stubs, "agent-secrets", "echo none; exit 1")
        dotfiles = self.root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        (dotfiles / "scripts" / "on-google-vm").symlink_to(DOTFILES / "scripts" / "on-google-vm")
        (dotfiles / "agent-secrets").symlink_to(DOTFILES / "agent-secrets")
        self.run_args = self.root / "run-args"
        self.env = {
            "HOME": str(home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(dotfiles),
            "AGENTBOX_SRC": str(self.root / "src"),
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "AGENTBOX_DISK": "0",
            "AGENT_SECRETS_URL": "https://broker.example",
            "STUB_RUN_ARGS": str(self.run_args),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def box_env(self, metadata_host: str) -> dict[str, str]:
        """The -e values `docker run` got, as a name -> value map."""
        script = f'source "{AGENTBOX}"\n{NO_HOST}cmd_new repo1 -- omp\n'
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, timeout=60,
                                env={**self.env, "GCE_METADATA_HOST": metadata_host})
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.run_args.read_text(encoding="utf-8").splitlines()
        values = [args[i + 1] for i, arg in enumerate(args) if arg == "-e"]
        return dict(value.split("=", 1) for value in values)

    def test_a_google_vm_box_reaches_the_metadata_server_through_the_relay(self) -> None:
        stub = MetadataStub.start()
        self.addCleanup(stub.stop)
        env = self.box_env(stub.host)
        self.assertEqual({name: env.get(name) for name in GCE_VARS}, dict.fromkeys(GCE_VARS, RELAY))
        self.assertEqual(env["AGENTBOX_IMDS_FORWARDER"], RELAY)

    def test_a_box_on_any_other_machine_gets_none_of_them(self) -> None:
        env = self.box_env("127.0.0.1:9")
        self.assertEqual([name for name in GCE_VARS if name in env], [])
        self.assertEqual(env["AGENTBOX_IMDS_FORWARDER"], RELAY)


if __name__ == "__main__":
    unittest.main()
