#!/usr/bin/env python3
"""Docker snapshots honor their source and cannot replace reserved box destinations."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import test_agentbox_dirs as directory_tests


class DockerSnapshot(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = directory_tests.AgentboxDirs(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.source = self.fixture.home / ".docker"

    def launch(self) -> tuple[list[str], Path]:
        args = self.fixture.run_args_of("--dir", str(self.fixture.lane))
        binds = self.fixture.binds_of(args)
        selected = [bind for bind in binds if bind.split(":")[1:2] == [str(self.source)]]
        if selected:
            snapshot = Path(selected[0].split(":")[0])
        else:
            home_bind = next(bind for bind in binds if bind.split(":")[1:2] == [str(self.fixture.home)])
            snapshot = Path(home_bind.split(":")[0]) / ".docker"
        return binds, snapshot

    def test_effective_host_config_is_copied_into_private_box_config(self) -> None:
        (self.source / "config.json").write_text('{"currentContext":"wrong-default"}')
        effective = self.fixture.root / "docker-custom"
        effective.mkdir()
        expected = {"currentContext": "selected-host-context", "credHelpers": {"registry.example": "custom"}}
        (effective / "config.json").write_text(json.dumps(expected))
        self.fixture.env["DOCKER_CONFIG"] = str(effective)
        _, snapshot = self.launch()
        self.assertEqual(json.loads((snapshot / "config.json").read_text()), expected)
        self.assertNotEqual(snapshot, effective)

    def test_primary_external_config_link_is_materialized_privately(self) -> None:
        target = self.fixture.root / "external-config.json"
        original = '{"currentContext":"host-context"}'
        target.write_text(original)
        (self.source / "config.json").symlink_to(target)
        _, snapshot = self.launch()
        config = snapshot / "config.json"
        self.assertFalse(config.is_symlink(), "startup writes must never follow a host config symlink")
        config.write_text("{}")
        self.assertEqual(target.read_text(), original)

    def test_internal_links_resolve_only_into_the_private_copy(self) -> None:
        contexts = self.source / "contexts"
        contexts.mkdir()
        (contexts / "saved").write_text("host-context-data")
        (contexts / "relative").symlink_to("saved")
        (contexts / "absolute").symlink_to(contexts / "saved")
        binds, snapshot = self.launch()
        for name in ("relative", "absolute"):
            self.assertEqual((snapshot / "contexts" / name).resolve(), snapshot / "contexts/saved")
        forbidden = {str(contexts), str(contexts / "saved")}
        self.assertFalse(any(bind.split(":")[1:2] and bind.split(":")[1] in forbidden for bind in binds), binds)
        (snapshot / "contexts/absolute").write_text("box-private-update")
        self.assertEqual((contexts / "saved").read_text(), "host-context-data")

    def test_external_links_use_safe_aliases_not_private_runtime_destinations(self) -> None:
        for name, target in (("tmp", Path("/tmp")),
                             ("runtime", Path(f"/run/user/{os.getuid()}")),
                             ("docker-socket", Path("/var/run/docker.sock"))):
            (self.source / name).symlink_to(target)
        binds, snapshot = self.launch()
        destinations = {bind.split(":")[1] for bind in binds if ":" in bind}
        private_tmp = next(bind for bind in binds if bind.split(":")[1:2] == ["/tmp"])
        self.assertNotEqual(private_tmp.split(":")[0], "/tmp")
        self.assertNotIn("/run/docker.sock", destinations)
        self.assertNotIn("/var/run/docker.sock", destinations)
        runtime = [bind for bind in binds if bind.split(":")[1:2] == [f"/run/user/{os.getuid()}"]]
        self.assertEqual(len(runtime), 1)
        self.assertNotEqual(runtime[0].split(":")[0], f"/run/user/{os.getuid()}")
        for name in ("tmp", "runtime", "docker-socket"):
            self.assertTrue(os.readlink(snapshot / name).startswith("/opt/agentbox/docker-external/"))

    def test_helper_discovery_uses_the_selected_config_snapshot(self) -> None:
        directory_tests.write_stub(self.fixture.root / "bin", "jq", 'exec /usr/bin/jq "$@"')
        effective = self.fixture.root / "docker-custom"
        effective.mkdir()
        (effective / "config.json").write_text('{"credsStore":"selected"}')
        self.fixture.env["DOCKER_CONFIG"] = str(effective)
        directory_tests.write_stub(self.fixture.root / "bin", "docker-credential-selected", "exit 0")
        binds, _ = self.launch()
        aliases = Path(next(bind for bind in binds if bind.endswith(":/opt/agentbox/docker-helpers")).split(":")[0])
        self.assertTrue((aliases / "docker-credential-selected").is_file())

    def test_dir_naming_docker_config_adds_no_second_destination(self) -> None:
        args = self.fixture.run_args_of("--dir", str(self.source))
        destinations = [bind.split(":")[1] for bind in self.fixture.binds_of(args) if ":" in bind]
        self.assertEqual(destinations.count(str(self.source)), 1, destinations)


if __name__ == "__main__":
    unittest.main()
