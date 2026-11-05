"""The move's relauncher runs from a private copy, so a publish cannot kill it mid-flight.

`agentbox move --self` hands the last step to a detached relauncher: hang omp up, wait up to
180 s for its teardown, respawn the pane into the new box. That relauncher used to execute
`scripts/agentbox` itself.

~/.dotfiles is production. Every publish REPLACES that file on disk while sessions are using
it, and bash reads a script incrementally by byte offset, so a file swapped under a running one
makes it read the new bytes from the old offset and die where it stands, with no error.

On 2026-11-05 that stranded two stream leads. FRAC's relauncher started at 08:27:25Z and
BASELINE's at 08:29:05Z; both logged "hung up; waiting for its teardown"; the file was rewritten
at 08:30:17Z by a publish. Both were inside the sleep loop. Neither logged another line, and the
loop always logs on exit, so both processes died there. Each lead's omp was hung up and its pane
sat at a shell until a person respawned it.

These tests drive the hand-off's own launch line, cut from the script, against exactly that:
rewrite the original mid-flight and require the relauncher to finish anyway. The old line fails
them, which is the point.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

AGENTBOX = Path(__file__).resolve().parent.parent / "agentbox"

PROBE = """#!/bin/bash
echo "running from: $0"
sleep 6
echo "finished"
"""

REWRITTEN = "#!/bin/bash\necho REWRITTEN BY A PUBLISH\n"


def launch_line() -> str:
    """The hand-off's own `setsid` line, from the script, with the log redirect removed."""
    text = AGENTBOX.read_text()
    match = re.search(r"^    setsid --fork bash -c .*?\n(?:.*?\n)*?.*?&$", text, re.MULTILINE)
    assert match, "the hand-off's setsid line is no longer findable in scripts/agentbox"
    line = match.group(0)
    assert "exec" not in line, (
        "the relauncher must RUN its private copy, not exec it: exec replaces the shell and "
        "discards the trap that removes the copy"
    )
    # `--fork` is the whole reason this relauncher outlives the hang-up it performs. Measured
    # 2026-11-05 over 32 observations on two omp builds: omp's teardown SIGTERMs what is
    # reachable from it BY PARENT LINK, and plain `setsid` changes session and process group
    # but not the parent, so the relauncher is killed 86 ms to 2.2 s after it hangs its omp up
    # -- before that omp has even exited. 19 of 26 plain-setsid arms died; 4 of 4 `--fork` arms
    # lived. That is how FRAC and BASELINE were stranded at 08:27:25Z and 08:29:05Z.
    assert "setsid --fork" in line, (
        "the relauncher must be started with `setsid --fork`: plain setsid leaves its parent "
        "the dying omp, and omp's hang-up teardown kills it mid-wait"
    )
    return line.replace("</dev/null >>\"$log\" 2>&1", "</dev/null >\"$out\" 2>&1")


def run_handoff(tmp_path: Path, rewrite: bool) -> dict:
    original = tmp_path / "relaunch-probe.sh"
    original.write_text(PROBE)
    original.chmod(0o755)
    out = tmp_path / "out.log"
    marker = tmp_path / "copy-path"

    script = f"""
set -uo pipefail
out={out!s}
run_copy="$(mktemp {tmp_path!s}/agentbox-relaunch-XXXXXX)"
printf '%s' "$run_copy" > {marker!s}
cat {original!s} >"$run_copy" && chmod +x "$run_copy"
{launch_line().replace('"$run_copy" move --relaunch "$@"', '"$run_copy"')}
sleep 2
{f'printf %s {REWRITTEN!r} > {original!s}' if rewrite else 'true'}
sleep 9
"""
    subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True, timeout=120)
    copy_path = Path(marker.read_text())
    return {
        "out": out.read_text() if out.exists() else "",
        "copy_remains": copy_path.exists(),
        "ran_from": (out.read_text().splitlines() or [""])[0] if out.exists() else "",
    }


def test_it_runs_from_a_private_copy_not_the_live_file(tmp_path):
    result = run_handoff(tmp_path, rewrite=False)
    assert "agentbox-relaunch-" in result["ran_from"], result
    assert "relaunch-probe.sh" not in result["ran_from"], result


def test_it_finishes_even_when_a_publish_rewrites_the_original(tmp_path):
    """The 2026-11-05 case. Running the live file, this never prints the last line."""
    result = run_handoff(tmp_path, rewrite=True)
    assert "finished" in result["out"], result
    assert "REWRITTEN" not in result["out"], result


def test_the_private_copy_is_removed_afterwards(tmp_path):
    result = run_handoff(tmp_path, rewrite=True)
    assert not result["copy_remains"], "the private copy leaked"


def test_the_live_file_running_itself_is_what_fails(tmp_path):
    """The control: without the copy, a mid-flight rewrite loses the process silently.

    If this ever passes, a publish no longer replaces a running script in place and the copy
    could be dropped. Until then it is the reason the copy exists.
    """
    original = tmp_path / "probe.sh"
    original.write_text(PROBE)
    original.chmod(0o755)
    out = tmp_path / "control.log"
    subprocess.run(
        ["bash", "-c", f'setsid {original!s} </dev/null >{out!s} 2>&1 & sleep 2; '
                       f'printf %s {REWRITTEN!r} > {original!s}; sleep 9'],
        check=True, capture_output=True, text=True, timeout=120,
    )
    assert "finished" not in out.read_text(), (
        "a rewritten script no longer kills the process running it; the private copy in "
        "move_hand_off may no longer be needed"
    )
