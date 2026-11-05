"""The box refuses to start on a `--dir` that is not bound into it.

Inside a box an unbound path is not an error: the directory exists, it is writable, it is
empty, and it is the box's own disk, so a session works in it for hours and loses everything
in it when the box ends. Five sessions reported exactly that on 2026-11-05
(dispatch://INFRA-1192/artifact/frictions-md, friction f1), each having "checked" the path by
listing it.

agentbox/entrypoint.sh therefore asks `findmnt -T` for every path in AGENTBOX_DIRS before it
starts anything. Measured in the image on 2026-11-05: a bound path answers with itself or with
a bound parent, a path on the container's own root answers `/`, and a path that is not there at
all answers nothing.

The rules this holds:
  - the first --dir is the directory the session works in, so an unbound one always refuses;
  - any other unbound --dir refuses a fresh launch, and on a resume (AGENTBOX_DROP_GONE_DIRS,
    set by `agentbox new --drop-gone-dirs`) is said and left out instead, since refusing there
    leaves a live session's pane at a shell;
  - an empty answer refuses nothing: that path is absent rather than unbound, and absent fails
    loudly at first use.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent.parent / "agentbox" / "entrypoint.sh"

# The stub stands in for findmnt: "/" for anything under /unbound (a path on the box's own
# root), nothing for /silent (a path that is not there), else the path itself (bound).
FINDMNT_STUB = """#!/bin/bash
path=""
while [ $# -gt 0 ]; do case "$1" in -T) path="$2"; shift;; esac; shift; done
case "$path" in
  /unbound*) echo "/" ;;
  /silent*)  exit 1 ;;
  *)         echo "$path" ;;
esac
"""


def bind_check_block() -> str:
    """The entrypoint's bind check alone, so the test needs no container."""
    text = ENTRYPOINT.read_text()
    match = re.search(
        r"^# Every --dir the launcher named.*?^fi$", text, re.MULTILINE | re.DOTALL
    )
    assert match, "the bind-check block is no longer findable in agentbox/entrypoint.sh"
    return match.group(0)


@pytest.fixture(scope="module")
def stub_path(tmp_path_factory) -> str:
    stubs = tmp_path_factory.mktemp("stubs")
    findmnt = stubs / "findmnt"
    findmnt.write_text(FINDMNT_STUB)
    findmnt.chmod(findmnt.stat().st_mode | stat.S_IXUSR)
    return str(stubs)


def run(stub_path: str, dirs: str, resume: bool = False) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{stub_path}:{os.environ['PATH']}",
        "AGENTBOX_DIRS": dirs,
        "AGENTBOX_DROP_GONE_DIRS": "1" if resume else "",
    }
    return subprocess.run(
        ["bash", "-c", "set -uo pipefail\n" + bind_check_block()],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_every_dir_bound_starts(stub_path):
    assert run(stub_path, "/a:/b:/c").returncode == 0


def test_every_dir_bound_starts_on_a_resume(stub_path):
    assert run(stub_path, "/a:/b:/c", resume=True).returncode == 0


def test_unbound_working_directory_refuses(stub_path):
    done = run(stub_path, "/unbound/x:/b")
    assert done.returncode == 1
    assert "/unbound/x" in done.stderr


def test_unbound_working_directory_refuses_a_resume_too(stub_path):
    """The first --dir is the session's own tree; a box in any other is not that session's."""
    done = run(stub_path, "/unbound/x:/b", resume=True)
    assert done.returncode == 1
    assert "/unbound/x" in done.stderr


def test_a_later_unbound_dir_refuses_a_fresh_launch(stub_path):
    done = run(stub_path, "/a:/unbound/y")
    assert done.returncode == 1
    assert "/unbound/y" in done.stderr


def test_a_later_unbound_dir_is_said_and_dropped_on_a_resume(stub_path):
    """Refusing here would leave a live session's pane at a shell."""
    done = run(stub_path, "/a:/unbound/y", resume=True)
    assert done.returncode == 0
    assert "/unbound/y" in done.stderr
    assert "NOT bound" in done.stderr


def test_a_path_findmnt_cannot_read_refuses_nothing(stub_path):
    """An absent path is not the silent case: it fails loudly at first use."""
    assert run(stub_path, "/a:/silent/z").returncode == 0


def test_no_dirs_at_all_is_not_a_box_with_a_problem(stub_path):
    assert run(stub_path, "").returncode == 0


def test_a_trailing_separator_is_not_a_path(stub_path):
    assert run(stub_path, "/a:").returncode == 0
