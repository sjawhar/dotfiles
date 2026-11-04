#!/usr/bin/env python3
"""A credential helper keeps the invocation name that selects its provider."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

import test_agentbox_dirs as directory_tests


class HelperInvocation(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = directory_tests.AgentboxDirs(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        directory_tests.write_stub(self.fixture.root / "bin", "jq", 'exec /usr/bin/jq "$@"')
        (self.fixture.home / ".docker/config.json").write_text('{"credsStore":"named"}')

    def exercise(self, directory: Path, *, mounted_mise: bool) -> None:
        target_directory = self.fixture.home / ".mise/bin" if mounted_mise else directory
        directory_tests.write_stub(
            target_directory, "mise",
            '[ "${0##*/}" = docker-credential-named ] || exit 41\n'
            '[ "$1" = get ] || exit 42\n'
            'IFS= read -r registry || [ -n "$registry" ]\n'
            'printf \'{"Username":"fixture","Secret":"fixture-secret","ServerURL":"%s"}\\n\' "$registry"',
        )
        target = target_directory / "mise"
        directory.mkdir(parents=True, exist_ok=True)
        invocation = directory / "docker-credential-named"
        invocation.symlink_to(target)
        self.fixture.env["PATH"] = f"{directory}:{self.fixture.env['PATH']}"
        args = self.fixture.run_args_of("--dir", str(self.fixture.lane))
        binds = self.fixture.binds_of(args)
        helper_bind = next(bind for bind in binds if bind.endswith(":/opt/agentbox/docker-helpers"))
        aliases = Path(helper_bind.split(":")[0])
        if mounted_mise:
            self.assertFalse((aliases / "docker-credential-named").exists(),
                             "an already visible mise shim needs no wrapper")
            command, path = ["docker-credential-named", "get"], f"{aliases}:{directory}:/usr/bin:/bin"
        else:
            boxed = "/opt/agentbox/docker-helper-bin/0/docker-credential-named"
            self.assertIn(f"{target}:{boxed}", binds,
                          "the helper payload is bound under its selecting name inside the box")
            self.assertIn(boxed, (aliases / "docker-credential-named").read_text())
            # Outside a box, run the bound payload under the same basename the box gives it.
            stand_in = self.fixture.root / "boxed-helper"
            stand_in.mkdir()
            (stand_in / "docker-credential-named").symlink_to(target)
            command, path = [str(stand_in / "docker-credential-named"), "get"], "/usr/bin:/bin"
        result = subprocess.run(command, input="registry.example", capture_output=True,
                                text=True, timeout=30, env={**os.environ, "PATH": path})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["ServerURL"], "registry.example")

    def test_external_name_selecting_symlink_keeps_its_name(self) -> None:
        self.exercise(self.fixture.root / "external-bin", mounted_mise=False)

    def test_mounted_mise_shim_is_not_replaced(self) -> None:
        self.exercise(self.fixture.home / ".mise/shims", mounted_mise=True)


if __name__ == "__main__":
    unittest.main()
