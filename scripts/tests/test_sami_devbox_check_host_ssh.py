#!/usr/bin/env python3
"""SSH acceptance recognizes containing writable mounts, not similarly named paths."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

CHECK = Path(__file__).resolve().parents[2] / "devbox/sami-devbox-check"


class HostSshMountCoverage(unittest.TestCase):
    def test_manifest_coverage_controls_the_real_ssh_probe(self) -> None:
        source = CHECK.read_text()
        body = source.split("host_sshd_check() {", 1)[1].split("\n}\n", 1)[0]
        function = "host_sshd_check() {" + body + "\n}\n"
        legacy = "\n".join(f"ro {path}" for path in (
            ".ssh/sami-devbox", ".ssh/sami-devbox.pub", ".ssh/known_hosts.sami-devbox"))
        for manifest, expected in (("rw .ssh\n", True), (legacy, True),
                                   ("rw .ssh-other\n", False), ("ro .ssh\n", False)):
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "agentbox").mkdir()
                (root / "agentbox/mounts").write_text(manifest)
                binary = root / "bin"
                binary.mkdir()
                ssh = binary / "ssh"
                ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >"$PROBE_LOG"\n')
                ssh.chmod(0o755)
                log = root / "ssh-probe"
                script = 'ok() { echo "ok $*"; }; fail() { echo "FAIL $*"; }\n' + function + "host_sshd_check\n"
                result = subprocess.run(
                    ["bash"], input=script, capture_output=True, text=True, timeout=30,
                    env={**os.environ, "DOTFILES_DIR": str(root), "PATH": f"{binary}:/usr/bin:/bin",
                         "PROBE_LOG": str(log)},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(log.exists(), expected, result.stdout)
                self.assertEqual("WAIT" in result.stdout, not expected, result.stdout)
                if expected:
                    self.assertIn("sami-devbox true", log.read_text())
                    self.assertIn("ok ssh box", result.stdout)


if __name__ == "__main__":
    unittest.main()
