"""`agentbox new` runs from a private copy, so a publish cannot kill a launch in flight.

Bash reads a script incrementally by byte offset. Replace the file under a running one and it
reads the new bytes from the old offset and dies where it stands -- with no error line, no exit
line, and exit status 0, so no caller can detect it either. `~/.dotfiles` is production: every
`dotfiles-publish` replaces `scripts/agentbox` while sessions are launching boxes with it.

`new` is the long subcommand -- image, container, filesystem, then waiting for omp to register --
and two things run it in exactly the wrong window. The move's relauncher did, and a publish at
08:30:17Z on 2026-11-05 killed two stream leads' relaunchers mid-wait (`test_agentbox_handoff.py`
covers that one). The restart registry does too, through every entry's `resume=` line, and that
path is worse: it runs unattended and in bulk straight after a reboot, which is when publishes
cluster, because that is when people are fixing whatever caused the reboot. 75 sessions came back
through it at 04:09Z that morning.

These tests run the preamble as it stands in `scripts/agentbox` -- extracted from the file, not
copied here -- so an edit to it is an edit to what is tested.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

AGENTBOX = Path(__file__).resolve().parent.parent / "agentbox"

TAIL = """
echo "running from: $0"
sleep 6
echo "finished"
"""

REWRITTEN = "#!/bin/bash\necho REWRITTEN BY A PUBLISH\n"


def preamble() -> str:
    """The self-copy block and the line after it that removes the copy, read from the script
    itself."""
    text = AGENTBOX.read_text()
    match = re.search(
        r'^if \[\[ "\$\{1:-\}" == new && -z "\$\{AGENTBOX_SELF_COPY:-\}" \]\]; then\n'
        r"(?:.*?\n)*?^fi\n"
        r"(?:^#.*\n)*"
        r"^\[\[ -z \"\$\{AGENTBOX_SELF_COPY:-\}\" \]\] \|\| .*$",
        text,
        re.MULTILINE,
    )
    assert match, "the self-copy preamble is no longer findable in scripts/agentbox"
    block = match.group(0)
    assert "exec" in block, "the launch must replace this shell, so no copy of it lingers"
    return block


def write_probe(tmp_path: Path) -> Path:
    """The preamble and TAIL, as an executable `agentbox` in tmp_path."""
    probe = tmp_path / "agentbox"
    probe.write_text("#!/bin/bash\nset -uo pipefail\n" + preamble() + "\n" + TAIL)
    probe.chmod(0o755)
    return probe


def run_probe(tmp_path: Path, *, rewrite: bool, bypass: bool) -> dict:
    """Launch the probe, replace its file mid-run as a publish does, report what survived."""
    probe = write_probe(tmp_path)
    out = tmp_path / "out.log"
    env = 'AGENTBOX_SELF_COPY=%s ' % (tmp_path / "pretend-copy") if bypass else ""
    if bypass:
        (tmp_path / "pretend-copy").write_text("")
    script = f"""
export TMPDIR={tmp_path!s}
{env}{probe!s} new >{out!s} 2>&1 &
pid=$!
sleep 2
{f'printf %s {REWRITTEN!r} > {probe!s}' if rewrite else 'true'}
wait "$pid"; echo "exit=$?"
"""
    done = subprocess.run(
        ["bash", "-c", script], check=True, capture_output=True, text=True, timeout=120
    )
    text = out.read_text() if out.exists() else ""
    return {
        "out": text,
        "status": done.stdout.strip().rpartition("exit=")[2],
        "ran_from": (text.splitlines() or [""])[0],
        "copies_left": sorted(p.name for p in tmp_path.glob("agentbox-self-*")),
    }


def test_it_runs_from_a_private_copy_not_the_live_file(tmp_path):
    result = run_probe(tmp_path, rewrite=False, bypass=False)
    assert "agentbox-self-" in result["ran_from"], result
    assert not result["ran_from"].endswith("/agentbox"), (
        "it ran the file a publish replaces, not a copy of it: %r" % result
    )
    assert "finished" in result["out"], result


def test_a_launch_finishes_even_when_a_publish_rewrites_the_script(tmp_path):
    result = run_probe(tmp_path, rewrite=True, bypass=False)
    assert "finished" in result["out"], result
    assert "REWRITTEN" not in result["out"], result


def test_the_private_copy_is_removed_afterwards(tmp_path):
    result = run_probe(tmp_path, rewrite=True, bypass=False)
    assert result["copies_left"] == [], result


def test_the_private_copy_is_gone_while_the_launch_still_runs(tmp_path):
    """The copy removes its own file as it starts, and bash reads the running script through a
    descriptor that it holds open. So while the launch runs, its copy is gone and TMPDIR holds no
    copy. Then the launch prints its last line and exits 0."""
    probe = write_probe(tmp_path)
    out = tmp_path / "out.log"
    with out.open("w") as log:
        launch = subprocess.Popen(
            [str(probe), "new"],
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "TMPDIR": str(tmp_path)},
        )
    try:
        # Wait for TAIL's first line, which it prints once the preamble is done.
        deadline = time.monotonic() + 30
        while "\n" not in out.read_text() and time.monotonic() < deadline:
            time.sleep(0.05)
        ran_from = out.read_text().partition("\n")[0].removeprefix("running from: ")
        assert "agentbox-self-" in ran_from, "the launch did not run from a copy: %r" % ran_from
        copy_exists = Path(ran_from).exists()
        copies = sorted(p.name for p in tmp_path.glob("agentbox-self-*"))
        # Still running after the look, so the look came while the launch ran.
        assert launch.poll() is None, "the launch ended before the test looked: %r" % out.read_text()
        assert not copy_exists and copies == [], (
            "the copy is not gone while the launch runs: %s exists=%s, TMPDIR holds %r"
            % (ran_from, copy_exists, copies)
        )
        status = launch.wait(timeout=60)
    finally:
        if launch.poll() is None:
            launch.kill()
            launch.wait()
    assert status == 0, out.read_text()
    assert out.read_text().splitlines()[-1] == "finished", out.read_text()


def test_running_the_live_file_is_what_fails_and_it_fails_silently(tmp_path):
    """The control. Without the copy a mid-launch publish loses the process, reporting success.

    If this ever passes, bash no longer dies on a rewritten script and the copy could go. Until
    then it is the whole reason for it -- and the exit status is why nothing noticed on 11-05.
    """
    result = run_probe(tmp_path, rewrite=True, bypass=True)
    assert "finished" not in result["out"], result
    assert result["status"] == "0", (
        "the control is expected to die with a success status; a nonzero status here means a "
        "caller could have detected the death and the silence claim needs rewriting: %r" % result
    )
