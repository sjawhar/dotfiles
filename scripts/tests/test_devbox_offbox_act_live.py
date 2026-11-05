"""`devbox-offbox-act live` names the session a process RUNS, not one it mentions.

The restart guard refuses to restart a session that any live omp process "names". On
2026-11-05 at 04:28Z, the post-reboot restart of sami-devbox skipped three dead sessions --
the Verified Linux stream lead, SPEEDCOACH and PIPE -- because an hourly headless `omp -p`
carries its whole PROMPT in its arguments, and that prompt names the lead it reports to. The
same rule sits in devbox-offbox-watch, so while that checker ran, the watcher could not have
restarted the lead either.

So a `proc` line is printed only for a process whose own session is that id: the value of
`--resume` or `--session-id`, or an absolute transcript path it was given.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ACT = Path(__file__).resolve().parent.parent / "devbox-offbox-act"

LEAD = "01a15f9c-6bee-7127-b4f1-907ec8d3176d"
COACH = "01a16a9e-a9b0-761c-97bf-28d7cf2eaac2"
OMP = "/home/sami/.mise/installs/github-sjawhar-oh-my-pi/18.16.0/bin/omp"


def _executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def box(tmp_path: Path) -> dict[str, str]:
    """A HOME holding a stub omp-session-live, `ps` and `docker`.

    The script exports a PATH of its own, rooted at `$HOME/.dotfiles`, so a stub reaches it
    only from there -- `$HOME/.dotfiles/shims` is that PATH's first entry.
    """
    home = tmp_path / "home"
    scripts = home / ".dotfiles" / "scripts"
    shims = home / ".dotfiles" / "shims"
    scripts.mkdir(parents=True)
    shims.mkdir(parents=True)
    # Every session is dead, so only the process scan can print anything.
    _executable(scripts / "omp-session-live", "#!/bin/sh\nexit 1\n")
    # No containers, so the box half prints `boxscan ok 0 containers` and nothing else.
    _executable(shims / "docker", "#!/bin/sh\nexit 0\n")

    env = dict(os.environ)
    env["HOME"] = str(home)
    return {"env": env, "stubs": str(shims)}


def run_live(box: dict[str, str], ps_output: str, *ids: str) -> list[str]:
    _executable(
        Path(box["stubs"]) / "ps",
        "#!/bin/sh\ncat <<'PSEOF'\n" + ps_output + "\nPSEOF\n",
    )
    done = subprocess.run(
        ["bash", str(ACT), "live", "120", *ids],
        capture_output=True,
        text=True,
        env=box["env"],
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return [line for line in done.stdout.splitlines() if line.startswith("proc ")]


def test_a_prompt_that_names_another_session_is_not_that_session(box):
    """The 2026-11-05 case: the drift checker's prompt names the lead it reports to."""
    checker = (
        f" 82563 timeout 1500 {OMP} -p --model anthropic/claude-opus-5 You are the"
        f" Verified Linux drift checker. Report to the lead {LEAD} every hour."
    )
    assert run_live(box, checker, LEAD) == []


def test_resume_argument_is_that_session(box):
    assert run_live(box, f" 684475 {OMP} --resume {LEAD}", LEAD)


def test_resume_in_its_equals_form_is_that_session(box):
    assert run_live(box, f" 684475 {OMP} --session-id={COACH} --foo", COACH)


def test_an_agentbox_launch_line_is_that_session(box):
    line = f" 664560 bash -lc agentbox new --drop-gone-dirs --dir /home/sami/src -- omp --resume {COACH}"
    assert run_live(box, line, COACH)


def test_an_absolute_transcript_path_is_that_session(box):
    line = f" 12350 {OMP} /mnt/nvme/sami/sessions/20261104_{LEAD}.jsonl"
    assert run_live(box, line, LEAD)


def test_a_transcript_named_inside_a_prompt_is_not(box):
    """A bare `<id>.jsonl` in prose is a mention; the real argument is a path."""
    line = f" 12352 {OMP} -p --mode json Read {LEAD}.jsonl and summarise it"
    assert run_live(box, line, LEAD) == []


def test_a_process_that_does_not_run_omp_is_never_a_holder(box):
    """A grep over the transcripts used to block the session it was reading."""
    assert run_live(box, f" 12349 grep -r {LEAD} /home/sami/.omp/agent/sessions", LEAD) == []


def test_each_id_is_judged_on_its_own(box):
    """One line mentioning the lead and running the coach answers for the coach alone."""
    line = f" 684475 {OMP} --resume {COACH} --note report-to-{LEAD}"
    found = run_live(box, line, LEAD, COACH)
    assert [l.split()[1] for l in found] == [COACH]
