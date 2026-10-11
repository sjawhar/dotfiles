#!/usr/bin/env python3
"""installers/lib.sh ensure_link: link a path to its dotfiles source without overwriting what an app wrote there.

An app that saves by renaming a temp file over its config path (COSMIC's cosmic-config does) turns
the link into a plain file. One identical to the source is relinked; anything else fails the call
and stays as it was.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "installers" / "lib.sh"


class EnsureLink(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.src = root / "dotfiles" / "setting"
        self.src.parent.mkdir()
        self.src.write_text("true")
        self.dst = root / "config" / "setting"
        self.dst.parent.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def ensure_link(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; ensure_link "$2" "$3"', "t", str(LIB), str(self.src), str(self.dst)],
            capture_output=True,
            text=True,
        )

    def assert_linked(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.dst.is_symlink())
        self.assertEqual(self.dst.resolve(), self.src.resolve())

    def test_links_a_missing_path(self) -> None:
        self.assert_linked(self.ensure_link())

    def test_repoints_a_link_to_somewhere_else(self) -> None:
        self.dst.symlink_to(self.dst.parent / "elsewhere")
        self.assert_linked(self.ensure_link())

    def test_relinks_a_plain_file_identical_to_the_source(self) -> None:
        self.dst.write_text("true")
        self.assert_linked(self.ensure_link())

    def test_keeps_a_plain_file_that_differs_and_fails(self) -> None:
        self.dst.write_text("false")
        result = self.ensure_link()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.dst.is_symlink())
        self.assertEqual(self.dst.read_text(), "false")
        self.assertEqual(self.src.read_text(), "true")
        self.assertIn(str(self.dst), result.stderr)
        self.assertIn(str(self.src), result.stderr)

    def test_keeps_a_real_directory_and_links_nothing_inside_it(self) -> None:
        self.dst.mkdir()
        (self.dst / "key").write_text("false")
        result = self.ensure_link()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.dst.is_symlink())
        self.assertEqual(sorted(p.name for p in self.dst.iterdir()), ["key"])
        self.assertIn(str(self.dst), result.stderr)


if __name__ == "__main__":
    unittest.main()
