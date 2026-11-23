"""A child process whose name is not UTF-8, for the tests of each reader of other processes' names.

Any process may set any 15 bytes as its own name with prctl(PR_SET_NAME), and the kernel puts
those bytes, unchanged, in /proc/<pid>/comm, in the parentheses of /proc/<pid>/stat and on the
`Name:` line of /proc/<pid>/status. A reader that decodes one of them as strict UTF-8 raises
UnicodeDecodeError, or, where it catches ValueError, returns its fallback for that process.
test_oryx_tunnel_key_cleanup.py keeps such a process alive for its whole test class, so a reader
that walks every process on the host meets one whenever pytest-xdist runs the two at once.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import NamedTuple

NAME = b"\xffbadname"
# NAME as a reader that decodes with replacement reads it.
READ_AS = "\ufffdbadname"

# The child names itself, runs the command it was given (if any) as its own child, says so, and
# lives until its stdin closes.
_CHILD = """\
import ctypes, subprocess, sys
PR_SET_NAME = 15
if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_NAME, bytes.fromhex(sys.argv[1]), 0, 0, 0):
    raise OSError(ctypes.get_errno(), "prctl(PR_SET_NAME)")
code = subprocess.run(sys.argv[2:], stdout=sys.stderr).returncode if sys.argv[2:] else 0
sys.stdout.write(f"ready {code}\\n")
sys.stdout.flush()
sys.stdin.read()
"""


class Named(NamedTuple):
    pid: int
    code: int  # the exit code of the command the child ran, 0 when it ran none
    output: str  # what that command printed, both streams


@contextlib.contextmanager
def process_named(*command: str, name: bytes = NAME, env: dict[str, str] | None = None) -> Generator[Named]:
    """A live child of this process named `name` for the length of the block.

    With `command`, the child first runs it as its own child, so that command's parent process
    carries the name. Raises AssertionError when the kernel does not show the name, so a test
    never passes on a process that is not named as it says.
    """
    with tempfile.TemporaryFile() as output:
        child = subprocess.Popen(
            [sys.executable, "-c", _CHILD, name.hex(), *command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=output,
            env=env,
        )
        try:
            assert child.stdout is not None
            ready = child.stdout.readline().decode()
            output.seek(0)
            printed = output.read().decode("utf8", "replace")
            if not ready.startswith("ready "):
                raise AssertionError(f"the named child did not start: {printed}")
            comm = (Path("/proc") / str(child.pid) / "comm").read_bytes()
            if comm != name + b"\n":
                raise AssertionError(f"the named child is named {comm!r}, not {name!r}")
            yield Named(child.pid, int(ready.split()[1]), printed)
        finally:
            if child.stdin is not None:
                child.stdin.close()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
