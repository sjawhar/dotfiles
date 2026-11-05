"""The refusals of devbox-offbox-watch: the five reasons it does not restart a dead session.

A restarter's refusals are the whole of its safety, and the watch is moving off sami-rocqperf into
a CronJob in the dispatch cluster (dispatch://INFRA-993), where the same five must hold. Each one
is here:

1. a writeback stall on the watched box -- a resume into a stalled box hangs, as 87 did on
   2026-11-04;
2. the restart budget, 3 in 10 minutes and 10 in an hour across every session;
3. a live process that RUNS the session, so a restart can never make a duplicate;
4. a session that died again within 30 minutes of its own restart;
5. a box whose omp is being relaunched in place right now (`agentbox restart`). At 10:10Z on
   2026-11-05 a pass read such a session as dead -- it is, for those seconds -- and asked the host
   to restart it; only `agentbox new`'s own duplicate guard stopped there being two of it. The
   marker expires, because a session nothing may ever restart again is worse than one restarted
   twice.

The watch is driven here with a stand-in for the box: the probe's and the acting half's own output
as text, which is what it reads in a real run. The transport (ssh over stdin on sami-rocqperf, a
forced command from the cluster) is checked in its own test at the end.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import time
from pathlib import Path

import pytest

WATCH_PATH = Path(__file__).resolve().parent.parent / "devbox-offbox-watch"
SESSION = "01a1a7fc-a94f-72f8-9651-bd5ecd335709"
RESUME = f"agentbox new --dir /srv/work -- omp --resume {SESSION}"


def load_watch():
    spec = importlib.util.spec_from_loader(
        "devbox_offbox_watch", importlib.machinery.SourceFileLoader("devbox_offbox_watch", str(WATCH_PATH)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watch = load_watch()


def probe_text(**facts: str) -> str:
    """What devbox-offbox-probe prints for one registry entry with auto_restart=yes."""
    lines = {
        "host": "sami-devbox",
        "now": "2026-11-05T10:10:00Z",
        "pressure_io_full_avg10": "0.1",
        "writeback_d_count": "0",
        "writeback_d_procs": "",
        "registry_present": "yes",
    }
    lines.update(facts)
    text = "".join(f"{k}={v}\n" for k, v in lines.items())
    return text + (f"===ENTRY {SESSION}.entry\n"
                   f"session={SESSION}\ntitle=UTIL\ntmux=agents\ncwd=/srv/work\n"
                   f"resume={RESUME}\nauto_restart=yes\nowner_note=it runs the merge train\n"
                   f"===END\n")


def live_text(*lines: str) -> str:
    """What `devbox-offbox-act live` prints. With no reading but the scan, the session is dead."""
    return "".join(line + "\n" for line in lines) + "boxscan ok 3 containers\n"


class FakeBox:
    """sami-devbox, as far as this watch can tell: canned answers and a record of every ask."""

    def __init__(self, probe: str, live: str) -> None:
        self.probe_text = probe
        self.live_text = live
        self.asks: list[list[str]] = []
        self.restarted: list[str] = []
        self.forced = False
        self.key = ""

    def _done(self, stdout: str, code: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=["ssh"], returncode=code, stdout=stdout, stderr="")

    def ask_probe(self, remote_proc, registry, train, timeout=None):
        self.asks.append(["probe"])
        return self._done(self.probe_text)

    def ask_live(self, ids, fresh, timeout=None):
        self.asks.append(["live", *ids])
        return self._done(self.live_text)

    def ask_restart(self, entry, timeout=None):
        self.asks.append(["restart", entry["session"]])
        self.restarted.append(entry["session"])
        return self._done("window=agents:7 @7\n")

    def ask_await(self, session, seconds, timeout=None):
        self.asks.append(["await", session])
        return self._done("live\n")


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run one pass against a stand-in box and collect every Envoy message it sent."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(watch.Reporter, "send",
                        lambda self, targets, kind, text: sent.append((kind, text)))
    state_path = tmp_path / "state.json"

    def run(live: str, probe: str | None = None, state: dict | None = None,
            extra: list[str] | None = None) -> tuple[FakeBox, list[tuple[str, str]], dict]:
        if state is not None:
            state_path.write_text(json.dumps(state))
        box = FakeBox(probe if probe is not None else probe_text(), live)
        monkeypatch.setattr(watch, "Remote", lambda *a, **k: box)
        code = watch.main(["--state", str(state_path), "--sre", "SRE",
                           "--chief", "CHIEF", *(extra or [])])
        assert code == 0, f"the pass exited {code}"
        return box, sent, json.loads(state_path.read_text())

    return run


# --- the five refusals -------------------------------------------------------------------------


def test_a_writeback_stall_refuses_every_restart(harness) -> None:
    box, sent, _ = harness(live_text(f"live {SESSION} 0"),
                           probe=probe_text(pressure_io_full_avg10="9.4"))
    assert box.restarted == []
    assert any(kind == "refuse-stall" for kind, _ in sent), sent


def test_the_restart_budget_refuses_past_three_in_ten_minutes(harness) -> None:
    now = time.time()
    box, sent, _ = harness(live_text(f"live {SESSION} 0"),
                           state={"restarts": [{"at": now - 60, "session": f"other-{i}"} for i in range(3)]})
    assert box.restarted == []
    kinds = [kind for kind, _ in sent]
    assert "refuse-limit" in kinds, sent


def test_a_live_process_that_runs_the_session_stops_a_restart(harness) -> None:
    """The `proc` reading is both a reason it is alive and the thing that would make a restart a
    duplicate, so the outcome is the same from either side: nothing is restarted."""
    box, sent, _ = harness(live_text(f"proc {SESSION} 4242 bash -lc omp --resume {SESSION}"))
    assert box.restarted == []
    assert not any(kind.startswith("restart") for kind, _ in sent), sent


def test_a_session_that_died_again_within_thirty_minutes_is_not_restarted_again(harness) -> None:
    now = time.time()
    box, sent, _ = harness(live_text(f"live {SESSION} 0"),
                           state={"restarts": [], "sessions": {SESSION: {"last_restart": now - 300}}})
    assert box.restarted == []
    assert any(kind == "refuse-flap" for kind, _ in sent), sent


def test_a_box_mid_relaunch_is_not_restarted(harness) -> None:
    """The fifth refusal. The session is dead by every reading, and it is coming back by itself."""
    box, sent, _ = harness(live_text(f"live {SESSION} 0", f"restarting {SESSION} 12"))
    assert box.restarted == []
    message = [text for kind, text in sent if kind == "refuse-box-restart"]
    assert len(message) == 1, sent
    assert SESSION in message[0]
    assert "12 s into its relaunch" in message[0], message[0]
    assert str(watch.BOX_RESTART_SECONDS) in message[0]


def test_an_expired_relaunch_marker_does_not_hold_a_session_for_ever(harness) -> None:
    """A relaunch that died leaves its marker behind. Past BOX_RESTART_SECONDS the session is
    restarted, because unrestartable for ever is the worse failure."""
    old = watch.BOX_RESTART_SECONDS + 60
    box, sent, _ = harness(live_text(f"live {SESSION} 0", f"restarting {SESSION} {old}"))
    assert box.restarted == [SESSION]
    assert not any(kind == "refuse-box-restart" for kind, _ in sent), sent


def test_the_marker_does_not_make_a_session_look_alive(harness) -> None:
    """A `restarting` line is a refusal, not a liveness reading: the watch must not record the
    session as live and clear the state a real restart needs."""
    _, _, state = harness(live_text(f"live {SESSION} 0", f"restarting {SESSION} 5"))
    assert not state.get("sessions", {}).get(SESSION, {}).get("last_seen_live")


# --- report-only, the shadow log and the two transports ------------------------------------------


def test_report_only_restarts_nothing_and_names_what_it_would_have(harness) -> None:
    box, sent, _ = harness(live_text(f"live {SESSION} 0"), extra=["--report-only"])
    assert box.restarted == []
    message = [text for kind, text in sent if kind == "report-only"]
    assert len(message) == 1 and RESUME in message[0], sent


def test_every_pass_writes_one_shadow_line(harness, tmp_path: Path) -> None:
    """The arming condition is a day of agreement with the watcher on sami-rocqperf, so a pass
    that found nothing to do is evidence too and writes its line like any other."""
    log = tmp_path / "shadow.jsonl"
    harness(live_text(f"holder {SESSION} host 4242"), extra=["--shadow-log", str(log)])
    harness(live_text(f"live {SESSION} 0", f"restarting {SESSION} 9"),
            extra=["--shadow-log", str(log), "--report-only"])
    lines = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(lines) == 2, log.read_text()
    assert lines[0]["live"] == [SESSION] and lines[0]["dead"] == []
    assert lines[1]["dead"] == [SESSION]
    assert lines[1]["restarting"] == {SESSION: 9}
    assert lines[1]["refused"]["box_restart"] and lines[1]["mode"] == "report-only"
    assert lines[1]["reached"] == "judged"


def test_the_forced_command_transport_sends_verbs_and_no_script() -> None:
    """From the cluster every call is a verb and a session id, which the pinned
    devbox-offbox-act on the host parses itself; the resume line is never sent."""
    remote = watch.Remote("sami-devbox", 30, forced=True, key="/etc/offbox/id_ed25519")
    sent: list[tuple[str, str]] = []
    remote._ssh = lambda command, stdin, timeout: sent.append((command, stdin)) or \
        subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")
    remote.ask_probe("/proc", "/registry", "/train")
    remote.ask_live([SESSION], 120)
    remote.ask_restart({"session": SESSION, "tmux": "agents", "title": "UTIL",
                        "cwd": "/srv/work", "resume": RESUME})
    remote.ask_await(SESSION, 45)
    assert [command for command, _ in sent] == [
        "probe", f"live {SESSION}", f"restart {SESSION}", f"await {SESSION}"]
    assert [stdin for _, stdin in sent] == ["", "", "", ""]
    assert RESUME not in " ".join(command for command, _ in sent)


def test_the_stdin_transport_still_ships_the_two_halves() -> None:
    """sami-rocqperf keeps shipping the scripts until the cutover, so the old transport has to go
    on working: the script's own text on stdin, `bash -s --` and the arguments."""
    remote = watch.Remote("sami-devbox", 30)
    sent: list[tuple[str, str]] = []
    remote._ssh = lambda command, stdin, timeout: sent.append((command, stdin)) or \
        subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")
    remote.ask_live([SESSION], 120)
    command, stdin = sent[0]
    assert command == f"bash -s -- live 120 {SESSION}"
    assert stdin.startswith("#!/usr/bin/env bash")
