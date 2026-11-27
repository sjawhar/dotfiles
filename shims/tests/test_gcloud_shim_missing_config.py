#!/usr/bin/env python3
"""The gcloud shim reads a configuration's file quietly when the file is not there.

An agent's call under an empty CLOUDSDK_CONFIG (scripts/gcloud-reader runs gcloud in a fresh
one) has no configurations/config_default. The shim's readers of a
configuration's `account` and `impersonate_service_account` treat a missing file as no value, and
used to print bash's "No such file or directory" for it: the `2> /dev/null` came after the `<` of
the loop, so the shell reported the failed redirect before stderr went to /dev/null.
"""

from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stdin_cases import run_without_tty  # noqa: E402 - found through the path above


class MissingConfigurationFile(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        real = self.bin / "gcloud"
        real.write_text('#!/bin/bash\necho "REAL-GCLOUD-RAN $*"\n', encoding="utf-8")
        real.chmod(real.stat().st_mode | stat.S_IEXEC)
        self.config = root / "empty-config"
        self.config.mkdir()
        self.env = {
            # The shim is run by its path, so it takes the stub past PATH as the real gcloud.
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(root / "home"),
            "CLOUDSDK_CONFIG": str(self.config),
            # A closed port: scripts/on-google-vm says no, so no metadata server is asked.
            "GCE_METADATA_HOST": "127.0.0.1:9",
            "OMP_SESSION_ID": "01a00000-0000-7000-8000-000000000000",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_an_agent_call_under_an_empty_config_dir_prints_no_missing_file(self) -> None:
        for args in (["auth", "print-access-token"], ["storage", "buckets", "list"],
                     ["organizations", "get-iam-policy", "123456789012"]):
            with self.subTest(args=args):
                result = run_without_tty([str(SHIMS / "gcloud"), *args], env=self.env, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("REAL-GCLOUD-RAN", result.stdout)
                self.assertNotIn("No such file or directory", result.stderr)
                self.assertNotIn("config_default", result.stderr)


if __name__ == "__main__":
    unittest.main()
