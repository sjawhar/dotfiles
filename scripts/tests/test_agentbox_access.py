#!/usr/bin/env python3
"""Host disk access and private Docker snapshots from the real mount builder."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import test_agentbox_dirs as directory_tests


class AgentboxAccess(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = directory_tests.AgentboxDirs(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.nvme = self.fixture.root / "nvme-default"
        self.nvme.mkdir()

    def binds(self, *arguments: str) -> list[str]:
        return self.fixture.binds_of(self.fixture.run_args_of(*arguments))

    def test_only_durable_storage_is_shared_as_a_whole_by_default(self) -> None:
        durable = self.fixture.root / "durable"
        durable.mkdir()
        binds = self.binds("--dir", str(self.fixture.lane))
        self.assertNotIn(f"{self.nvme}:{self.nvme}", binds)
        self.assertIn(f"{durable}:{durable}", binds)

    def test_named_nvme_children_are_shared_without_granting_the_parent_tree(self) -> None:
        for name in ("wt", "devbox-check-state", "oss-verifier/ssh"):
            child = self.nvme / name
            child.mkdir(parents=True)
            with self.subTest(name):
                binds = self.binds("--dir", str(child))
                self.assertNotIn(f"{self.nvme}:{self.nvme}", binds)
                self.assertIn(f"{child}:{child}", binds)

    def test_config_and_ssh_are_writable_and_jj_has_no_private_overlay(self) -> None:
        binds = self.binds("--dir", str(self.fixture.lane))
        for relative in (".agents", ".ssh", ".gnupg", ".gcloud", ".config/jj", ".config/legion",
                         ".config/gh", ".azure", ".azure-box-identity", ".config/systemd/user"):
            path = self.fixture.home / relative
            self.assertIn(f"{path}:{path}", binds)
            self.assertNotIn(f"{path}:{path}:ro", binds)
        self.assertFalse(any("jj-user-repos" in bind for bind in binds), binds)

    def test_docker_state_is_complete_and_private(self) -> None:
        source = self.fixture.home / ".docker"
        config = {"auths": {"private.example": {"auth": "fixture-only"}},
                  "credsStore": "fixture-store", "currentContext": "fixture-context"}
        (source / "config.json").write_text(json.dumps(config))
        certificate = source / "contexts" / "tls" / "fixture" / "cert.pem"
        certificate.parent.mkdir(parents=True)
        certificate.write_text("fixture certificate")
        binds = self.binds("--dir", str(self.fixture.lane))
        private_bind = next(bind for bind in binds if bind.split(":")[1:2] == [str(source)])
        snapshot = Path(private_bind.split(":")[0])
        self.assertNotEqual(snapshot, source)
        self.assertEqual(json.loads((snapshot / "config.json").read_text()), config)
        self.assertEqual((snapshot / "contexts/tls/fixture/cert.pem").read_text(), "fixture certificate")
        (snapshot / "config.json").write_text("{}")
        self.assertEqual(json.loads((source / "config.json").read_text()), config)

    def test_docker_external_symlink_targets_remain_accessible(self) -> None:
        outside = self.fixture.root / "docker-certificates"
        outside.mkdir()
        (outside / "cert.pem").write_text("fixture")
        (self.fixture.home / ".docker" / "certs.d").symlink_to(outside)
        binds = self.binds("--dir", str(self.fixture.lane))
        self.assertTrue(any(bind.startswith(f"{outside}:/opt/agentbox/docker-external/") for bind in binds), binds)

    def test_external_host_helper_keeps_its_command_and_input(self) -> None:
        directory_tests.write_stub(self.fixture.root / "bin", "jq", 'exec /usr/bin/jq "$@"')
        external = self.fixture.root / "external-bin"
        directory_tests.write_stub(external, "docker-credential-outside",
                                   'IFS= read -r registry; printf "%s:%s\\n" "$1" "$registry"')
        helper = external / "docker-credential-outside"
        self.fixture.env["PATH"] = f"{external}:{self.fixture.env['PATH']}"
        (self.fixture.home / ".docker/config.json").write_text('{"credsStore":"outside"}')
        binds = self.binds("--dir", str(self.fixture.lane))
        payload = next(bind for bind in binds if bind.startswith(f"{helper}:/opt/agentbox/docker-helper-bin/"))
        boxed = payload.split(":")[1]
        self.assertTrue(boxed.endswith("/docker-credential-outside"), payload)
        helper_bind = next(bind for bind in binds if bind.endswith(":/opt/agentbox/docker-helpers"))
        helper_directory = Path(helper_bind.split(":")[0])
        self.assertIn(boxed, (helper_directory / "docker-credential-outside").read_text())
        result = subprocess.run([str(helper), "get"],
                                input="registry.example\n", capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "get:registry.example\n")
        args = self.fixture.run_args.read_text().splitlines()
        self.assertIn("/opt/agentbox/docker-helpers", self.fixture.env_of(args)["PATH"].split(":"))


if __name__ == "__main__":
    unittest.main()
