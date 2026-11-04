#!/usr/bin/env python3
"""Internal readiness targets the box daemon without changing the user's Docker context."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
ENTRYPOINT = DOTFILES / "agentbox/entrypoint.sh"
AGENTBOX = DOTFILES / "scripts/agentbox"


class InnerDockerContext(unittest.TestCase):
    def run_probe(self, body: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / ".docker").mkdir()
            config = home / ".docker/config.json"
            original = {"currentContext": "host-remote", "contexts": "fixture"}
            config.write_text(json.dumps(original))
            setup = r'''
seq() { printf '1\n'; }
sleep() { :; }
tail() { :; }
docker() { [ "$*" = '--host=unix:///var/run/docker.sock info' ]; }
timeout() { shift; "$@"; }
ok() { echo "ok $*"; }
fail() { echo "FAIL $*"; return 1; }
'''
            result = subprocess.run(
                ["bash"], input=setup + body, capture_output=True, text=True, timeout=30,
                env={**os.environ, "HOME": str(home), "DOCKER_CONTEXT": "host-remote"},
            )
            self.assertEqual(json.loads(config.read_text()), original)
            return result

    def test_startup_readiness_does_not_probe_the_stored_remote_context(self) -> None:
        source = ENTRYPOINT.read_text()
        body = "for _ in $(seq 1 60); do" + source.split("for _ in $(seq 1 60); do", 1)[1]
        body = body.split("chmod 666 /var/run/docker.sock", 1)[0]
        result = self.run_probe(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_doctor_checks_the_inner_daemon_not_the_selected_context(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'source "{AGENTBOX}"; printf "%s" "$DOCTOR_BOX_SCRIPT"'],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "DOTFILES_DIR": str(DOTFILES), "AGENT_SECRETS_URL": "https://broker.example"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        line = next(line for line in result.stdout.splitlines() if 'ok "inner docker answers"' in line)
        checked = self.run_probe(line + "\n")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertNotIn("FAIL", checked.stdout)


if __name__ == "__main__":
    unittest.main()
