"""Restart placement uses real tmux on a private socket, never the host workspace.

Creating a loose window instead of splitting the requested one breaks these tests. Panes run
finite commands and exit when the fixture releases them; cleanup sends no signal or tmux kill.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
ACT = SCRIPTS / "devbox-offbox-act"
SESSION = "01a1a7fc-a94f-72f8-9651-bd5ecd335709"
STREAMS = ("chief", "ossv", "linux", "tasks", "grader", "infra", "rocq", "cicd")
spec = importlib.util.spec_from_loader(
    "offbox_windows", importlib.machinery.SourceFileLoader("offbox_windows", str(SCRIPTS / "devbox-offbox-watch")))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


@pytest.fixture
def workspace(tmp_path: Path):
    home = tmp_path / "home"
    shims = home / ".dotfiles/shims"
    shims.mkdir(parents=True)
    socket = tmp_path / "tmux.sock"
    tmux = shims / "tmux"
    tmux.write_text(f"#!/bin/sh\nexec /usr/bin/tmux -S {shlex.quote(str(socket))} -f /dev/null \"$@\"\n")
    tmux.chmod(0o755)
    release = tmp_path / "release"
    wait = f"while [ ! -e {shlex.quote(str(release))} ]; do /bin/sleep 0.05; done"
    env = dict(os.environ, HOME=str(home))
    env.pop("TMUX", None)
    env.pop("AGENTBOX_TMUX_GUARD_SOCK", None)
    registry = tmp_path / "registry"
    registry.mkdir()
    env["DEVBOX_OFFBOX_REGISTRY"] = str(registry)

    def command(*args):
        return subprocess.run([str(tmux), *args], env=env, text=True, capture_output=True, check=True)

    name = "restart-window-test"
    command("new-session", "-d", "-s", name, "-n", "chief", "-x", "300", "-y", "200", wait)
    command("set-option", "-t", name, "automatic-rename", "off")
    command("set-option", "-t", name, "renumber-windows", "off")
    cwd = tmp_path / "work dir"
    cwd.mkdir()
    marker = tmp_path / "resumed"
    resume = f"printf '%s\\n' \"$PWD\" > {shlex.quote(str(marker))}; {wait}; exit"
    yield {"tmux": command, "name": name, "env": env, "registry": registry,
           "cwd": cwd, "marker": marker, "resume": resume, "wait": wait}
    release.touch()
    # Each process observes the release file and exits on its own; never kill a pane/server.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        done = subprocess.run([str(tmux), "has-session", "-t", f"={name}"], env=env,
                              capture_output=True, text=True)
        if done.returncode:
            break
        time.sleep(0.05)
    else:
        pytest.fail("throwaway panes did not exit naturally after release")


def wait_for_resume(box):
    deadline = time.monotonic() + 10
    while not box["marker"].exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert box["marker"].read_text().strip() == str(box["cwd"])


def snapshot(box):
    return box["tmux"]("list-windows", "-t", f"={box['name']}", "-F",
                       "#{window_index} #{window_id} #{window_name}").stdout.splitlines()


def test_restart_splits_the_stream_window_without_changing_the_eight(workspace):
    box = workspace
    for stream in STREAMS[1:]:
        box["tmux"]("new-window", "-d", "-t", f"={box['name']}:", "-n", stream, box["wait"])
    before = snapshot(box)
    remote = watch.Remote("unused-test-host", 1)

    def local_stdin(command, stdin, timeout):
        assert command.startswith("bash -s -- restart ")
        return subprocess.run(shlex.split(command), input=stdin, env=box["env"], text=True,
                              capture_output=True, timeout=timeout)

    remote._ssh = local_stdin
    done = remote.ask_restart({"session": SESSION, "tmux": box["name"] + ":tasks", "title": "ad-hoc name",
                               "cwd": str(box["cwd"]), "resume": box["resume"]})
    assert done.returncode == 0, done.stderr
    wait_for_resume(box)
    assert snapshot(box) == before, done.stdout
    panes = box["tmux"]("list-panes", "-t", f"={box['name']}:=tasks", "-F", "#{pane_id}").stdout.splitlines()
    assert len(panes) == 2, done.stdout
    assert f"window={box['name']}:tasks " in done.stdout
    assert "pane=%" in done.stdout
    print("existing stream: same eight window IDs/names/order; tasks has two panes; cwd preserved")
    print(done.stdout, end="")


def test_forced_restart_uses_chief_when_the_named_stream_is_missing(workspace):
    box = workspace
    before = snapshot(box)
    fields = {"session": SESSION, "title": "ad-hoc name", "tmux": box["name"] + ":infra",
              "cwd": str(box["cwd"]), "resume": box["resume"]}
    (box["registry"] / f"{SESSION}.entry").write_text("".join(f"{k}={v}\n" for k, v in fields.items()))
    done = subprocess.run([str(ACT), "--forced"], env=dict(box["env"], SSH_ORIGINAL_COMMAND=f"restart {SESSION}"),
                          text=True, capture_output=True, timeout=30)
    assert done.returncode == 0, done.stderr
    wait_for_resume(box)
    assert snapshot(box) == before
    panes = box["tmux"]("list-panes", "-t", f"={box['name']}:=chief", "-F", "#{pane_id}").stdout.splitlines()
    assert len(panes) == 2
    assert f"fallback={box['name']}:infra missing; using {box['name']}:chief" in done.stdout
    assert f"window={box['name']}:chief " in done.stdout
    print("missing stream: restarted in chief; no windows created; existing chief ID/name/index unchanged")
    print(done.stdout, end="")


@pytest.mark.parametrize("tmux, expected", [
    ("agents", "chief"),
    ("agents:tasks", "tasks"),
    ("agents:old-title", "chief"),
    ("dev", "chief"),
    ("dev:tasks", "chief"),
    ("agents:", "chief"),
])
def test_restart_placement_uses_only_tmux_and_maps_legacy_dev(tmux, expected):
    entry = {"session": SESSION, "tmux": tmux, "title": "lane", "cwd": "/srv/work", "resume": "true"}
    remote = watch.Remote("unused-test-host", 1)
    sent = []
    remote.run = lambda script, args, timeout: sent.append(args) or subprocess.CompletedProcess(args, 0)
    remote.ask_restart(entry)
    assert sent[0] == ["restart", "agents", expected, "/srv/work", "true"]


def test_restart_refuses_a_missing_tmux_session_without_creating_it(workspace):
    box = workspace
    before = snapshot(box)
    done = subprocess.run([str(ACT), "restart", box["name"] + "-absent", "tasks",
                           str(box["cwd"]), box["resume"]], env=box["env"], text=True,
                          capture_output=True, timeout=30)
    assert done.returncode != 0
    assert "chief" in done.stderr
    assert not box["marker"].exists()
    assert snapshot(box) == before
    sessions = box["tmux"]("list-sessions", "-F", "#{session_name}").stdout.splitlines()
    assert sessions == [box["name"]]
