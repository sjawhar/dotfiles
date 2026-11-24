"""leak_terms_fixture runs age-keygen, sops and mise from `/`, never from the test's working directory.

With mise's paranoid mode on, a mise shim run where an untrusted mise.toml applies refuses with "not trusted": run
from a /mnt/scratch export of this checkout, with mise's shims on PATH, every leak-terms test errored with "age-keygen
could not create the test key". The test puts mise's shims first on PATH, runs where that refusal can happen, and
shows it does, before it trusts a pass.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import leak_terms_fixture


def mise_shims() -> Path | None:
    """mise's shims directory, whose age-keygen is a link to mise; None when this machine has none."""
    candidates = [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    candidates.append(Path(os.environ.get("MISE_DATA_DIR", Path.home() / ".mise")) / "shims")
    for directory in candidates:
        shim = directory / "age-keygen"
        if shim.is_symlink() and Path(os.path.realpath(shim)).name == "mise":
            return directory
    return None


class ToolsRunFromRoot(unittest.TestCase):
    def test_the_test_key_is_made_from_a_directory_mise_does_not_trust(self) -> None:
        shims = mise_shims()
        if shims is None:
            self.skipTest("no mise shim for age-keygen here, so no mise config can refuse it")
        path = f"{shims}{os.pathsep}{os.environ.get('PATH', '')}"
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "mise.toml").write_text("[tools]\n", encoding="utf-8")
            # Control: mise's shim refuses there, so a pass below is the fixture's doing.
            refused = subprocess.run(["age-keygen", "--version"], cwd=directory, capture_output=True, text=True,
                                     check=False, env={**os.environ, "PATH": path})
            if refused.returncode == 0:
                self.skipTest("mise runs its shim under an untrusted mise.toml here (paranoid is off)")
            self.assertIn("not trusted", refused.stderr)
            previous = os.getcwd()
            os.chdir(directory)
            self.addCleanup(os.chdir, previous)
            key = Path(directory) / "key.txt"
            with mock.patch.dict(os.environ, {"PATH": path}):
                leak_terms_fixture._run_age_keygen(key)
            self.assertIn("AGE-SECRET-KEY-", key.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
