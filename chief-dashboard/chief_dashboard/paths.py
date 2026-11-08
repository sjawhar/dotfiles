"""Where the chief of staff's own records default to.

A committed file names no machine's own path (`AGENTS.md`, "Everything committed is portable"),
so each record defaults under `$XDG_STATE_HOME` and a machine that keeps its records elsewhere
says so in the environment variable beside each use.
"""

from __future__ import annotations

import os
from pathlib import Path


def chief_state(name: str) -> Path:
    """The default location of one chief-of-staff record, under `$XDG_STATE_HOME/chief/`."""
    root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(root) / "chief" / name
