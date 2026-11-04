#!/usr/bin/env python3
"""New boxes register under the physical host without overriding profile connections."""

from __future__ import annotations

import unittest

import test_agentbox_envoy as envoy_tests


class AgentboxIdentity(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = envoy_tests.AgentboxEnvoy(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_explicit_host_identity_is_short_and_connection_settings_stay_profile_owned(self) -> None:
        for host in ("sami-devbox", "sami-rocqperf.tail12ec1c.ts.net"):
            with self.subTest(host):
                self.fixture.env["AGENTBOX_HOST"] = host
                self.fixture.env["ENVOY_MACHINE_ID"] = "agentbox-stale-id"
                values = self.fixture.box_env("omp")
                self.assertIn(f"ENVOY_MACHINE_ID={host.split('.', 1)[0]}", values)
                names = {value.split("=", 1)[0] for value in values}
                self.assertFalse(names & set(envoy_tests.ENVOY_VARS), values)

    def test_identity_defaults_to_the_launching_hostname(self) -> None:
        envoy_tests.write_stub(self.fixture.root / "bin", "hostname", "echo sami-devbox.tail12ec1c.ts.net")
        self.assertIn("ENVOY_MACHINE_ID=sami-devbox", self.fixture.box_env("omp"))


if __name__ == "__main__":
    unittest.main()
