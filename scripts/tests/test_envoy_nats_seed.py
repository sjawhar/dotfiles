"""envoy-nats-seed's two output modes, the contract an envoy.json `natsSeed` entry relies on.

`--value` prints the seed itself (the client reads the command's stdout as the seed) and exits 1
when there is none, so a missing credential is an error the client names; launcher mode prints the
cache file's path and exits 0 either way. Technique: the real script over a scratch
XDG_RUNTIME_DIR holding a fresh cache (or a fresh miss marker), so no run reaches AWS: a fresh file
starts no background refresh, and a fresh miss marker suppresses the fetch.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "envoy-nats-seed"
SEED = "SU" + "A" * 56


class EnvoyNatsSeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = Path(tempfile.mkdtemp(prefix="envoy-nats-seed-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.runtime)], check=False)
        self.dir = self.runtime / "envoy"
        self.dir.mkdir(mode=0o700)
        self.seed = self.dir / "nats-nkey-seed-omp-session"

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {"PATH": os.environ["PATH"], "HOME": str(self.runtime), "XDG_RUNTIME_DIR": str(self.runtime)}
        return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)

    def test_value_prints_the_cached_seed_and_launcher_mode_its_path(self) -> None:
        self.seed.write_text(SEED)
        self.seed.chmod(0o600)
        value = self.run_script("--value")
        self.assertEqual((value.returncode, value.stdout), (0, SEED))
        launcher = self.run_script()
        self.assertEqual((launcher.returncode, launcher.stdout.strip()), (0, str(self.seed)))

    def test_value_exits_1_naming_the_miss_while_launcher_mode_still_exits_0(self) -> None:
        (self.dir / "nats-nkey-seed-omp-session.miss").write_text("the aws CLI is not installed\n")
        value = self.run_script("--value")
        self.assertEqual((value.returncode, value.stdout), (1, ""))
        self.assertIn("the aws CLI is not installed", value.stderr)
        launcher = self.run_script()
        self.assertEqual((launcher.returncode, launcher.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
