#!/usr/bin/env python3
"""Only the AgentBox installer's three unapproved authorized-key options are removed."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[2] / "installers/agentbox-host-ssh.sh"


class AgentboxHostSshAccess(unittest.TestCase):
    def test_existing_only_mode_preserves_keys_other_lines_and_file_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ssh = root / ".ssh"
            ssh.mkdir()
            public = "ssh-ed25519 AAAAFIXTURE agentbox fixture"
            (ssh / "fixture-host.pub").write_text(public + "\n")
            key = ssh / "fixture-host"
            key.write_text("private fixture, never regenerated")
            auth = ssh / "authorized_keys"
            other = 'command="independent-control" ssh-ed25519 AAAAOTHER other\n'
            auth.write_text(other + 'from="172.23.0.0/16",no-agent-forwarding,no-X11-forwarding '
                            + public + "\n")
            auth.chmod(0o640)
            before = auth.stat()
            binary = root / "bin"
            binary.mkdir()
            for name, body in (("hostname", "echo fixture-host"),
                               ("ssh-keygen", "echo unexpected-keygen >&2; exit 91"),
                               ("tailscale", "echo unexpected-network >&2; exit 92"),
                               ("docker", "echo unexpected-docker >&2; exit 93")):
                path = binary / name
                path.write_text(f"#!/bin/sh\n{body}\n")
                path.chmod(0o755)
            result = subprocess.run(
                [str(INSTALLER), "--unrestrict-existing"], capture_output=True, text=True, timeout=30,
                env={**os.environ, "HOME": str(root), "PATH": f"{binary}:/usr/bin:/bin"},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(auth.read_text(), other + public + "\n")
            after = auth.stat()
            self.assertEqual((stat.S_IMODE(after.st_mode), after.st_uid, after.st_gid),
                             (stat.S_IMODE(before.st_mode), before.st_uid, before.st_gid))
            self.assertEqual(key.read_text(), "private fixture, never regenerated")
            self.assertEqual((ssh / "fixture-host.pub").read_text(), public + "\n")
            self.assertNotIn("unexpected-", result.stderr)
            self.assertNotIn("AAAAFIXTURE", result.stdout + result.stderr)

    def test_cross_host_key_is_selected_without_a_local_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ssh = root / ".ssh"
            ssh.mkdir()
            payload = b"cross-host fixture"
            public = "ssh-ed25519 " + base64.b64encode(payload).decode() + " fixture"
            fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(payload).digest()).decode().rstrip("=")
            other = "# unrelated entry stays unchanged\n"
            auth = ssh / "authorized_keys"
            auth.write_text(other + 'from="100.112.105.63" ' + public + "\n")
            before = auth.stat()
            result = subprocess.run(
                [str(INSTALLER), "--unrestrict-existing", "--key-fingerprint", fingerprint],
                capture_output=True, text=True, timeout=30, env={**os.environ, "HOME": str(root)},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(auth.read_text(), other + public + "\n")
            after = auth.stat()
            self.assertEqual((after.st_mode, after.st_uid, after.st_gid),
                             (before.st_mode, before.st_uid, before.st_gid))
            self.assertEqual(sorted(path.name for path in ssh.iterdir()), ["authorized_keys"])
            self.assertNotIn(base64.b64encode(payload).decode(), result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
