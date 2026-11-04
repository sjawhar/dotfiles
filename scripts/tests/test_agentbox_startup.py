#!/usr/bin/env python3
"""Box startup changes only its own home directories, before user processes start."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
ENTRYPOINT = DOTFILES / "agentbox" / "entrypoint.sh"
SSH_CONFIG = DOTFILES / "agentbox" / "box-ssh-config"


class AgentboxStartup(unittest.TestCase):
    def test_passwd_home_precedes_home_ownership_and_mounts_are_not_chowned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            own = home / ".config"
            mounted = home / ".gcloud"
            own.mkdir(parents=True)
            (mounted / "sdk").mkdir(parents=True)
            # Both directories have the same st_dev. Bind mounts cannot be detected by device.
            self.assertEqual(own.stat().st_dev, mounted.stat().st_dev)
            prefix = ENTRYPOINT.read_text().split("mkdir -p /var/log", 1)[0]
            setup = r'''
getent() { printf 'ubuntu:x:1000:1000::/home/ubuntu:/bin/bash\n'; }
usermod() { printf 'usermod %s\n' "$*" >>"$EVENTS"; }
chown() { printf 'chown %s\n' "$*" >>"$EVENTS"; }
findmnt() { printf '%s\n' "$HOME" "$HOME/.gcloud"; }
'''
            events = root / "events"
            result = subprocess.run(
                ["bash"], input=setup + prefix, capture_output=True, text=True, timeout=30,
                env={**os.environ, "HOME": str(home), "EVENTS": str(events),
                     "AGENTBOX_USER": "ubuntu", "AGENTBOX_IMDS_FORWARDER": "172.31.0.1:8169",
                     "AGENTBOX_ENVOY_FORWARDER": "172.31.0.1:9020"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = events.read_text().splitlines()
            self.assertEqual(lines[0], f"usermod -d {home} ubuntu")
            self.assertIn(f"chown ubuntu:ubuntu {own}", lines)
            self.assertFalse(any(str(mounted) in line for line in lines), lines)

    def test_ssh_config_gives_the_user_its_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir()
            binary = root / "bin"
            binary.mkdir()
            chown = binary / "chown"
            chown.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >>"$EVENTS"\n')
            chown.chmod(0o755)
            events = root / "events"
            result = subprocess.run(
                [str(SSH_CONFIG), str(home), "ubuntu"], capture_output=True, text=True, timeout=30,
                env={**os.environ, "PATH": f"{binary}:/usr/bin:/bin", "EVENTS": str(events),
                     "AGENTBOX_SSH_SYSTEM_DIR": str(root / "etc")},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = events.read_text().splitlines()
            self.assertIn(f"ubuntu:ubuntu {home}/.ssh {home}/.ssh/config.d", lines)

    def test_docker_startup_preserves_host_routes_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docker_dir = root / ".docker"
            docker_dir.mkdir()
            original = {
                "auths": {"ghcr.io": {"auth": "fixture-only"}},
                "credsStore": "host-store",
                "credHelpers": {"private.example": "host-helper"},
                "currentContext": "host-context",
            }
            config = docker_dir / "config.json"
            config.write_text(json.dumps(original))
            binary = root / "bin"
            binary.mkdir()
            for name in ("docker-credential-secretsd", "docker-credential-gcloud"):
                helper = binary / name
                helper.write_text("#!/bin/sh\nexit 0\n")
                helper.chmod(0o755)
            source = ENTRYPOINT.read_text()
            block = source.split("# Private agent-c images:", 1)[1].split("# An ssh config", 1)[0]
            # Restore the opening comment after extracting the production startup block.
            block = "# Private agent-c images:" + block
            setup = r'''
setpriv() { while [ "$1" != env ]; do shift; done; "$@"; }
secrets() { echo unexpected-login >&2; return 1; }
'''
            result = subprocess.run(
                ["bash"], input=setup + block, capture_output=True, text=True, timeout=30,
                env={**os.environ, "HOME": str(root), "PATH": f"{binary}:/usr/bin:/bin",
                     "AGENTBOX_USER": "ubuntu", "AGENTBOX_CRED_HELPERS":
                     '{"us-west1-docker.pkg.dev":"gcloud"}'},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(config.read_text()), original)
            self.assertNotIn("unexpected-login", result.stderr)
            self.assertNotIn("ghcr.io login failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
