"""chief_dashboard.sources and the one request of its own standup.py sends: each runs the dotfiles'
scripts/agent-private-guard with the bearer it is about to send. A bearer that lists a private project,
or a guard that cannot run or fails, stops the command with exit 2 before its first request; the
standup thread records the refusal as the room's error and reads again; with a public bearer the
requests are those of a run with no private project.

The stand-ins are scripts/tests' private_project_stubs.py (the flag object and Dispatch, whose whoami
the guard asks) and gce_metadata_stub.py (the token the flag is read with).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(PACKAGE.parent / "scripts" / "tests"))

from chief_dashboard import sources, standup  # noqa: E402 - found through the path above
from gce_metadata_stub import MetadataStub  # noqa: E402
from private_project_stubs import (  # noqa: E402
    FLAG_OFF,
    FLAG_ON,
    PRIV_TOKEN,
    PUB_TOKEN,
    WHOAMI,
    WHOAMI_PRIV,
    WHOAMI_PUB,
    DispatchStub,
    FlagObject,
    flag_env,
)

PROJECTS = [{"key": "PUB"}]


class World:
    def __init__(self, tmp: Path) -> None:
        self.metadata = MetadataStub.start()
        self.flag = FlagObject(FLAG_ON)
        self.dispatch = DispatchStub({PRIV_TOKEN: WHOAMI_PRIV, PUB_TOKEN: WHOAMI_PUB},
                                     {"/api/v1/projects": PROJECTS,
                                      "/api/v1/issues?label=stream&open=true": [],
                                      f"/api/v1/issues/{standup.ISSUE}/messages": {"message": {"id": "m1"}}})
        self.config = tmp / "envoy.json"
        self.env = {**flag_env(self.flag, self.metadata.host, str(tmp / "cache")), "ENVOY_CONFIG_FILE": str(self.config)}

    def bearer(self, token: str) -> None:
        self.config.write_text(json.dumps({"dispatch": {"serverUrl": self.dispatch.url, "token": token}}))

    def stop(self) -> None:
        for server in (self.metadata, self.flag, self.dispatch):
            server.stop()


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path)
    for name, value in w.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sources, "ENVOY_CONFIG", w.config)
    monkeypatch.setattr(standup, "ENVOY_CONFIG", w.config)
    yield w
    w.stop()


def test_a_priv_bearer_stops_with_exit_2_before_the_first_request(world, capsys):
    world.bearer(PRIV_TOKEN)
    api = sources.Dispatch()
    # The class, not any SystemExit: the server's threads survive a refusal only by catching it by name.
    with pytest.raises(sources.PrivateProjectRefused) as stopped:
        api.projects()
    assert stopped.value.code == 2
    assert "PRIV is private" in capsys.readouterr().err
    with pytest.raises(sources.PrivateProjectRefused):
        api.open("/api/v1/projects")
    assert world.dispatch.others() == []


def test_a_priv_bearer_stops_the_command_with_exit_2(world):
    world.bearer(PRIV_TOKEN)
    done = subprocess.run([sys.executable, "-m", "chief_dashboard", "lead-times", "--all-streams"], cwd=PACKAGE,
                          env={**os.environ, **world.env, "PYTHONPATH": str(PACKAGE)},
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 2, done.stderr
    assert "PRIV is private" in done.stderr
    assert world.dispatch.others() == []


def test_dispatch_check_with_a_priv_bearer_stops_with_exit_2_before_its_first_request(world):
    world.bearer(PRIV_TOKEN)
    # --no-redrive: should the guard ever let the run through, it sends no re-drive and records none.
    done = subprocess.run([sys.executable, "-m", "chief_dashboard", "dispatch-check", "--project", "PUB", "--no-redrive"],
                          cwd=PACKAGE, env={**os.environ, **world.env, "PYTHONPATH": str(PACKAGE)},
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 2, done.stderr
    assert "PRIV is private" in done.stderr
    assert world.dispatch.paths() == [WHOAMI]  # the guard asked; the command asked nothing


def test_a_priv_bearer_stops_standup_before_its_post(world, tmp_path):
    world.bearer(PRIV_TOKEN)
    room = standup.Room.__new__(standup.Room)
    with pytest.raises(sources.PrivateProjectRefused) as stopped:
        room._post_message("a line")
    assert stopped.value.code == 2
    assert world.dispatch.paths("POST") == []


def test_the_standup_read_records_a_refusal_and_reads_again_once_dispatch_allows_it(world, tmp_path, caplog):
    # The standup thread's read catches the refusal by name: a SystemExit past it would end the thread for good.
    path = f"/api/v1/issues/{standup.ISSUE}/messages"
    said = {"id": "s1", "created_at": "2026-11-01T17:00:00Z", "body": "Yesterday we landed the grader fix.",
            "author": {"kind": "session", "id": "01lead", "origin": {"session_title": "VERIFY: lead"}}}
    world.dispatch.routes[path] = [{"message": said, "replies": []}]
    world.bearer(PUB_TOKEN)
    world.dispatch.answers[PUB_TOKEN] = WHOAMI_PRIV
    room = standup.Room(tmp_path / "state", speech=object())
    with caplog.at_level(logging.WARNING, logger=standup.log.name):
        assert room.refresh() is False
    assert "could not read the standup room" in caplog.text
    assert "PRIV is private" in (room.error or "")
    assert room.all_lines() == []
    assert world.dispatch.others() == []
    world.dispatch.answers[PUB_TOKEN] = WHOAMI_PUB
    assert room.refresh() is True
    assert room.error is None
    assert [ln.text for ln in room.all_lines()] == ["Yesterday we landed the grader fix."]
    assert world.dispatch.others() == [path]


def test_a_pub_bearer_makes_the_requests_of_a_run_with_no_private_project(world):
    world.bearer(PUB_TOKEN)
    world.flag.body = FLAG_OFF
    assert sources.Dispatch().projects() == PROJECTS
    assert standup.Room.__new__(standup.Room)._post_message("a line") == {"id": "m1"}
    before = world.dispatch.paths()
    assert WHOAMI not in before
    world.dispatch.calls.clear()
    shutil.rmtree(Path(world.env["XDG_CACHE_HOME"]) / "agent-private", ignore_errors=True)  # the flag's copy
    world.flag.body = FLAG_ON
    assert sources.Dispatch().projects() == PROJECTS
    assert standup.Room.__new__(standup.Room)._post_message("a line") == {"id": "m1"}
    assert world.dispatch.others() == before
    assert world.dispatch.paths().count(WHOAMI) == 2


def test_each_request_asks_again_so_a_token_turned_private_stops_the_next_one(world):
    world.bearer(PUB_TOKEN)
    api = sources.Dispatch()
    assert api.projects() == PROJECTS
    world.dispatch.answers[PUB_TOKEN] = WHOAMI_PRIV
    with pytest.raises(sources.PrivateProjectRefused) as stopped:
        api.projects()
    assert stopped.value.code == 2
    assert world.dispatch.others() == ["/api/v1/projects"]


def test_a_guard_that_cannot_run_or_fails_stops_the_request_with_exit_2(world, tmp_path, monkeypatch, capsys):
    # Flag off and a public bearer, which the guard itself lets through: only the guard's fault refuses.
    world.bearer(PUB_TOKEN)
    world.flag.body = FLAG_OFF
    assert sources.Dispatch().projects() == PROJECTS  # the control: the real guard passes this world
    world.dispatch.calls.clear()
    failing = tmp_path / "failing-guard"
    failing.write_text("import sys\nsys.exit(1)\n")
    for guard, why in ((tmp_path / "missing" / "agent-private-guard", "can't open file"),
                       (failing, "agent-private-guard exited 1")):
        monkeypatch.setattr(sources, "GUARD", guard)
        capsys.readouterr()
        with pytest.raises(sources.PrivateProjectRefused) as stopped:
            sources.Dispatch().projects()
        assert stopped.value.code == 2, guard
        assert why in capsys.readouterr().err, guard
    assert world.dispatch.paths() == []
