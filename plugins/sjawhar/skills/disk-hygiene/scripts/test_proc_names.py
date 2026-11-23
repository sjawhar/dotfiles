"""Regression tests: the process readers of disk_hygiene.py and jj_opstore_gc.py read a process
named in bytes that are not UTF-8.

Any process may name itself any bytes (prctl PR_SET_NAME), and the kernel puts them, unchanged,
in /proc/<pid>/comm and in the parentheses of /proc/<pid>/stat. A reader that decoded them as
strict UTF-8 raised UnicodeDecodeError, so one such process anywhere on the host stopped
`writers`, `cpu` and every removal that checks which processes hold a path. Each test here reads a
real child named b'\\xffbadname'.

Run from this directory: python3 -m unittest test_proc_names
"""

from __future__ import annotations

import contextlib
import os
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Generator
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import disk_hygiene  # after SCRIPTS_DIR is on sys.path
import jj_opstore_gc  # after SCRIPTS_DIR is on sys.path

NAME = b"\xffbadname"
READ_AS = "\ufffdbadname"  # NAME decoded with replacement

# The child names itself, says so, and lives until its stdin closes.
_CHILD = """\
import ctypes, sys
if ctypes.CDLL(None, use_errno=True).prctl(15, bytes.fromhex(sys.argv[1]), 0, 0, 0):  # PR_SET_NAME
    raise OSError(ctypes.get_errno(), "prctl(PR_SET_NAME)")
sys.stdout.write("ready\\n")
sys.stdout.flush()
sys.stdin.read()
"""


@contextlib.contextmanager
def named_child(cwd: str | None = None) -> Generator[int]:
    """The pid of a live child named NAME, for the length of the block. Fails the test when the
    kernel does not show the name, or when the child is not ready within 30 s."""
    child = subprocess.Popen([sys.executable, "-c", _CHILD, NAME.hex()], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, cwd=cwd, start_new_session=True)
    try:
        assert child.stdout is not None
        if not select.select([child.stdout], [], [], 30)[0]:
            os.killpg(child.pid, signal.SIGKILL)
            raise AssertionError("the named child was not ready within 30 s")
        if child.stdout.readline() != b"ready\n":
            raise AssertionError("the named child did not start")
        comm = Path(f"/proc/{child.pid}/comm").read_bytes()
        if comm != NAME + b"\n":
            raise AssertionError(f"the named child is named {comm!r}, not {NAME!r}")
        yield child.pid
    finally:
        if child.stdin is not None:
            child.stdin.close()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


class DiskHygieneReadsTheName(unittest.TestCase):
    def test_comm_and_stat_are_read(self) -> None:
        with named_child() as pid:
            self.assertEqual(disk_hygiene._proc_comm(pid), READ_AS)
            fields = disk_hygiene._proc_stat_fields(str(pid))
        self.assertIsNotNone(fields)
        assert fields is not None
        self.assertGreaterEqual(len(fields), 20)

    def test_the_sample_of_every_process_goes_past_it(self) -> None:
        with named_child() as pid:
            sample, _ = disk_hygiene.proc_sample(False)
        self.assertEqual(sample[pid]["comm"], READ_AS)

    def test_a_process_holding_a_path_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as held, named_child(cwd=held) as pid:
            self.assertIn((pid, READ_AS), disk_hygiene.procs_under(held))


class JjProcessesReadTheStat(unittest.TestCase):
    def test_a_pid_reused_by_such_a_process_is_read(self) -> None:
        """pgrep matched a `jj`, and its pid was reused before the read of its stat."""
        with named_child() as pid:
            found = subprocess.CompletedProcess(["pgrep"], 0, stdout=f"{pid}\n", stderr="")
            with mock.patch.object(jj_opstore_gc.subprocess, "run", return_value=found):
                procs = jj_opstore_gc.jj_processes()
        self.assertIn(pid, procs)


if __name__ == "__main__":
    unittest.main()
