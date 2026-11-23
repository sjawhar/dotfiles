#!/usr/bin/env python3
"""scripts/tmux-snapshot reads the processes under each pane; any argument of any of them may be bytes that are
not UTF-8 (a Latin-1 file name), and the snapshot reads past it."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from non_utf8_name import process_named  # noqa: E402 - found through the path above

SCRIPT = Path(__file__).resolve().parents[1] / "tmux-snapshot"
LOADER = importlib.machinery.SourceFileLoader("tmux_snapshot", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
SNAPSHOT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SNAPSHOT  # its dataclasses look their module up there
LOADER.exec_module(SNAPSHOT)


class LauncherSpec(unittest.TestCase):
    def test_a_command_line_that_is_not_utf8_is_read(self) -> None:
        # The child's own command line ends in the argument it passes on: b"arg\xff".
        with process_named("true", "arg\udcff") as named:
            self.assertEqual(named.code, 0, named.output)
            self.assertIn(b"arg\xff", (Path("/proc") / str(named.pid) / "cmdline").read_bytes())
            self.assertIsNone(SNAPSHOT.launcher_spec(named.pid))


if __name__ == "__main__":
    unittest.main()
