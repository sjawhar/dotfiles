#!/usr/bin/env python3
"""A failed mount producer or decoder must abort before any ownership change."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[2] / "agentbox/entrypoint.sh"


class MountpointFailures(unittest.TestCase):
    def run_failure(self, output: str, status: int) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            (home / ".gcloud/host-data").mkdir(parents=True)
            events = root / "chown-events"
            source = ENTRYPOINT.read_text().split("mkdir -p /var/log", 1)[0]
            setup = r'''
getent() { printf 'ubuntu:x:1000:1000::%s:/bin/bash\n' "$HOME"; }
chown() { printf '%s\0' "$*" >>"$EVENTS"; }
findmnt() { printf '%s\n' "$FINDMNT_OUTPUT"; return "$FINDMNT_STATUS"; }
'''
            result = subprocess.run(
                ["bash"], input=setup + source, capture_output=True, text=True, timeout=30,
                env={**os.environ, "HOME": str(home), "EVENTS": str(events),
                     "FINDMNT_OUTPUT": output.replace("@HOME@", str(home)),
                     "FINDMNT_STATUS": str(status), "AGENTBOX_USER": "ubuntu",
                     "AGENTBOX_ENVOY_FORWARDER": "172.31.0.1:9020"},
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(events.exists(), "mount enumeration failure must prevent every chown")

    def test_failed_findmnt_status_is_not_hidden_by_valid_output(self) -> None:
        self.run_failure(json.dumps({"filesystems": [{"target": "@HOME@"},
                                                     {"target": "@HOME@/.gcloud"}]}), 9)

    def test_malformed_mount_json_prevents_chown(self) -> None:
        self.run_failure("not mount JSON", 0)


if __name__ == "__main__":
    unittest.main()
