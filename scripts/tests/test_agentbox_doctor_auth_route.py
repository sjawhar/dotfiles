#!/usr/bin/env python3
"""Doctor's probes ask the route a box uses: Docker Hub's configured credential route, not a hardcoded broker
helper, and the build pool's Job right on the Kueue MultiKueue manager, where a box creates its build Jobs
(dispatch://INFRA-5858, R3)."""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts/agentbox"
HUB = "https://index.docker.io/v1/"
SECRET = "fixture-doctor-secret-not-a-live-token"
MANAGER = "gke_theorem-agent-infra_us-central1_theorem-multikueue"
GKE_ASK = f"--context {MANAGER} -n builds auth can-i create jobs"


class DoctorAuthRoute(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        (self.home / ".docker").mkdir()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.log = self.home / "helper.log"
        self.env = {**os.environ, "HOME": str(self.home), "DOCKER_CONFIG": str(self.home / ".docker"),
                    "DOTFILES_DIR": str(DOTFILES), "PATH": f"{self.bin}:/usr/bin:/bin",
                    "AGENT_SECRETS_URL": "https://broker.example", "HELPER_LOG": str(self.log)}
        extracted = subprocess.run(
            ["bash", "-c", f'source "{AGENTBOX}"; printf "%s" "$PROBE_FUNCS"'],
            capture_output=True, text=True, timeout=30, env=self.env,
        )
        self.assertEqual(extracted.returncode, 0, extracted.stderr)
        self.functions = extracted.stdout

    def helper(self, name: str, status: int = 0) -> None:
        path = self.bin / f"docker-credential-{name}"
        response = shlex.quote(json.dumps({"Username": "fixture", "Secret": SECRET}))
        path.write_text('#!/bin/sh\nIFS= read -r registry || [ -n "$registry" ]\n'
                        'printf "%s %s %s\\n" "${0##*/}" "$1" "$registry" >>"$HELPER_LOG"\n'
                        f"printf '%s\\n' {response}\nexit {status}\n")
        path.chmod(0o755)

    def probe(self, config: dict) -> subprocess.CompletedProcess[str]:
        path = self.home / ".docker/config.json"
        path.write_text(json.dumps(config))
        before = path.read_bytes()
        result = subprocess.run(["bash"], input=self.functions + "\nprobe_dockerhub\n",
                                capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        return result

    def test_global_store_is_the_route_that_gets_probed(self) -> None:
        self.helper("global")
        self.helper("secretsd")
        result = self.probe({"credsStore": "global"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [f"docker-credential-global get {HUB}"])

    def test_empty_helper_override_selects_inline_auth_not_global_store(self) -> None:
        self.helper("global")
        self.helper("secretsd")
        encoded = base64.b64encode(("fixture:" + SECRET).encode()).decode()
        result = self.probe({"credHelpers": {HUB: ""}, "credsStore": "global",
                             "auths": {HUB: {"auth": encoded}}})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.log.exists(), "inline auth needs no helper call")

    def test_helper_failure_is_not_hidden_by_a_valid_json_payload(self) -> None:
        self.helper("secretsd", status=17)
        result = self.probe({"credHelpers": {HUB: "secretsd"}})
        self.assertNotEqual(result.returncode, 0)

    def test_the_build_pool_probe_asks_the_manager(self) -> None:
        """kubectl answers yes only to the manager's builds, so a probe of another cluster fails."""
        path = self.bin / "kubectl"
        path.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"$HELPER_LOG"\n'
                        f'[ "$*" = "{GKE_ASK}" ] && {{ echo yes; exit 0; }}\necho no\nexit 1\n')
        path.chmod(0o755)
        result = subprocess.run(["bash"], input=self.functions + "\nprobe_gke\n",
                                capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "can create jobs in the builds namespace")
        self.assertEqual(self.log.read_text().splitlines(), [GKE_ASK])

    def test_the_command_doctor_shows_for_the_build_pool_asks_the_manager(self) -> None:
        shown = subprocess.run(["bash", "-c", f'source "{AGENTBOX}"; probe_command gke'],
                               capture_output=True, text=True, timeout=30, env=self.env)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown.stdout.strip(), f"kubectl {GKE_ASK}")


if __name__ == "__main__":
    unittest.main()
