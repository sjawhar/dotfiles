#!/usr/bin/env python3
"""scripts/tests/non_utf8_name.py: the named child the process-reader tests use, and its bound on a hung start."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from non_utf8_name import NAME, process_named  # noqa: E402 - found through the path above


class ProcessNamed(unittest.TestCase):
    def test_the_child_carries_the_name(self) -> None:
        with process_named() as named:
            self.assertEqual((Path("/proc") / str(named.pid) / "comm").read_bytes(), NAME + b"\n")

    def test_a_child_that_never_becomes_ready_fails_the_test_within_its_bound(self) -> None:
        started = time.monotonic()
        with self.assertRaisesRegex(AssertionError, "not ready within 1 s"):
            with process_named("sleep", "30", ready_within=1.0):
                pass
        self.assertLess(time.monotonic() - started, 10)


if __name__ == "__main__":
    unittest.main()
