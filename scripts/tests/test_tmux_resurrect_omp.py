#!/usr/bin/env python3
"""scripts/tmux-resurrect-omp rewrites a saved pane into the command that resumes its session.

Two things it must never do: read a session id out of text that is not an argument of the process
(a prompt holding `git ls-tree -r f1006fe8` named a session that way), and bring back a session
that moved to another machine.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "tmux-resurrect-omp"
spec = importlib.util.spec_from_loader("tmux_resurrect_omp",
                                       importlib.machinery.SourceFileLoader("tmux_resurrect_omp", str(MODULE)))
resurrect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resurrect)


class NamedSession(unittest.TestCase):
    SID = "01a19700-0000-7000-8000-0000000e0001"
    OTHER = "01a19700-0000-7000-8000-0000000e0002"

    def test_only_a_whole_argument_names_a_session(self) -> None:
        self.assertEqual(resurrect.named_session(f"omp --resume {self.SID}"), self.SID)
        self.assertEqual(resurrect.named_session(f"omp --restore={self.SID}"), self.SID)
        self.assertEqual(resurrect.named_session(f"omp -r {self.SID}"), self.SID)
        self.assertIsNone(resurrect.named_session("omp -p 'check git ls-tree -r f1006fe8 output'"),
                          "a prompt that holds -r <hex> names no session")
        self.assertIsNone(resurrect.named_session(f"omp -p 'the work of session {self.SID}'"))

    def test_the_id_comes_from_the_last_path_component(self) -> None:
        nested = (f"/home/sami/.omp/agent/sessions/-w/2026-10-14T17-14-49-559Z_{self.OTHER}/"
                  f"2026-10-25T02-17-36-102Z_{self.SID}.jsonl")
        self.assertEqual(resurrect.named_session(f"omp --resume {nested}"), self.SID,
                         "a subagent's transcript sits under its parent's directory")


class MovedAway(unittest.TestCase):
    SID = "01a19700-0000-7000-8000-0000000e0001"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.index = Path(self.temp.name) / "agentbox-moved"
        self.index.mkdir()
        self.old_index = resurrect.MOVED_INDEX
        resurrect.MOVED_INDEX = self.index
        self.addCleanup(setattr, resurrect, "MOVED_INDEX", self.old_index)
        os.environ["AGENTBOX_HOST"] = "oldbox"
        self.addCleanup(os.environ.pop, "AGENTBOX_HOST", None)

    def test_a_session_that_left_this_machine_is_not_resumed_here(self) -> None:
        (self.index / self.SID).write_text("from=oldbox\nto=devbox\ntime=2026-11-03T23:50:00Z\n", encoding="utf-8")
        self.assertTrue(resurrect.moved_away(self.SID))

    def test_a_marker_of_another_machine_or_none_holds_nothing(self) -> None:
        self.assertFalse(resurrect.moved_away(self.SID), "no marker, nothing to hold")
        (self.index / self.SID).write_text("from=devbox\nto=oldbox\n", encoding="utf-8")
        self.assertFalse(resurrect.moved_away(self.SID),
                         "a marker the move copied to the target is inert there")


if __name__ == "__main__":
    unittest.main()
