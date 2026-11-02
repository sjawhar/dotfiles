"""Live agent sessions per tmux window (sessions.py): counted from the processes and tmux, never declared, with the
sessions inside agent boxes attributed through their host window, and shown on each stream's row and in one strip of
the sessions outside the stream windows. No test reads a live /proc, tmux or docker."""

from __future__ import annotations

import json
from pathlib import Path

from test_dashboard import NOW, snapshot

from chief_dashboard import sessions, v2_model, v2_render

DATA = Path(__file__).parent / "data"
OMP = "~/.mise/installs/github-sjawhar-oh-my-pi/18.13.3/bin/omp"
CID = "8f75bcfb73293e38105d140745398b70102c72f10acc1b0ed57742529c38f00d"


def recorded():
    """sami-rocqperf at 2026-11-02 00:18Z, from `chief-dashboard sessions --record`."""
    raw = json.loads((DATA / "sessions-reading.json").read_text())
    return sessions.measure(raw["processes"], raw["panes"], raw["containers"], host="sami-rocqperf"), raw


def test_the_recorded_reading_counts_every_session_once_by_window():
    census, raw = recorded()
    counts = {w: len(ss) for w, ss in census["windows"].items()}
    assert counts == {"chief": 6, "ossv": 14, "linux": 7, "tasks": 13, "grader": 5, "infra": 7, "rocq": 5, "cicd": 1}
    assert census["total"] == 76 == sum(counts.values()) + sessions.ungrouped_count(census)
    # omp's own helpers run the same binary and are not sessions, orphaned or not.
    workers = [p for p in raw["processes"] if any(a.startswith("__omp_worker_") for a in p["argv"][1:2])]
    assert workers
    counted = {s["pid"] for ss in census["windows"].values() for s in ss}
    counted |= {s["pid"] for pl in census["ungrouped"] for s in pl["sessions"]}
    assert len(counted) == 76 and not counted & {p["pid"] for p in workers}
    assert sessions.stream_counts(census) == {"OSSV-142": 14, "LINUX-31": 7, "BET-83": 13, "BET-59": 5,
                                              "INFRA-203": 7, "INFRA-167": 5}


def test_the_recorded_boxes_are_attributed_through_their_host_window():
    census, raw = recorded()
    places = {(pl["kind"], pl["name"]): pl["sessions"] for pl in census["ungrouped"]}
    lanes = places[("window", "ossv-lanes")]
    # Each boxed omp runs as the box's shifted uid with no pane; its window is that of its host `docker run`.
    boxed_uids = {p["uid"] for p in raw["processes"] if p["pid"] in {s["pid"] for s in lanes}}
    assert len(lanes) == 13 and all(s["box"].startswith("agentbox-") for s in lanes) and 1000 not in boxed_uids
    assert "🌀 CURL: Revive agents and dispatch lane leads" in {s["title"] for s in lanes}
    assert [s["box"] for s in places[("tmux", "lane-curl-decide")]] == ["agentbox-910ba59656e3"]
    assert not any(kind == "box" for kind, _ in places)  # no box was left without its window
    assert [len(places[("tmux", g)]) for g in ("gate-1423-cross", "gate-1423-merge")] == [1, 1]
    assert all(s["headless"] for s in places[("none", "")]) and len(places[("none", "")]) == 2
    assert census["idle_windows"] == ["w-h-rw-sta-4"]


def proc(pid, ppid, argv, uid=1000, cgroup=""):
    return {"pid": pid, "ppid": ppid, "uid": uid, "argv": argv, "cgroup": cgroup}


def pane(pid, window, tmux="agents", title="π ⠋ 🐓 ROCQ: rocq speedup", pane_id=None):
    return {"pane": pane_id or f"%{pid}", "pid": pid, "tmux": tmux, "window": window, "title": title}


def test_what_counts_as_a_session():
    procs = [
        proc(10, 1, ["-bash"]),
        proc(11, 10, ["bash", "~/.dotfiles/shims/omp", "--resume", "01a13dca-e979-7264-af32-f654ef57a6f5"]),
        proc(12, 11, [OMP, "--resume", "01a13dca-e979-7264-af32-f654ef57a6f5"]),
        proc(13, 12, [OMP, "__omp_worker_daemon_broker"]),
        proc(14, 12, ["omp", "-p", "a subagent of 12"]),
        proc(20, 1, ["-bash"]),
        proc(21, 20, ["omp", "--resume", "01a13c57-d826-7242-80fe-5f0670188b11", "."]),  # argv[0] is a bare omp
        proc(30, 1, [OMP, "__omp_worker_mnemopi_embed_server"]),  # an orphaned helper
        proc(40, 1, ["python3", "omp-memory-watch"]),
    ]
    # A grouped session lists each pane once per member session: still one pane.
    panes = [pane(10, "rocq"), pane(10, "rocq", tmux="agents"), pane(20, "infra", title="π > 🤖 INFRA: agent infra")]
    census = sessions.measure(procs, panes, {}, host="h")
    assert census["total"] == 2
    assert [(s["pid"], s["title"], s["session"]) for s in census["windows"]["rocq"]] == [
        (12, "🐓 ROCQ: rocq speedup", "01a13dca-e979-7264-af32-f654ef57a6f5")]
    assert [s["title"] for s in census["windows"]["infra"]] == ["🤖 INFRA: agent infra"]


def test_ungrouped_places_are_named_by_window_tmux_session_or_box():
    procs = [
        proc(10, 1, ["-bash"]), proc(11, 10, [OMP]),
        proc(20, 1, ["bash"]), proc(21, 20, [OMP]),
        proc(30, 1, ["/usr/bin/containerd-shim-runc-v2", "-namespace", "moby", "-id", CID], uid=0),
        proc(31, 30, ["/sbin/docker-init", "--", "bun"], uid=165536),
        proc(32, 31, [OMP, "--resume", "01a18c6b-c125-7047-9793-c219add49cb5", "."], uid=166536,
             cgroup=f"0::/user.slice/user-1000.slice/user-1000-agentbox.slice/docker-{CID}.scope/init.scope"),
        proc(40, 1, [OMP, "-p", "review"]),
        proc(50, 1, ["bash"]),  # a window with no agent
    ]
    panes = [pane(10, "w-curl-asan", title="π ⠸ 🌀 CURL: lanes"), pane(20, "bash", tmux="gm-1189", title="h"),
             pane(50, "w-empty")]
    census = sessions.measure(procs, panes, {}, host="h")
    places = [(pl["kind"], pl["name"], [sessions.label(s) for s in pl["sessions"]]) for pl in census["ungrouped"]]
    assert places == [
        ("window", "w-curl-asan", ["🌀 CURL: lanes"]),
        ("tmux", "gm-1189", ["pid 21"]),
        # docker could not name the container: still counted, under its id
        ("box", f"container {CID[:12]}", ["session 01a18c6b-c125-7047-9793-c219add49cb5"]),
        ("none", "", ["headless run (omp -p)"]),
    ]
    assert census["total"] == 4 and census["idle_windows"] == ["w-empty"]
    assert all(not ss for ss in census["windows"].values())


def test_a_box_takes_the_window_of_its_host_docker_run():
    procs = [
        proc(10, 1, ["-bash"]),
        proc(11, 10, ["bash", "agentbox", "new", "--", "omp"]),
        proc(12, 11, ["docker", "run", "--rm", "--init", "--name", "agentbox-7abef3ca6089", "-it", "img"]),
        proc(30, 1, ["/usr/bin/containerd-shim-runc-v2", "-namespace", "moby", "-id", CID], uid=0),
        proc(31, 30, ["/sbin/docker-init", "--", "bun"], uid=165536),
        proc(32, 31, ["bun", "agentbox-run.ts", "omp"], uid=166536),
        # the cgroup unreadable: the runtime shim above it names the container
        proc(33, 32, [OMP, "--resume", "01a18c6b-c125-7047-9793-c219add49cb5", "."], uid=166536),
    ]
    census = sessions.measure(procs, [pane(10, "ossv", title="π ⠙ 🐝 FUZZ: harness")],
                              {CID: "agentbox-7abef3ca6089"}, host="h")
    assert [(s["pid"], s["box"], s["title"]) for s in census["windows"]["ossv"]] == [
        (33, "agentbox-7abef3ca6089", "🐝 FUZZ: harness")]
    assert census["total"] == 1 and census["ungrouped"] == []


def test_the_recording_keeps_no_prompt_path_or_environment(tmp_path):
    procs = [
        proc(10, 1, ["-bash"]),
        proc(11, 10, ["docker", "run", "-e", "TOKEN=secret", "-v", "/home/x:/home/x", "--name", "agentbox-1", "i"]),
        proc(12, 10, [OMP, "-p", "--model", "m", "review the secret plan"]),
        proc(13, 1, ["sleep", "9"]),
    ]
    raw = sessions.record(procs, [pane(10, "w-1")], {})
    argv = {p["pid"]: p["argv"] for p in raw["processes"]}
    assert argv == {10: ["-bash"], 11: ["docker", "run", "--name", "agentbox-1"], 12: [OMP, "-p", "--model"]}


def test_each_stream_row_carries_its_count_and_the_strip_names_the_windows_outside():
    census, _ = recorded()
    snap = snapshot()
    snap["sessions"] = census
    page = v2_model.build(snap, NOW)
    v = next(x for x in page.streams if x.st.issue == "BET-83")
    assert (v.sessions, v.windows) == (13, ["tasks"])
    head = v2_render.stream_section(v, page, [])
    head = head[head.index('<div class="sh-right">'):head.index("</div></div>")]
    assert '<a class="sess" href="#sessions"' in head and ">13 sessions in tasks</a>" in head
    top = v2_render.top_line(page)
    assert '<span class="n">76</span> live <span class="n bad">18</span> outside a stream window' in top
    strip = v2_render.sessions_strip(page)
    assert "Live agent sessions (76)" in strip and "tasks <b>13</b>" in strip and "cicd <b>1</b> (no stream row)" in strip
    assert "<b>window ossv-lanes</b> <span class=\"bv\">13</span>" in strip
    assert "<b>tmux session gate-1423-cross</b>" in strip and "Windows with no agent: w-h-rw-sta-4" in strip
    assert 'id="sessions"' in v2_render.body(page, {})


def test_a_stream_without_a_window_and_an_unmeasured_read_say_so():
    snap = snapshot()
    page = v2_model.build(snap, NOW)
    v = next(x for x in page.streams if x.st.issue == "BET-83")
    assert v.sessions is None and "sessions in tasks not measured" in v2_render.stream_section(v, page, [])
    assert "not measured" in v2_render.sessions_strip(page) and "not measured" in v2_render.top_line(page)
    v.windows = []
    assert "no tmux window of its own" in v2_render.stream_sessions(v)
