#!/usr/bin/env python3
"""scripts/tmux-resurrect-omp rewrites a saved pane into the command that resumes its session.

Two things it must never do: read a session id out of text that is not an argument of the process
(a prompt holding `git ls-tree -r f1006fe8` named a session that way), and bring back a session
that moved to another machine.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from subprocess import Popen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from non_utf8_name import READ_AS, process_named  # noqa: E402 - found through the path above

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


class ProfileSurvivesRestore(unittest.TestCase):
    """A named profile's pane must come back in that profile, in the form the restore list matches.

    Before 2026-11-25 a theorem pane was saved as `omp --resume <id>`, which reopens the session in
    the default profile as a fork of a stale copy; and a pane whose omp runs under the shim (memory
    ceiling) is saved with `bash` as its command, which the hook skipped entirely.
    """

    SID = "01a19700-0000-7000-8000-0000000e0003"
    RESTORE_LIST = "omp --resume"  # @resurrect-processes "~omp --resume" in .tmux.conf
    temp: tempfile.TemporaryDirectory[str]
    root: Path
    fake_omp: Path

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        moved = self.root / "agentbox-moved"
        moved.mkdir()
        self.addCleanup(setattr, resurrect, "MOVED_INDEX", resurrect.MOVED_INDEX)
        setattr(resurrect, "MOVED_INDEX", moved)
        self.fake_omp = self.root / "omp"  # comm "omp", like the real binary
        _ = self.fake_omp.write_bytes(Path("/bin/sleep").read_bytes())
        self.fake_omp.chmod(0o755)

    def pane_running_omp(self, session_dir: str) -> int:
        """A shell (the pane) whose child, named omp, holds a session file open, as omp's writer does."""
        session = self.root / session_dir / f"2026-11-25T15-00-00-000Z_{self.SID}.jsonl"
        session.parent.mkdir(parents=True)
        session.touch()
        shell = Popen(["bash", "-c", f'exec 3<"{session}"; "{self.fake_omp}" 30 & wait'])
        self.addCleanup(shell.wait)
        self.addCleanup(lambda: Popen(["pkill", "-P", str(shell.pid)]).wait())
        for _ in range(100):
            if resurrect.find_omp(shell.pid) is not None:
                return shell.pid
            time.sleep(0.02)
        self.fail("the stand-in omp never started")

    def restored_command(self, pane_pid: int, command: str, full_command: str) -> str:
        save = self.root / "save.txt"
        save.write_text("\t".join(["pane", "dev", "2", "1", ":*", "1", "title", ":/home/u/src", "1",
                                   command, ":" + full_command]) + "\n")
        self.addCleanup(setattr, resurrect, "panes", resurrect.panes)
        setattr(resurrect, "panes", lambda: {("dev", "2", "1"): (pane_pid, "%9")})
        resurrect.rewrite(save)
        return save.read_text().split("\n")[0].split("\t", 10)[10][1:]

    def test_a_named_profile_session_under_the_shim_resumes_in_its_profile(self) -> None:
        pid = self.pane_running_omp(".omp/profiles/theorem/agent/sessions/-src")
        restored = self.restored_command(pid, "bash", "bash /home/u/.dotfiles/shims/omp --profile theorem")
        self.assertEqual(restored, f"omp --resume {self.SID} --profile theorem")
        self.assertIn(self.RESTORE_LIST, restored)

    def test_a_default_profile_session_names_no_profile(self) -> None:
        pid = self.pane_running_omp(".omp/agent/sessions/-src")
        self.assertEqual(self.restored_command(pid, "omp", "omp"), f"omp --resume {self.SID}")

    def test_an_idle_session_takes_its_profile_from_the_command_line(self) -> None:
        shell = Popen(["sleep", "30"])  # a pane with no omp under it: argv is all there is
        self.addCleanup(shell.wait)
        self.addCleanup(shell.kill)
        restored = self.restored_command(
            shell.pid, "bash", f"bash /home/u/.dotfiles/shims/omp --profile=theorem --resume {self.SID}")
        self.assertEqual(restored, f"omp --resume {self.SID} --profile theorem")

    def test_a_pane_that_runs_no_omp_is_left_as_saved(self) -> None:
        shell = Popen(["sleep", "30"])
        self.addCleanup(shell.wait)
        self.addCleanup(shell.kill)
        self.assertEqual(self.restored_command(shell.pid, "bash", "nvim notes.md"), "nvim notes.md")


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


class NameNotUtf8(unittest.TestCase):
    def test_a_process_named_in_bytes_that_are_not_utf8_is_read(self) -> None:
        """A pane's process may name itself any bytes (prctl PR_SET_NAME); the omp search reads past it."""
        with process_named() as named:
            self.assertEqual(resurrect.comm(named.pid), READ_AS)


if __name__ == "__main__":
    unittest.main()
