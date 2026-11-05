"""`devbox-offbox-act --forced` is a key's whole vocabulary: four verbs and a session id.

The watch of sami-devbox moves into a CronJob in the dispatch cluster (dispatch://INFRA-993), and
a pod cannot ship the acting half over ssh stdin the way sami-rocqperf does: its key carries
`restrict,command="<pinned path>/devbox-offbox-act --forced"`, and a forced command gets no stdin.
So this file is where the key's power is written down and held: only `probe`, `live <uuid>...`,
`restart <uuid>` and `await <uuid>` are accepted, every id is matched against ^[0-9a-f-]{36}$,
and a `restart` reads its resume line from the host's own registry -- never from the caller, which
is what stops the key asking for an arbitrary `agentbox new`.

What this file CANNOT prove, because it runs the command directly as sshd would rather than
through sshd: that a shell, a port forward, an agent forward and a tty are refused. Those are
`restrict`'s, refused by sshd before this file runs; they are proven by hand once the key is on
the box (the PR body says how).
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

ACT = Path(__file__).resolve().parent.parent / "devbox-offbox-act"

SESSION = "01a1a7fc-a94f-72f8-9651-bd5ecd335709"
OTHER = "01a15f9c-6bee-7127-b4f1-907ec8d3176d"


def _executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def box(tmp_path: Path) -> dict:
    """A HOME with a stub tmux and docker, a registry, and a marker directory.

    The script exports a PATH of its own rooted at `$HOME/.dotfiles`, so a stub is reached only
    from there; `$HOME/.dotfiles/shims` is that PATH's first entry.
    """
    home = tmp_path / "home"
    shims = home / ".dotfiles" / "shims"
    scripts = home / ".dotfiles" / "scripts"
    shims.mkdir(parents=True)
    scripts.mkdir(parents=True)
    registry = tmp_path / "registry"
    registry.mkdir()
    markers = tmp_path / "markers"
    markers.mkdir()
    calls = tmp_path / "tmux-calls"
    # Every tmux call is recorded and nothing is run. `has-session` answers "no session", so the
    # restart path also creates one, which is the real sequence.
    _executable(shims / "tmux", f"#!/bin/sh\nprintf '%s\\n' \"$*\" >>{calls}\n"
                                "case \"$1\" in has-session) exit 1 ;; "
                                "new-window) echo 'agents:7 @7' ;; esac\nexit 0\n")
    _executable(shims / "docker", "#!/bin/sh\nexit 0\n")
    _executable(scripts / "omp-session-live", "#!/bin/sh\nexit 1\n")
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "DEVBOX_OFFBOX_REGISTRY": str(registry),
        "DEVBOX_OFFBOX_TRAIN": str(tmp_path / "no-train"),
        "AGENTBOX_RESTART_MARKERS": str(markers),
    })
    return {"env": env, "registry": registry, "markers": markers, "calls": calls}


def forced(box: dict, command: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run the forced command exactly as sshd runs it: no argument, the command in the
    environment."""
    env = dict(box["env"])
    env["SSH_ORIGINAL_COMMAND"] = command
    return subprocess.run([str(ACT), "--forced"], env=env, capture_output=True, text=True,
                          timeout=timeout)


def entry(box: dict, name: str, **fields: str) -> Path:
    path = box["registry"] / f"{name}.entry"
    lines = {"session": name, "title": "UTIL", "tmux": "agents",
             "cwd": str(box["registry"]), "resume": "agentbox new --dir /srv -- omp --resume " + name,
             "auto_restart": "yes"}
    lines.update(fields)
    path.write_text("".join(f"{k}={v}\n" for k, v in lines.items()))
    return path


# --- what the key refuses --------------------------------------------------------------------


@pytest.mark.parametrize("command, because", [
    ("", "no command at all: an interactive login"),
    ("bash", "a shell by name"),
    ("bash -l", "a login shell"),
    ("sh -c id", "a shell with a command"),
    ("agentbox new --dir /", "the launch the key must never be able to ask for"),
    ("agentbox new --dir / -- omp", "the same with an omp after it"),
    ("restart", "a verb with no id"),
    ("restart deadbeef", "an id that is not a session id"),
    ("restart 01a1a7fc-a94f-72f8-9651-bd5ecd33570", "an id one character short"),
    ("restart 01A1A7FC-A94F-72F8-9651-BD5ECD335709", "an id in capitals"),
    (f"restart {SESSION} {OTHER}", "two ids where one is allowed"),
    (f"restart {SESSION} agentbox new -- omp", "a resume line supplied by the caller"),
    (f"live {SESSION}; rm -rf /", "a second command after a semicolon"),
    (f"live {SESSION} && id", "a second command after &&"),
    (f"live $(id)", "a command substitution"),
    ("live 120", "the freshness window, which is this file's and not the caller's"),
    (f"live 600 {SESSION}", "a freshness window before the id"),
    ("live", "live with no id"),
    (f"await {SESSION} 99999", "an await window supplied by the caller"),
    ("await", "await with no id"),
    ("probe extra", "probe with an argument"),
    ("probe /etc/shadow", "probe pointed at a file"),
    ("scp -f /etc/shadow", "scp, which is how a forced command is usually read for free"),
    ("rsync --server", "rsync, the same"),
    ("internal-sftp", "sftp"),
    (f"LIVE {SESSION}", "a verb in capitals"),
    (f"/bin/echo live {SESSION}", "a path in front of the verb"),
    (f"live {SESSION}\nrestart {SESSION}", "a second line"),
    (f"probe {SESSION}", "probe with a session id, the one argument shape the character class and"
                        " the id loop both admit"),
    ("live " + "-" * 36, "36 hyphens, which the old `[0-9a-f-]{36}` called a session id"),
    (f"live {SESSION[:-1]}0x", "an id with a non-hexadecimal character in its last group"),
])
def test_forced_refuses(box: dict, command: str, because: str) -> None:
    done = forced(box, command)
    assert done.returncode != 0, f"{command!r} ({because}) was accepted:\n{done.stdout}"
    assert done.stderr.strip().count("\n") == 0, f"more than one line on stderr: {done.stderr!r}"
    assert done.stderr.startswith("devbox-offbox-act --forced:"), done.stderr
    assert not box["calls"].exists(), f"{command!r} ({because}) reached tmux"


def test_forced_takes_no_argument_of_its_own(box: dict) -> None:
    """A second argument would be a way past SSH_ORIGINAL_COMMAND's parsing."""
    env = dict(box["env"])
    env["SSH_ORIGINAL_COMMAND"] = f"live {SESSION}"
    done = subprocess.run([str(ACT), "--forced", "restart", SESSION], env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode != 0
    assert "takes no argument of its own" in done.stderr


def test_live_refuses_more_ids_than_the_cap(box: dict) -> None:
    """An unbounded list is not privilege, it is the watched box's CPU: 3,000 ids ran 34 s wall
    against the pinned copy with the box scan stubbed out (agent-infra#2194's gate). The count is
    refused before any per-id work."""
    ids = " ".join(f"01a1a7fc-a94f-72f8-9651-{i:012d}" for i in range(257))
    done = forced(box, f"live {ids}")
    assert done.returncode != 0
    assert "at most 256 session ids" in done.stderr, done.stderr
    assert not box["calls"].exists()


def test_live_accepts_the_cap_itself(box: dict) -> None:
    """The boundary on the other side: 256 ids are accepted, so the cap refuses nothing the watch
    asks for (the registry's largest was about 20 entries, all in one call)."""
    ids = [f"01a1a7fc-a94f-72f8-9651-{i:012d}" for i in range(256)]
    done = forced(box, "live " + " ".join(ids), timeout=300)
    assert done.returncode == 0, done.stderr
    assert f"live {ids[0]} 0" in done.stdout
    assert f"live {ids[-1]} 0" in done.stdout


# --- what it accepts, and what a restart reads -------------------------------------------------


def test_restart_runs_the_registry_s_resume_line_and_not_the_caller_s(box: dict) -> None:
    """The one thing the forced command exists for: the resume line is the owner's, from this
    host's registry, whatever the caller says."""
    entry(box, SESSION, resume="agentbox new --dir /srv/work -- omp --resume " + SESSION)
    done = forced(box, f"restart {SESSION}")
    assert done.returncode == 0, done.stderr
    calls = box["calls"].read_text()
    # tmux is handed one argument, so %q has escaped every space in the resume line.
    assert "--resume" in calls and SESSION in calls, calls
    assert "/srv/work" in calls, calls
    assert f"resume=agentbox new --dir /srv/work -- omp --resume {SESSION}" in done.stdout


def test_restart_refuses_an_entry_naming_another_session(box: dict) -> None:
    """A file dropped into the registry cannot borrow a session id it does not hold."""
    entry(box, SESSION, session=OTHER)
    done = forced(box, f"restart {SESSION}")
    assert done.returncode == 1
    assert f"holds session={OTHER}" in done.stderr
    assert not box["calls"].exists()


def test_restart_refuses_a_session_with_no_entry(box: dict) -> None:
    done = forced(box, f"restart {SESSION}")
    assert done.returncode == 1
    assert "no registry entry" in done.stderr
    assert not box["calls"].exists()


@pytest.mark.parametrize("missing", ["tmux", "cwd", "resume", "title"])
def test_restart_refuses_an_entry_missing_a_field(box: dict, missing: str) -> None:
    entry(box, SESSION, **{missing: ""})
    done = forced(box, f"restart {SESSION}")
    assert done.returncode == 1
    assert f"has no {missing}" in done.stderr
    assert not box["calls"].exists()


def test_live_accepts_one_id_and_several(box: dict) -> None:
    for command in (f"live {SESSION}", f"live {SESSION} {OTHER}"):
        done = forced(box, command, timeout=180)
        assert done.returncode == 0, done.stderr
        # Every session here is dead, so the readings are the `live <id> 0` lines and the scan.
        assert f"live {SESSION} 0" in done.stdout, done.stdout
        assert "boxscan ok" in done.stdout


def test_probe_is_accepted_with_no_argument(box: dict) -> None:
    """The cluster watcher's first call of every pass. It is read-only and takes nothing from the
    caller: the registry and the merge train are the installed copy's own paths."""
    entry(box, SESSION)
    done = forced(box, "probe", timeout=180)
    assert done.returncode == 0, done.stderr
    assert "registry_present=yes" in done.stdout
    assert f"===ENTRY {SESSION}.entry" in done.stdout


# --- the reading that stops a restart during a box's own relaunch ------------------------------


def test_live_prints_a_restarting_reading_for_a_box_mid_relaunch(box: dict) -> None:
    """`agentbox restart` hangs omp up and spawns a fresh one in the same container, and in that
    gap the session is dead by every other reading. At 10:10Z on 2026-11-05 a watcher pass read
    exactly that and asked for a restart of a session being restarted in place; only
    `agentbox new`'s own duplicate guard stopped there being two of it."""
    marker = box["markers"] / f"restarting-{SESSION}"
    marker.write_text(f"session={SESSION}\nbox=agentbox-1\nat={int(time.time()) - 7}\n")
    done = forced(box, f"live {SESSION}", timeout=180)
    assert done.returncode == 0, done.stderr
    line = [l for l in done.stdout.splitlines() if l.startswith("restarting ")]
    assert len(line) == 1, done.stdout
    _, got_id, age = line[0].split()
    assert got_id == SESSION
    assert 5 <= int(age) <= 60, line[0]


def test_live_prints_no_restarting_reading_without_a_marker(box: dict) -> None:
    done = forced(box, f"live {SESSION}", timeout=180)
    assert done.returncode == 0, done.stderr
    assert "restarting " not in done.stdout


def test_a_marker_with_no_at_line_is_aged_from_its_mtime(box: dict) -> None:
    """A marker half-written, or written by an older runner, still reads as a marker: its age is
    the file's own, so the reading degrades to "a relaunch started about now" and never to
    "no relaunch"."""
    marker = box["markers"] / f"restarting-{SESSION}"
    marker.write_text("box=agentbox-1\n")
    done = forced(box, f"live {SESSION}", timeout=180)
    line = [l for l in done.stdout.splitlines() if l.startswith("restarting ")]
    assert len(line) == 1, done.stdout
    assert int(line[0].split()[2]) < 60
