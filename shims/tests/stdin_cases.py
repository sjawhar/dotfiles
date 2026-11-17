#!/usr/bin/env python3
"""Running a shim in a session with, and without, a controlling terminal (INFRA-1538, INFRA-4089).

The identity shims take a command that arrives over SSH in a session with no controlling terminal
(`ssh host cmd`) for an agent's. The session's terminal decides, not this command's stdin and not
`SSH_TTY` alone: sshd gives a person's interactive session a terminal, tmux gives every pane one,
and every process of that session has it as its controlling terminal, piped stdin included. A tmux
server started before `.tmux.conf` carried `SSH_TTY` gives its panes `SSH_CONNECTION` without
`SSH_TTY`, and that alone took a person's own pane for an agent's (dispatch://INFRA-4089).

So every case runs in a session of its own, which has the pty it names as its controlling terminal
or none at all, and names its own stdin too: a case must never be decided by the terminal, or the
lack of one, of whoever runs the suite.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys

# A plausible `SSH_CONNECTION`: client address and port, server address and port.
SSH_CONNECTION = "10.0.0.1 54321 10.0.0.2 22"
# A plausible `SSH_TTY`: the terminal sshd gave the person's session.
SSH_TTY = "/dev/pts/7"
# The environment of a person's own interactive SSH session, whatever that session's command
# reads on stdin.
SSH_PERSON = {"SSH_CONNECTION": SSH_CONNECTION, "SSH_TTY": SSH_TTY}

# subprocess can start a session (start_new_session) but cannot give it a controlling terminal,
# and a preexec_fn is not safe in a test that runs threads. So the command starts under this
# trampoline: a session leader with no terminal yet, it takes the pty open on the fd it is given
# (TIOCSCTTY), as sshd does for `ssh -t` and tmux for a pane, closes that fd unless it is stdin,
# and execs the command.
_TAKE_TERMINAL = (
    "import fcntl, os, sys, termios\n"
    "fd = int(sys.argv[1])\n"
    "fcntl.ioctl(fd, termios.TIOCSCTTY, 0)\n"
    "if fd:\n"
    "    os.close(fd)\n"
    "os.execvp(sys.argv[2], sys.argv[2:])\n"
)

# The other way round: a pty on stdin that the session does NOT control. bash started as a
# session leader with no terminal opens the pty on its stdin by name (openat "/dev/pts/N",
# O_RDWR|O_NONBLOCK, strace on 2026-11-17) and so takes it as its controlling terminal; the
# command therefore runs as the child of the session leader, which can take none.
_NOT_LEADER = (
    "import os, sys\n"
    "pid = os.fork()\n"
    "if not pid:\n"
    "    os.execvp(sys.argv[1], sys.argv[1:])\n"
    "sys.exit(os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]))\n"
)


def run_without_tty(argv: list[str], *, pty_on_stdin: bool = False, timeout: float | None = None,
                    **kwargs) -> subprocess.CompletedProcess[str]:
    """`ssh host <argv>`: a session with no controlling terminal, stdin /dev/null.

    With pty_on_stdin, stdin is a pty all the same, one this session does not control: `[[ -t 0 ]]`
    is true there and the session still has no terminal, which tells a test of the session's
    terminal from a test of stdin.
    """
    if pty_on_stdin:
        return _run_on_pty(argv, controlling=False, input=None, timeout=timeout, **kwargs)
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
                          start_new_session=True, timeout=timeout, **kwargs)


def run_with_tty(argv: list[str], *, input: str | None = None, timeout: float | None = None,
                 **kwargs) -> subprocess.CompletedProcess[str]:
    """A session whose controlling terminal is a pty: a person's own shell, a tmux pane, or
    `ssh -t host <argv>`.

    stdin is that terminal. With `input`, stdin is a pipe carrying it instead and the terminal stays
    the session's: a command in a pipeline the person typed (`echo x | gh …`), or git handing its
    credential helper the request.
    """
    return _run_on_pty(argv, controlling=True, input=input, timeout=timeout, **kwargs)


def _run_on_pty(argv: list[str], *, controlling: bool, input: str | None, timeout: float | None,
                **kwargs) -> subprocess.CompletedProcess[str]:
    """argv in a session of its own, with a pty on stdin (or a pipe carrying `input`), and that pty
    its controlling terminal when `controlling`.

    The master side stays open — a pty whose master is gone fails tcgetattr, so `[[ -t 0 ]]` would
    be false and the case would not be the one it means to be — and an EOT goes into it, so a
    command that reads the terminal (the stub `secrets`, which ends in `cat`) sees end of file. A
    command still running at the timeout is killed with its whole session.
    """
    master, slave = os.openpty()
    try:
        command, pass_fds = [sys.executable, "-I", "-c", _NOT_LEADER, *argv], ()
        if controlling:
            terminal = 0 if input is None else slave
            command = [sys.executable, "-I", "-c", _TAKE_TERMINAL, str(terminal), *argv]
            pass_fds = () if input is None else (slave,)
        proc = subprocess.Popen(
            command, stdin=slave if input is None else subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True, pass_fds=pass_fds, **kwargs
        )
        os.write(master, b"\x04")
        try:
            out, err = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)
    finally:
        os.close(slave)
        os.close(master)
