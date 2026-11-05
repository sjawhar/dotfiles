#!/usr/bin/env python3
"""installers/lib.sh ensure_box_health_owner, called the way every box watch's installer calls it:
under `set -euo pipefail`, from a script with no `name` variable of its own.

Until 2026-11-02 its first line read `local name="$1" dropin=".../$name.service.d/..."`, and bash
expands every word of one `local` before it assigns any, so the call stopped with
`name: unbound variable` in disk-fill-watch.sh, load-swap-watch.sh and devbox/install.sh.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "installers" / "lib.sh"
OWNER = "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"


def call(home: Path, owner: str | None) -> subprocess.CompletedProcess:
    script = f'set -euo pipefail\nsource "{LIB}"\nensure_box_health_owner demo-watch\n'
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    if owner is not None:
        env["BOX_HEALTH_OWNER"] = owner
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, check=False)


class EnsureBoxHealthOwner(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.dropin = self.home / ".config/systemd/user/demo-watch.service.d/box-health-owner.conf"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_unset_owner_passes_under_set_u_and_writes_nothing(self) -> None:
        result = call(self.home, None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.dropin.exists())

    def test_owner_writes_the_drop_in_beside_the_named_unit(self) -> None:
        result = call(self.home, OWNER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.dropin.read_text(), f"[Service]\nEnvironment=BOX_HEALTH_OWNER={OWNER}\n")
        self.assertIn(f"demo-watch: alerts go to session {OWNER}", result.stdout)

    def test_unusable_owner_is_refused(self) -> None:
        result = call(self.home, "not-a-session")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.dropin.exists())


if __name__ == "__main__":
    unittest.main()
