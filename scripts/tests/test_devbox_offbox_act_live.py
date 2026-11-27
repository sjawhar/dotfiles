"""`devbox-offbox-act live` names the session a process RUNS, not one it mentions.

The restart guard refuses to restart a session that any live omp process "names". On
2026-11-05 at 04:28Z, the post-reboot restart of sami-devbox skipped three dead sessions --
the Verified Linux stream lead, SPEEDCOACH and PIPE -- because an hourly headless `omp -p`
carries its whole PROMPT in its arguments, and that prompt names the lead it reports to. The
same rule sits in devbox-offbox-watch, so while that checker ran, the watcher could not have
restarted the lead either.

So a `proc` line is printed only for a process whose own session is that id: the value of
`--resume` or `--session-id`, or an absolute transcript path it was given.

A `holder` line names an omp process on the host that holds a session's transcript open FOR
WRITING. That scan used to fork `readlink` for every descriptor of every omp. On sami-devbox on
2026-11-17 that was 3,709 descriptors of 62 omps and 22.8 s at load 287 (11:52Z), 21.7 s of a
30.8 s `live` (11:54Z), and 30-51 s of each 60 s test here at load 330-390 (12:02Z). The holder
tests below run against a /proc of their own (`DEVBOX_OFFBOX_PROC`); the last one runs against
this host's.
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
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
    # No process runs a session by its command line; `run_live` replaces this.
    _executable(shims / "ps", "#!/bin/sh\nexit 0\n")

    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("DEVBOX_OFFBOX_PROC", None)
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


# -- `holder`: an omp on the host holding a session's transcript open for writing --------------

# A descriptor's access mode, as the octal `flags:` of /proc/<pid>/fdinfo/<fd> shows it.
WRITER = "02102001"  # O_WRONLY|O_APPEND|O_CLOEXEC|O_LARGEFILE: omp's own session writer
READER = "02100000"  # O_RDONLY|O_CLOEXEC|O_LARGEFILE: a tool reading another session's file
READ_WRITE = "0100002"  # O_RDWR|O_LARGEFILE
OTHER = "01a170e0-e386-7223-9afa-1de32c879230"
SESSIONS = "/work/.omp/agent/sessions/-src"

# How long `live` may take with ps, docker and omp-session-live stubbed, which leaves the host scan
# and little else. On sami-devbox on 2026-11-17 it took 37.6 s at load 438 before the scan was one
# pass, and about 1 s after it.
BUDGET_SECONDS = 10


def transcript(session: str, where: str = SESSIONS) -> str:
    return f"{where}/2026-11-17T09-00-00-000Z_{session}.jsonl"


class FakeProc:
    """A /proc of processes, each with its `comm`, its descriptors and their fdinfo."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir()
        # A real /proc holds entries that are no process.
        (root / "self").mkdir()
        (root / "sys").mkdir()

    def process(self, pid: int, comm: str = "omp",
                fds: dict[int, tuple[str, str | None]] | None = None) -> Path:
        """`fds` maps a descriptor to its target and its flags. Flags of None write no fdinfo, as
        for a descriptor closed between the two reads; the scan reads it only for a transcript."""
        directory = self.root / str(pid)
        (directory / "fd").mkdir(parents=True)
        (directory / "fdinfo").mkdir()
        (directory / "comm").write_text(comm + "\n")
        for fd, (target, flags) in (fds or {}).items():
            os.symlink(target, directory / "fd" / str(fd))
            if flags is not None:
                (directory / "fdinfo" / str(fd)).write_text(
                    f"pos:\t0\nflags:\t{flags}\nmnt_id:\t30\nino:\t1\n")
        return directory


@pytest.fixture
def proc(tmp_path: Path, box: dict) -> FakeProc:
    fake = FakeProc(tmp_path / "proc")
    box["env"]["DEVBOX_OFFBOX_PROC"] = str(fake.root)
    return fake


def live(box: dict, *ids: str, timeout: float = 60) -> tuple[list[str], float]:
    """Every line `live` prints, and how long it took."""
    started = time.monotonic()
    try:
        done = subprocess.run(["bash", str(ACT), "live", "120", *ids], capture_output=True,
                              text=True, env=box["env"], timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.fail(f"`live` was still running after {timeout} s")
    elapsed = time.monotonic() - started
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines(), elapsed


def holders(box: dict, *ids: str) -> list[str]:
    lines, _ = live(box, *ids)
    return [line for line in lines if line.startswith("holder ")]


def test_an_omp_writing_the_transcript_holds_it(box, proc):
    proc.process(4100, fds={21: (transcript(LEAD), WRITER)})
    assert holders(box, LEAD) == [f"holder {LEAD} host 4100"]


def test_a_read_write_descriptor_holds_it_too(box, proc):
    proc.process(4100, fds={21: (transcript(LEAD), READ_WRITE)})
    assert holders(box, LEAD) == [f"holder {LEAD} host 4100"]


def test_an_omp_reading_the_transcript_does_not_hold_it(box, proc):
    """A tool in one session reads another's transcript; only a writer runs it."""
    proc.process(4100, fds={21: (transcript(LEAD), READER)})
    assert holders(box, LEAD) == []


@pytest.mark.parametrize("comm", ["bun", "omp-dup-watch", "python3"])
def test_only_an_omp_is_asked(box, proc, comm):
    proc.process(4100, comm=comm, fds={21: (transcript(LEAD), WRITER)})
    assert holders(box, LEAD) == []


@pytest.mark.parametrize("target", [
    f"{SESSIONS}/{LEAD}.jsonl",
    transcript(LEAD) + ".bak",
    f"{SESSIONS}/2026-11-17T09-00-00-000Z_{LEAD}.json",
    transcript(OTHER),
], ids=["no-underscore", "bak", "json", "another-session"])
def test_a_file_that_is_not_that_sessions_transcript_is_not_held(box, proc, target):
    proc.process(4100, fds={21: (target, WRITER)})
    assert holders(box, LEAD) == []


@pytest.mark.parametrize("flags", [None, "", "zz"], ids=["closed", "empty", "garbled"])
def test_a_descriptor_whose_access_mode_cannot_be_read_holds_nothing(box, proc, flags):
    proc.process(4100, fds={21: (transcript(LEAD), flags)})
    assert holders(box, LEAD) == []


def test_each_session_a_process_writes_is_its_own_line(box, proc):
    proc.process(4100, fds={21: (transcript(LEAD), WRITER), 22: (transcript(COACH), WRITER),
                            23: (transcript(OTHER), WRITER)})
    proc.process(4200, fds={30: (transcript(COACH), WRITER)})
    assert sorted(holders(box, LEAD, COACH)) == [
        f"holder {LEAD} host 4100", f"holder {COACH} host 4100", f"holder {COACH} host 4200"]


def test_a_subagent_transcript_is_the_subagents_not_its_parents(box, proc):
    """omp keeps a subagent's transcript in a directory named for its parent's transcript."""
    parent = f"{SESSIONS}/2026-11-17T09-00-00-000Z_{LEAD}"
    proc.process(4100, fds={21: (transcript(COACH, parent), WRITER)})
    assert holders(box, LEAD, COACH) == [f"holder {COACH} host 4100"]


@pytest.mark.parametrize("where", [f"{SESSIONS}/a dir  with spaces", f"{SESSIONS}/a\ttab and\na newline"],
                         ids=["spaces", "tab-and-newline"])
def test_a_transcript_path_is_read_whole(box, proc, where):
    proc.process(4100, fds={21: (transcript(LEAD, where), WRITER)})
    assert holders(box, LEAD) == [f"holder {LEAD} host 4100"]


def test_a_process_whose_descriptors_cannot_be_read_is_passed_over(box, proc):
    """Another user's process, or a boxed one under a shifted uid: the host cannot read its fds."""
    if os.geteuid() == 0:
        pytest.skip("root reads every fd directory, so none can be hidden from it")
    hidden = proc.process(4100, fds={21: (transcript(LEAD), WRITER)})
    proc.process(4200, fds={21: (transcript(LEAD), WRITER)})
    (hidden / "fd").chmod(0)
    try:
        assert holders(box, LEAD) == [f"holder {LEAD} host 4200"]
    finally:
        (hidden / "fd").chmod(0o755)


def test_a_process_gone_mid_scan_is_passed_over(box, proc):
    gone = proc.process(4100, fds={21: (transcript(LEAD), WRITER)})
    (gone / "comm").unlink()
    proc.process(4200, fds={21: (transcript(LEAD), WRITER)})
    assert holders(box, LEAD) == [f"holder {LEAD} host 4200"]


def test_the_host_scan_reads_every_descriptor_in_one_pass(box, proc):
    """64 omps of 160 descriptors each, past sami-devbox's 62 omps and 3,709 descriptors of
    2026-11-17, and 500 other processes. A fork of `readlink` per descriptor spends the whole
    budget on this alone; one `find` over the omps' fd directories reads them in one process."""
    sessions = [f"01a1e000-0000-7000-8000-{n:012x}" for n in range(64)]
    for n, session in enumerate(sessions):
        pid = 5000 + n
        fds: dict[int, tuple[str, str | None]] = {
            fd: (f"socket:[{pid * 1000 + fd}]", None) for fd in range(160)}
        fds[0] = ("/dev/null", None)
        fds[1] = (f"pipe:[{pid}]", None)
        fds[3] = ("/usr/lib/x86_64-linux-gnu/libc.so.6", None)
        fds[20] = (transcript(session), WRITER)
        fds[21] = (transcript(LEAD), READER)
        fds[22] = (transcript(OTHER), READER)
        proc.process(pid, fds=fds)
    proc.process(9000, fds={21: (transcript(LEAD), WRITER)})
    for pid in range(10000, 10500):
        proc.process(pid, comm="bash")

    lines, elapsed = live(box, LEAD, COACH, sessions[7], timeout=300)

    assert sorted(line for line in lines if line.startswith("holder ")) == [
        f"holder {LEAD} host 9000", f"holder {sessions[7]} host 5007"]
    assert elapsed < BUDGET_SECONDS, f"`live` took {elapsed:.1f} s over 10,241 descriptors"


def test_live_answers_inside_its_budget_on_this_host(box):
    """The scan of this host's own /proc, every other reading stubbed: the reading that broke.

    A host with no omp whose descriptors this user can read gives the scan nothing to read, and
    the test says so rather than pass."""
    omps = descriptors = 0
    for entry in Path("/proc").iterdir():
        try:
            # Bytes: a comm is whatever a process set, and another test's is not UTF-8.
            if not entry.name.isdigit() or (entry / "comm").read_bytes().strip() != b"omp":
                continue
            descriptors += len(os.listdir(entry / "fd"))
            omps += 1
        except OSError:
            continue
    if not omps:
        pytest.skip("no omp on this host whose descriptors this user can read")

    lines, elapsed = live(box, LEAD, timeout=300)

    assert lines and lines[-1].startswith("boxscan ok"), lines
    assert elapsed < BUDGET_SECONDS, (
        f"`live` took {elapsed:.1f} s over {omps} omps holding {descriptors} descriptors")
