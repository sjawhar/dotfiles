#!/usr/bin/env python3
"""scripts/pr-snapshot: run ce-babysit-pr's vendored pr-snapshot by name, or say what is missing.

The wrapper is copied into a stand-in checkout; the vendored script there is a stub that prints its
arguments and stdin and exits 7, so the test shows the wrapper passes all three through.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "pr-snapshot"
VENDORED = Path("vendor/compound-engineering/skills/ce-babysit-pr/scripts/pr-snapshot")


def checkout(tmp: str) -> Path:
    """A stand-in checkout holding only scripts/pr-snapshot."""
    root = Path(tmp) / "dotfiles"
    (root / "scripts").mkdir(parents=True)
    _ = shutil.copy(WRAPPER, root / "scripts" / "pr-snapshot")
    return root


def run_by_name(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{root / 'scripts'}:{os.environ['PATH']}"}
    return subprocess.run(["pr-snapshot", *args], input="from stdin\n", capture_output=True, text=True, env=env, timeout=30)


class PrSnapshot(unittest.TestCase):
    def test_runs_the_vendored_script_with_its_arguments_stdin_and_exit_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = checkout(tmp)
            stub = root / VENDORED
            stub.parent.mkdir(parents=True)
            _ = stub.write_text('#!/bin/bash\nprintf "<%s>" "$@"; echo; cat; exit 7\n')
            stub.chmod(0o755)
            result = run_by_name(root, "watch", "--pr", "12", "a b")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, "<watch><--pr><12><a b>\nfrom stdin\n")
        self.assertEqual(result.stderr, "")

    def test_without_the_vendored_clone_it_names_the_missing_script_and_the_installer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = checkout(tmp)
            result = run_by_name(root, "watch", "--pr", "12")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(root / VENDORED), result.stderr)
        self.assertIn(f"bash {root}/installers/omp.sh", result.stderr)


if __name__ == "__main__":
    unittest.main()
