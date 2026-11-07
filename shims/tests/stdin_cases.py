#!/usr/bin/env python3
"""Running a shim with, and without, a terminal, and in a person's SSH session (INFRA-1538).

The identity shims take a command that arrives in an SSH session sshd gave no terminal for an
agent's. The session, not this command's stdin, decides: sshd sets `SSH_TTY` for a session it
gave a terminal, and every command of that session inherits it, piped stdin included. So a case
that means a person's interactive SSH session sets `SSH_TTY`, and a case must never be decided by
whether the test runner itself has a terminal: every case names its own stdin too, /dev/null or
the slave side of a pty.
"""

from __future__ import annotations

import os
import subprocess

# A plausible `SSH_CONNECTION`: client address and port, server address and port.
SSH_CONNECTION = "10.0.0.1 54321 10.0.0.2 22"
# A plausible `SSH_TTY`: the terminal sshd gave the person's session.
SSH_TTY = "/dev/pts/7"
# The environment of a person's own interactive SSH session, whatever that session's command
# reads on stdin.
SSH_PERSON = {"SSH_CONNECTION": SSH_CONNECTION, "SSH_TTY": SSH_TTY}


def run_without_tty(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    """`ssh host <argv>`: a command with no terminal on stdin."""
    return subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, **kwargs
    )


def run_with_tty(argv: list[str], timeout: float | None = None, **kwargs) -> subprocess.CompletedProcess[str]:
    """`ssh -t host <argv>`, or a person's own shell: a terminal on stdin.

    The master side stays open — a pty whose master is gone fails tcgetattr, so `[[ -t 0 ]]` would
    be false and the case would not be the one it means to be — and an EOT goes into it, so a
    command that reads its stdin (the stub `secrets`, which ends in `cat`) sees end of file.
    """
    master, slave = os.openpty()
    try:
        proc = subprocess.Popen(
            argv, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs
        )
        os.write(master, b"\x04")
        out, err = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)
    finally:
        os.close(slave)
        os.close(master)
