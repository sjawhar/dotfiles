#!/usr/bin/env python3
"""Acceptance follows Docker's selected credential route without writing credentials."""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

CHECK = Path(__file__).resolve().parents[2] / "devbox/sami-devbox-check"
AR = "us-east1-docker.pkg.dev"
SECRET = "fixture-secret-not-a-live-token"


class RegistryAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.root / ".docker").mkdir()
        self.config = self.root / ".docker/config.json"
        self.log = self.root / "helper.log"
        self.stub("secrets", "exit 1")
        self.helper("gcloud", status=99)
        self.source = CHECK.read_text()
        marker = '\ncase "${1:-}" in'
        self.assertIn(marker, self.source)
        self.prefix = self.source.split(marker, 1)[0]

    def stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def helper(self, name: str, *, status: int = 0, username: str = "fixture") -> None:
        response = shlex.quote(json.dumps({"Username": username, "Secret": SECRET}))
        self.stub(
            f"docker-credential-{name}",
            '[ "$1" = get ] || exit 98\n'
            'IFS= read -r registry || [ -n "$registry" ]\n'
            'printf "%s %s %s\\n" "${0##*/}" "$1" "$registry" >>"$HELPER_LOG"\n'
            f"printf '%s\\n' {response}\nexit {status}",
        )

    def run_check(self, kind: str, config: dict) -> subprocess.CompletedProcess[str]:
        self.config.write_text(json.dumps(config))
        original = self.config.read_bytes()
        if kind == "ar":
            body = "    # Artifact Registry:" + self.source.split("    # Artifact Registry:", 1)[1]
            body = body.split("    moved_gcloud_config_check", 1)[0]
        else:
            tools = self.source.split("tools_check() {", 1)[1]
            body = tools.split("    knives_check\n", 1)[1].split("    # agent.db", 1)[0]
        result = subprocess.run(
            ["bash"], input=self.prefix + '\nout=""; got=""\n' + body + '\nprintf "fails=%s\\n" "$fails"\n',
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HOME": str(self.root), "DOCKER_CONFIG": str(self.root / ".docker"),
                 "DOTFILES_DIR": str(CHECK.parent.parent),
                 "PATH": f"{self.bin}:/usr/bin:/bin", "HELPER_LOG": str(self.log)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_bytes(), original, "acceptance must not write Docker state")
        self.assertNotIn(SECRET, result.stdout + result.stderr, "native protocol secrets must not enter diagnostics")
        return result

    def assert_ready(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotIn("FAIL", result.stdout)
        self.assertNotIn("WAIT", result.stdout)
        self.assertIn("ok", result.stdout)
        self.assertIn("fails=0", result.stdout)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_artifact_registry_uses_specific_helper_before_global_store(self) -> None:
        self.helper("specific")
        self.helper("global", status=97)
        result = self.run_check("ar", {"credHelpers": {AR: "specific"}, "credsStore": "global"})
        self.assert_ready(result)
        self.assertEqual(self.calls(), [f"docker-credential-specific get {AR}"])

    def test_artifact_registry_uses_global_store_native_protocol(self) -> None:
        self.helper("global")
        result = self.run_check("ar", {"credsStore": "global", "auths": {AR: {}}})
        self.assert_ready(result)
        self.assertEqual(self.calls(), [f"docker-credential-global get {AR}"])

    def test_artifact_registry_accepts_inline_auth(self) -> None:
        auth = base64.b64encode(("fixture:" + SECRET).encode()).decode()
        self.assert_ready(self.run_check("ar", {"auths": {AR: {"auth": auth}}}))
        self.assertEqual(self.calls(), [])

    def test_explicit_empty_helper_uses_file_auth_instead_of_global_store(self) -> None:
        self.helper("global", status=97)
        auth = base64.b64encode(("fixture:" + SECRET).encode()).decode()
        self.assert_ready(self.run_check("ar", {"credHelpers": {AR: ""}, "credsStore": "global",
                                                "auths": {AR: {"auth": auth}}}))
        self.assertEqual(self.calls(), [])

    def test_inline_identity_token_is_supported(self) -> None:
        self.assert_ready(self.run_check("ar", {"auths": {AR: {"identitytoken": SECRET}}}))
        self.assertEqual(self.calls(), [])

    def test_incomplete_native_response_fails(self) -> None:
        self.helper("gcloud", username="")
        result = self.run_check("ar", {"credHelpers": {AR: "gcloud"}})
        self.assertIn("FAIL", result.stdout)

    def test_failed_native_get_does_not_print_its_payload(self) -> None:
        self.helper("gcloud", status=17)
        result = self.run_check("ar", {"credHelpers": {AR: "gcloud"}})
        self.assertIn("FAIL", result.stdout)

    def test_ghcr_accepts_specific_helper_and_global_store(self) -> None:
        self.helper("specific")
        self.helper("global")
        for config in ({"credHelpers": {"ghcr.io": "specific"}}, {"credsStore": "global"}):
            with self.subTest(config=config):
                self.assert_ready(self.run_check("ghcr", config))

    def test_ghcr_accepts_inline_auth(self) -> None:
        auth = base64.b64encode(("fixture:" + SECRET).encode()).decode()
        self.assert_ready(self.run_check("ghcr", {"auths": {"ghcr.io": {"auth": auth}}}))

    def test_missing_ghcr_credentials_preserve_the_existing_wait(self) -> None:
        result = self.run_check("ghcr", {})
        self.assertIn("WAIT docker", result.stdout)
        self.assertNotIn("FAIL", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_empty_ghcr_helper_does_not_fall_through_to_global_store(self) -> None:
        self.helper("global")
        result = self.run_check("ghcr", {"credHelpers": {"ghcr.io": ""}, "credsStore": "global"})
        self.assertIn("WAIT docker", result.stdout)
        self.assertNotIn("FAIL", result.stdout)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
