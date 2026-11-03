"""Shake-log recovery in scripts/omp-session-render.

The disk mover replaces a moved session's transcript with a symlink into
/mnt/durable/sami/omp-sessions-moved/, while omp's N.shake.log files stay in the artifact
directory beside the symlink. A lookup that resolves the path first searches only under the
move target and finds nothing, so every moved session's subagents render placeholders
instead of their real tool output, silently (L1094).
"""

import json
import subprocess
import sys
from pathlib import Path

RENDER = Path(__file__).resolve().parents[1] / "omp-session-render"

ENTRY = {
    "type": "message",
    "message": {
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "before [shaken ~9 tokens — recover: artifact://7 (region 2)] after",
            }
        ],
    },
    "timestamp": "2026-11-04T01:00:00Z",
}
SHAKE_LOG = "### region 1 (one)\nWRONG REGION\n### region 2 (two)\nTHE REAL TOOL OUTPUT\n"


def render(path):
    out = subprocess.run(
        [sys.executable, str(RENDER), "show", str(path), "1", "1"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def write_transcript(path):
    path.write_text(json.dumps(ENTRY) + "\n")


def test_recovers_through_a_moved_sessions_symlink(tmp_path):
    """The transcript is a symlink elsewhere; the shake log sits beside the symlink."""
    moved = tmp_path / "omp-sessions-moved" / "sess"
    moved.mkdir(parents=True)
    write_transcript(moved / "sub.jsonl")

    live = tmp_path / "sessions" / "proj"
    live.mkdir(parents=True)
    link = live / "sub.jsonl"
    link.symlink_to(moved / "sub.jsonl")
    (live / "7.shake.log").write_text(SHAKE_LOG)

    body = render(link)
    assert "THE REAL TOOL OUTPUT" in body
    assert "[shaken ~9 tokens" not in body
    assert "WRONG REGION" not in body


def test_recovers_beside_a_plain_transcript(tmp_path):
    """No symlink: the ordinary case keeps working."""
    live = tmp_path / "sessions" / "proj"
    live.mkdir(parents=True)
    write_transcript(live / "sub.jsonl")
    (live / "7.shake.log").write_text(SHAKE_LOG)

    assert "THE REAL TOOL OUTPUT" in render(live / "sub.jsonl")


def test_recovers_from_a_grandparent_artifact_directory(tmp_path):
    """omp writes every shake log into the TOP-LEVEL session's artifact directory."""
    live = tmp_path / "sessions" / "proj"
    nested = live / "parent-artifacts" / "child-artifacts"
    nested.mkdir(parents=True)
    write_transcript(nested / "sub.jsonl")
    (live / "7.shake.log").write_text(SHAKE_LOG)

    assert "THE REAL TOOL OUTPUT" in render(nested / "sub.jsonl")


def test_leaves_the_placeholder_when_no_log_exists(tmp_path):
    """Nothing to recover from: the reader must see the placeholder, not silence."""
    live = tmp_path / "sessions" / "proj"
    live.mkdir(parents=True)
    write_transcript(live / "sub.jsonl")

    assert "[shaken ~9 tokens" in render(live / "sub.jsonl")
