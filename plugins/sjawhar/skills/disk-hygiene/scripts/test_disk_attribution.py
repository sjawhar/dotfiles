"""Regression tests for disk_attribution.py.

Run from this directory: python3 -m unittest test_disk_attribution
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import disk_attribution  # noqa: E402 -- needs SCRIPTS_DIR on sys.path first


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {"timeout": 5.0, "jobs": 4, "max_io_pressure": 100.0, "min_size": "1"}
    values.update(overrides)
    return argparse.Namespace(**values)


class EmptyBatchTest(unittest.TestCase):
    """A directory that splits into no subdirectories must not start a du with no paths.

    GNU du given no path arguments sizes the current directory and prints `.`, so an
    empty batch used to report a size for `.`, and `run` then raised KeyError('.')
    (measured on sami-agents, 2026-10-01, mid-run on a 4.8 TB host).
    """

    def test_chunks_of_nothing_is_no_batches(self) -> None:
        self.assertEqual(disk_attribution.chunks([], 8), [])

    def test_du_batch_refuses_an_empty_batch(self) -> None:
        with self.assertRaises(ValueError):
            disk_attribution.du_batch([], 5.0)

    def test_a_files_only_directory_that_must_split_finishes_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "root")
            only_files = root / "onlyfiles"
            only_files.mkdir(parents=True)
            (only_files / "a").write_bytes(b"x" * 4096)
            (only_files / "b").write_bytes(b"x" * 8192)
            batches: list[list[str]] = []

            def fake_du_batch(paths: list[str], _timeout: float) -> tuple[dict[str, int], list[str]]:
                batches.append(list(paths))
                if not paths:
                    # What real du does with no path arguments (reproduced 2026-10-01).
                    return {".": 123}, []
                # Never finish in time, so every directory is split down to its files.
                return {}, list(paths)

            run = disk_attribution.Attribution(_args(), disk_attribution.Output(None))
            node = disk_attribution.Node(os.path.abspath(root))
            with mock.patch.object(disk_attribution, "du_batch", fake_du_batch):
                run.run([node])

            self.assertNotIn([], batches, "an empty batch reached du")
            self.assertNotIn(".", run.nodes)
            self.assertEqual(run.nodes[str(only_files)].files, (only_files / "a").stat().st_blocks * 512 + (only_files / "b").stat().st_blocks * 512)


if __name__ == "__main__":
    unittest.main()
