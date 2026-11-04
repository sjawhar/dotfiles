#!/usr/bin/env python3
"""A host bind is excluded from ownership changes even when its name is a find pattern."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[2] / "agentbox/entrypoint.sh"


class LiteralMountpointNames(unittest.TestCase):
    def test_host_mount_names_are_literal(self) -> None:
        for name in ("[host]*?", "host cache", r"host\cache", "host\ncache"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                home = root / "home"
                shared = home / ".config" / name
                (shared / "child").mkdir(parents=True)
                own = home / "private"
                own.mkdir()
                events = root / "events"
                prefix = ENTRYPOINT.read_text().split("mkdir -p /var/log", 1)[0]
                raw = str(shared).replace("\\", r"\x5c").replace(" ", r"\x20").replace("\n", r"\x0a")
                targets = json.dumps({"filesystems": [{"target": str(home)}, {"target": str(shared)}]})
                setup = r'''
getent() { printf 'ubuntu:x:1000:1000::%s:/bin/bash\n' "$HOME"; }
chown() { printf '%s\0' "$*" >>"$EVENTS"; }
findmnt() {
    case " $* " in
        *" --json "*|*" -J "*) printf '%s\n' "$FINDMNT_JSON" ;;
        *) printf '%s\n' "$HOME" "$MOUNT_RAW" ;;
    esac
}
'''
                result = subprocess.run(
                    ["bash"], input=setup + prefix, capture_output=True, text=True, timeout=30,
                    env={**os.environ, "HOME": str(home), "EVENTS": str(events), "MOUNT_RAW": raw,
                         "FINDMNT_JSON": targets, "AGENTBOX_USER": "ubuntu",
                         "AGENTBOX_ENVOY_FORWARDER": "172.31.0.1:9020"},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                changed = [event.decode() for event in events.read_bytes().split(b"\0") if event]
                self.assertIn(f"ubuntu:ubuntu {own}", changed)
                self.assertFalse(any(str(shared) in event for event in changed), changed)


if __name__ == "__main__":
    unittest.main()
