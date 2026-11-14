#!/usr/bin/env python3
"""A box's /run/user/<uid> is 0700, so secretsd will start inside it.

`mkdir -p` takes the launcher's umask, which is 002 in these sessions, so the per-box runtime
directory came out 0775 and only its `secretsd` and `agent-secrets` children were chmodded. A
secretsd started inside a box then refused to run at all ("writable by other users"). Measured
at 22:13Z on 2026-11-04: all 20 live boxes on sami-devbox had /run/user/1000 at 775.

Technique, as test_agentbox_dirs.py: source `scripts/agentbox` (its source guard runs nothing)
and call `mount_args` with the runtime base in a temporary tree, under the umask that caused it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"


class RunUserMode(unittest.TestCase):
    def test_the_per_box_runtime_dir_and_its_key_dirs_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            run_base = root / "run"
            # mount_args stops at the first host thing a test machine need not have (the
            # secretsd socket); the runtime dir is made before that, which is what is asserted.
            script = ('umask 002\nsource "%s"\nRUN_BASE="%s"\ndie() { exit 0; }\n'
                      'mount_args agentbox-modetest >/dev/null 2>&1 || true\n' % (AGENTBOX, run_base))
            done = subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True, timeout=120, check=False,
                env={**os.environ, "HOME": str(root), "DOTFILES_DIR": str(DOTFILES),
                     "AGENTBOX_BOXES": str(root / "boxes"), "AGENTBOX_RUN_BASE": str(run_base),
                     "AGENTBOX_DISKS": str(root / "disks"),
                     "AGENTBOX_CACHE_ROOT": str(root / "box-cache")})
            self.assertEqual(done.returncode, 0, done.stderr)
            run_user = run_base / "agentbox-modetest/run-user"
            self.assertTrue(run_user.is_dir(), f"mount_args made no runtime dir: {done.stderr}")
            # The box's key dir is beside run-user/ and bound on its own (box_keydir); the
            # run-user/agent-secrets mount point stays private too.
            keydir = run_base / "agentbox-modetest/agent-secrets"
            for path in (run_user, run_user / "secretsd", run_user / "agent-secrets", keydir):
                self.assertEqual(oct(path.stat().st_mode & 0o777), "0o700", path)


if __name__ == "__main__":
    unittest.main()
