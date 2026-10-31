"""Tests on a small synthetic snapshot (a development fixture: it lives only here, never on the served page)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chief_dashboard import model, render

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def h(hours: float) -> str:
    return iso(NOW - timedelta(hours=hours))


def board(**over) -> dict:
    b = {
        "schema": "stream-scoreboard/1.1",
        "stream": "task-making",
        "name": "Task making",
        "project": "BET",
        "lead": {"title": "TASKCOACH", "session": "s-1"},
        "issue": "BET-83",
        "headline": "Two types are usable; the third waits on its controls.",
        "updated_at": h(1),
        "scope_issues": ["BET-5"],
        "numbers": [
            {
                "id": "usable",
                "name": "Usable task types (passed QA)",
                "role": "main",
                "unit": "types",
                "better": "higher",
                "target": {"value": 4, "by": "2026-10-31"},
                "values": [{"at": h(48), "value": 0}, {"at": h(2), "value": 2}],
            },
            {
                "id": "fast",
                "name": "Bracket eval share",
                "role": "fast",
                "unit": "share",
                "better": "higher",
                "values": [{"at": h(3), "value": 0.22, "n": 32}],
            },
            {
                "id": "cost",
                "name": "Tokens per usable task",
                "role": "guard",
                "guards": "usable",
                "unit": "M tokens",
                "better": "lower",
                "values": [{"at": h(3), "value": 300}],
            },
        ],
        "bottleneck": {"text": "Sets rarely bracket the band", "evidence": "1 of 8 sets", "since": "2026-10-23"},
        "experiments": [
            {"id": "e1", "change": "bracket skill", "number": "fast", "started": h(5), "ended": h(2),
             "before": 0.22, "after": 1.0, "verdict": "kept"},
        ],
        "needs_sami": [],
    }
    b.update(over)
    return b


def well_formed(b: dict) -> dict:
    for n in b["numbers"]:
        for v in n["values"]:
            v.setdefault("source", "fixture")
    for x in b["experiments"]:
        x.setdefault("hypothesis", "fixture")
    return b


def snapshot(boards: list[dict] | None = None, asks: list[dict] | None = None) -> dict:
    versions = [{"number": i + 1, "created_at": h(30 - 10 * i), "data": b} for i, b in enumerate(boards or [board()])]
    return {
        "read_at": iso(NOW),
        "errors": [],
        "dispatch_web": "https://dispatch.example",
        "stream_issues": [
            {"key": "BET-83", "title": "Task making stream: time and cost", "project": "BET", "claim": None},
            {"key": "OSSV-142", "title": "OSS Verify stream: turnaround", "project": "OSSV",
             "claim": {"kind": "session", "id": "s-2", "origin": {"session_title": "OSS lead"}}},
            {"key": "INFRA-9", "title": "Unranked stream: x", "project": "INFRA", "claim": None},
        ],
        # Overrides only: rank and attention.
        "registry": {
            "streams": [
                {"slug": "task-making", "rank": 1, "attention": True, "issue": "BET-83"},
                {"slug": "oss-verify", "name": "OSS Verify", "rank": 2, "attention": True, "issue": "OSSV-142"},
            ]
        },
        "scoreboards": {"BET-83": {"versions": versions}, "OSSV-142": {"versions": []}, "INFRA-9": {"versions": []}},
        "asks": asks or [],
        "ask_issues": {
            "BET-5": {"assignee": "sami@theorem.dev", "title": "Lemma list"},
            "OSSV-1": {"assignee": "sami@theorem.dev", "title": "OSS"},
            "BET-61": {"assignee": "jason@theorem.dev", "title": "Jason's"},
        },
        "agents": {"s-1": {"last_activity": h(0.2)}},
    }


def ask(aid: str, key: str, project: str, hours: float, question: str, waiting_on="human", urgency="med") -> dict:
    return {
        "id": aid,
        "ref": f"/issues/{key}/asks/{aid}",
        "question": question,
        "kind": "question",
        "urgency": urgency,
        "created_at": h(hours),
        "owner": {"issue": {"key": key}},
        "waiting_on": waiting_on,
        "project": project,
    }


# ------------------------------------------------------------------------------------------------ status rules


def num(values, target=4, better="higher", by="2026-10-31", role="main"):
    n = {"id": "x", "name": "X", "role": role, "unit": "u", "better": better, "values": values}
    if target is not None:
        n["target"] = {"value": target, "by": by}
    return model.number_view(n, NOW)


def test_met_when_latest_meets_target():
    assert num([{"at": h(48), "value": 1}, {"at": h(1), "value": 5}]).status == model.MET
    assert num([{"at": h(48), "value": 6}, {"at": h(1), "value": 3}], target=4, better="lower").status == model.MET


def test_on_track_and_behind_by_straight_line():
    rising = [{"at": h(72), "value": 0}, {"at": h(24), "value": 2}]  # +1/day, 7.5 more days to the 31st
    assert num(rising).status == model.ON_TRACK
    flat = [{"at": h(72), "value": 1}, {"at": h(24), "value": 1.1}]
    n = num(flat)
    assert n.status == model.BEHIND and n.projection is not None and n.projection < 4


def test_baseline_only_with_one_time_even_with_two_values():
    same = [{"at": h(3), "value": 6695}, {"at": h(3), "value": 7068}]
    assert num(same, target=707, better="lower").status == model.BASELINE


def test_not_measured_and_no_target_and_deadline_passed():
    assert num([]).status == model.NOT_MEASURED
    assert num([{"at": h(1), "value": 1}], target=None).status == model.NO_TARGET
    assert num([{"at": h(1), "value": 1}], by="2026-10-01").status == model.BEHIND


# ------------------------------------------------------------------------------------------------- loop health


def task_making(streams: list[model.Stream]) -> model.Stream:
    return next(s for s in streams if s.slug == "task-making")


def flags_of(b: dict) -> list[str]:
    st = task_making(model.build_streams(snapshot([b]), NOW))
    assert st.board is not None
    return [f.text for f in st.flags]


def test_healthy_loop_has_no_flags():
    assert flags_of(board()) == []


def test_stale_board_stale_fast_and_stalled_loop():
    b = board(updated_at=h(30))
    b["numbers"][1]["values"] = [{"at": h(60), "value": 0.2}]
    b["experiments"][0].update(started=h(100), ended=h(90))
    texts = flags_of(b)
    assert any("Scoreboard stale" in t for t in texts)
    assert any("not measured in 48 h" in t for t in texts)
    assert any("Loop stalled" in t for t in texts)


def test_fast_never_measured_and_main_without_target():
    b = board()
    b["numbers"][1]["values"] = []
    del b["numbers"][0]["target"]
    texts = flags_of(b)
    assert any("1 fast number never measured" in t for t in texts)
    assert any("without a target" in t for t in texts)


def test_version_1_1_fields_used_when_present():
    b = board()
    b["numbers"][1]["validation_status"] = "failed"
    b["experiments"].append(
        {"id": "e2", "change": "c", "number": "fast", "started": h(4), "ended": h(1), "before": 1, "after": 0.5,
         "verdict": "reverted", "designs_tried": 1, "why_stopped": "no time"}
    )
    texts = flags_of(b)
    assert any("validation failed" in t for t in texts)
    assert any("reverted after 1 design" in t for t in texts)


def test_stream_without_scoreboard_shows_plainly():
    page = model.build(snapshot(), NOW, NOW - timedelta(hours=24), "24 h")
    oss = next(s for s in page.streams if s.slug == "oss-verify")
    assert oss.board is None and oss.worst == model.NO_BOARD and not oss.mains
    assert oss.name == "OSS Verify" and oss.lead_session == "s-2" and oss.lead_title == "OSS lead"  # from the claim
    html = render.body(page, "https://dispatch.example", {}, "24h", False, None, False)
    assert "No scoreboard yet. OSSV-142 has the label <i>stream</i> but no scoreboard.json" in html


def test_streams_are_discovered_and_the_overrides_only_rank():
    streams = model.build_streams(snapshot(), NOW)
    assert [s.issue for s in streams] == ["OSSV-142", "BET-83", "INFRA-9"]  # no board first; unranked last
    tm = task_making(streams)
    assert (tm.name, tm.lead_title, tm.lead_session, tm.rank, tm.attention) == ("Task making", "TASKCOACH", "s-1", 1, True)
    unranked = streams[-1]
    assert (unranked.name, unranked.rank, unranked.attention) == ("Unranked stream", model.UNRANKED, False)


def test_glance_row_shows_headline_first_main_number_with_progress_and_change():
    b = board()
    b["numbers"].insert(1, {"id": "other", "name": "Other main", "role": "main", "unit": "u", "better": "higher",
                            "values": [], "missing": "not built"})
    page = model.build(snapshot([b]), NOW, NOW - timedelta(hours=24), "24 h")
    tm = task_making(page.streams)
    assert tm.headline_number.id == "usable" and tm.worst == model.NOT_MEASURED  # first number shown, worse one kept
    html = render.overview(page, "https://dispatch.example", {}, False)
    row = html[html.index('id="s-task-making"'):]
    assert "Two types are usable" in row
    assert "▲ +2 types since" in row and "(better)" in row
    assert 'style="width:50%"' in row  # 0 → 2 of 0 → 4
    assert "1 not measured" in row  # every main number keeps its chip
    assert "Sets rarely bracket the band" in row


def test_check_reports_format_errors_and_the_pages_own_flags():
    from chief_dashboard import check

    b = board()
    del b["headline"]
    b["numbers"][0]["role"] = "boss"
    b["experiments"].append({"id": "e2", "change": "c", "hypothesis": "h", "number": "nope", "started": h(1),
                             "ended": None, "before": None, "after": None, "verdict": "running"})
    errors, lines = check.report(b, NOW)
    assert any("role must be one of" in x for x in errors)
    assert any("is not the id of a number" in x for x in errors)
    page_flags = [f.text for f in task_making(model.build_streams(snapshot([b]), NOW)).flags]
    for f in page_flags:
        assert any(f in line for line in lines)
    assert any("No headline" in line for line in lines)
    errors, _ = check.report(well_formed(board()), NOW)
    assert errors == []


# ------------------------------------------------------------------------------------ asks waiting on Sami


def test_page_counts_open_asks_per_stream_and_links_the_inbox_instead_of_listing_them():
    asks = [
        ask("a1", "BET-5", "BET", 2, "Should we ship it?"),  # in task-making scope: counted
        ask("a2", "BET-83", "BET", 10, "May I send it?"),  # the stream issue itself: counted
        ask("a3", "BET-61", "BET", 50, "Jason's question?"),  # assigned to Jason: dropped
        ask("a4", "BET-5", "BET", 5, "Did you mean X?", waiting_on="agent"),  # not waiting on him: dropped
        ask("a5", "OSSV-1", "OSSV", 3, "On no stream's issues?"),  # counted nowhere: no project fallback
    ]
    snap = snapshot(asks=asks)
    snap["ask_issues"]["BET-83"] = {"assignee": "sami@theorem.dev", "title": "Task making stream"}
    page = model.build(snap, NOW, NOW - timedelta(hours=24), "24 h")
    by = {a.headline: (a.stream.slug if a.stream else None) for a in page.asks}
    assert by == {"Should we ship it?": "task-making", "May I send it?": "task-making", "On no stream's issues?": None}
    html = render.body(page, "https://dispatch.example", {}, "24h", False, None, False)
    assert 'href="https://dispatch.example/"' in html and "Decisions waiting on you are in your Dispatch Inbox" in html
    assert "2 decisions wait on you" in html  # task making's row, linking to the Inbox
    assert "Decide now" not in html and "Answer in Dispatch" not in html and "Should we ship it?" not in html.split(
        'id="changes"'
    )[0]


def test_an_ask_on_a_child_issue_counts_for_the_stream_its_ancestors_reach():
    """Most asks sit on a child issue (BET-250 under BET-83), not on the stream issue or its scope_issues; matching
    only the ask's own key left all seven streams at "no decisions waiting on you" while the header counted them."""
    asks = [
        ask("c1", "BET-250", "BET", 2, "Reactivate the farm user?"),  # child of a child of the stream issue
        ask("c2", "BET-999", "BET", 3, "Orphan question?"),  # its parent chain reaches no stream
    ]
    snap = snapshot(asks=asks)
    snap["ask_issues"].update({
        "BET-250": {"assignee": "sami@theorem.dev", "title": "Farm identity", "parent": "BET-251"},
        "BET-251": {"assignee": "sami@theorem.dev", "title": "Launch off the box", "parent": "BET-83"},
        "BET-999": {"assignee": "sami@theorem.dev", "title": "Elsewhere", "parent": "BET-998"},
        "BET-998": {"assignee": None, "title": "No stream", "parent": "BET-999"},  # a cycle must not hang
    })
    by = {a.headline: (a.stream.slug if a.stream else None)
          for a in model.asks_waiting_on_sami(snap, model.build_streams(snap, NOW))}
    assert by == {"Reactivate the farm user?": "task-making", "Orphan question?": None}


def test_question_headline_prefers_the_question_sentence():
    assert model.split_question("The email is ready. May I send it? Recommendation: send it.") == (
        "May I send it?",
        "The email is ready.",
    )


# ----------------------------------------------------------------------------------------------------- changes


def test_changes_diff_versions_and_skip_old_history_of_a_first_board():
    old = board()
    new = copy.deepcopy(old)
    new["numbers"][0]["values"].append({"at": h(0.5), "value": 3})
    long_change = "a new idea, described the way a lead really writes it, with the tool, the step and the number it should move"
    new["experiments"].append({"id": "e2", "change": long_change, "number": "fast", "started": h(0.4),
                               "ended": None, "before": 1.0, "after": None, "verdict": "running"})
    snap = snapshot([old, new])  # v1 at 30 h ago, v2 at 20 h ago
    streams = model.build_streams(snap, NOW)
    texts = [c.text for c in model.build_changes(snap, streams, NOW - timedelta(hours=25), NOW)]
    assert any("Usable task types (passed QA) 2 types → 3 types" in t for t in texts)  # the full name, never clipped
    assert f"Experiment started: {long_change}" in texts and not any("…" in t for t in texts)
    # Before the first version existed: one "first scoreboard" line, and only experiments inside the window.
    texts = [c.text for c in model.build_changes(snap, streams, NOW - timedelta(hours=3), NOW)]
    assert not any("First scoreboard" in t for t in texts)
    first = [c.text for c in model.build_changes(snap, streams, NOW - timedelta(hours=40), NOW)]
    assert any("First scoreboard published" in t for t in first)
    assert any("Experiment ended, kept" in t for t in first)
    assert any("Usable task types (passed QA) 0 types → 3 types" in t for t in first)  # measured inside the window


# ------------------------------------------------------------------------------------------------------ server


def test_previous_visit_groups_loads_into_visits(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_STATE_DIR", str(tmp_path))
    import importlib

    from chief_dashboard import server

    importlib.reload(server)
    dash = server.Dashboard()
    t0 = NOW - timedelta(hours=5)
    assert dash.previous_visit("sami", t0, record=True) is None
    assert dash.previous_visit("sami", t0 + timedelta(minutes=10), record=True) is None  # same visit
    later = t0 + timedelta(hours=3)
    assert dash.previous_visit("sami", later, record=True) == t0 + timedelta(minutes=10)
    assert dash.previous_visit("sami", later + timedelta(minutes=5), record=False) == t0 + timedelta(minutes=10)


@pytest.mark.parametrize("value,unit,out", [(6695, "s", "6,695 s"), (0.22, "share", "0.22 share"), (60, "%", "60%")])
def test_fmt_value(value, unit, out):
    assert model.fmt_value(value, unit) == out


def test_projection_of_a_count_never_reads_below_zero():
    falling = [{"at": h(96), "value": 5}, {"at": h(72), "value": 5}, {"at": h(48), "value": 1}, {"at": h(1), "value": 0}]
    n = num(falling, target=10)
    assert n.status == model.BEHIND and n.projection < 0
    assert "falls to 0 before Oct 31" in render.projection_text(n)


def test_unassigned_issue_asks_in_samis_projects_count():
    snap = snapshot(asks=[
        ask("u1", "INFRA-141", "INFRA", 3, "Which session cache?"),  # no assignee, Sami's project: counted
        ask("u2", "GTM-1", "GTM", 3, "Archana's question?"),  # assigned to someone else: dropped
        ask("u3", "ZLIB-9", "ZLIB", 3, "Not Sami's project?"),  # no assignee, not his project: dropped
        ask("u4", "INFRA-141", "INFRA", 3, "Agent's turn?", waiting_on="agent"),  # not waiting on a human
    ])
    snap["ask_issues"].update({
        "INFRA-141": {"assignee": None, "title": "Envoy sends"},
        "GTM-1": {"assignee": "archana@theorem.dev", "title": "Inbound"},
        "ZLIB-9": {"assignee": None, "title": "zlib"},
    })
    asks = model.asks_waiting_on_sami(snap, model.build_streams(snap, NOW))
    assert [a.headline for a in asks] == ["Which session cache?"]


def test_target_not_better_than_the_start_is_flagged_not_met():
    n = num([{"at": h(3), "value": 54.5}], target=168, better="lower")  # OSS Verify's turnaround today
    assert n.status == model.BAD_TARGET and render.progress_bar(n) == ""
    assert num([{"at": h(3), "value": 4}], target=4, better="higher").status == model.BAD_TARGET
    assert num([{"at": h(48), "value": 0}, {"at": h(1), "value": 5}], target=4).status == model.MET  # a real gain
    b = board()
    b["numbers"][0]["target"] = {"value": 0, "by": "2026-10-31"}  # higher is better; the first value is 0
    texts = flags_of(b)
    assert any(t.startswith("Target is not better than the start") for t in texts)
    from chief_dashboard import check

    _, lines = check.report(b, NOW)
    assert any("Target is not better than the start" in line for line in lines)


@pytest.mark.parametrize(
    "unit,out",
    [("trees of 4", "1 tree of 4"), ("merges/day", "1 merge/day"), ("days", "1 day"), ("s", "1 s"), ("h", "1 h")],
)
def test_unit_singular_at_one(unit, out):
    assert model.fmt_value(1, unit) == out
    assert model.fmt_value(2, "trees of 4") == "2 trees of 4"


# ----------------------------------------------------------------------------------------------------- videos

THROWAWAY = Path(__file__).parent / "data" / "throwaway.mp4"  # a 2.5 s test pattern made with ffmpeg; never served


def test_mp4_duration_and_ranges():
    from chief_dashboard import videos

    assert videos.mp4_duration(THROWAWAY) == pytest.approx(2.5, abs=0.1)
    assert videos.parse_range("bytes=0-1", 100) == (0, 1)
    assert videos.parse_range("bytes=90-", 100) == (90, 99)
    assert videos.parse_range("bytes=-10", 100) == (90, 99)
    assert videos.parse_range("bytes=0-500", 100) == (0, 99)
    assert videos.parse_range("bytes=200-", 100) is None
    assert videos.parse_range(None, 100) is None


def test_titles_from_script_front_matter_or_file_name():
    from chief_dashboard import videos

    assert videos.front_matter_title('---\ntitle: "How Nickolai works"\nlength: 4 min\n---\n# x') == "How Nickolai works"
    assert videos.front_matter_title("# no front matter") is None
    assert videos.title_from_name("explainer-lps-machcsl", "Verified grader") == "LPS machcsl"
    assert videos.title_from_name("explainer-nickolai-workflow", "Verified Linux") == "Nickolai workflow"
    assert videos.title_from_name("explainer", "Task making") == "Task making explainer"


class FakeDispatch:
    def __init__(self, files: dict[str, bytes]):
        self.files, self.opened = files, []

    def open(self, path, timeout=600):
        import io

        self.opened.append(path)
        return io.BytesIO(self.files[path])

    def get(self, path):
        return {"markdown": self.files[path].decode()}


def test_videos_are_downloaded_once_with_title_length_and_served_by_sha(tmp_path):
    import hashlib

    from chief_dashboard import videos

    data = THROWAWAY.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    base = "/api/v1/issues/BET-83/artifacts"
    api = FakeDispatch({
        f"{base}/explainer-mp4/versions/2": data,
        f"{base}/explainer-aengus-workflow-mp4/versions/1": data,
        f"{base}/explainer-script-md/text": b"---\ntitle: Task making in four minutes\n---\n",
    })
    arts = [
        {"slug": "explainer-mp4", "name": "explainer.mp4", "kind": "file",
         "versions": [{"number": 1, "created_at": h(5)}, {"number": 2, "created_at": h(1), "sha256": sha}]},
        {"slug": "explainer-aengus-workflow-mp4", "name": "explainer-aengus-workflow.mp4", "kind": "file",
         "versions": [{"number": 1, "created_at": h(2)}]},
        {"slug": "explainer-script-md", "name": "explainer-script.md", "kind": "doc", "versions": [{"number": 1}]},
        {"slug": "scoreboard-json", "name": "scoreboard.json", "kind": "file", "versions": [{"number": 1}]},
    ]
    store = videos.VideoStore(tmp_path)
    errors: list[str] = []
    rows = store.explainers(api, "BET-83", arts, errors)
    assert errors == []
    assert [(r["stem"], r.get("title"), r["sha"]) for r in rows] == [
        ("explainer", "Task making in four minutes", sha),
        ("explainer-aengus-workflow", None, sha),
    ]
    assert rows[0]["seconds"] == pytest.approx(2.5, abs=0.1) and store.path(sha).read_bytes() == data
    store.explainers(api, "BET-83", arts, errors)
    assert len([p for p in api.opened if p.endswith("versions/2")]) == 1  # a known sha is not downloaded again
    assert store.path("../../etc/passwd") is None


def test_each_stream_section_offers_only_its_own_videos_newest_first_each_once():
    """The dropdown beside a stream's KPI table is that stream's videos, newest first, one option per sha256 — not
    another stream's. A stream with none says so; nothing links to a separate videos page."""
    import re

    from chief_dashboard import v2_model, v2_render

    def video(stem: str, sha: str, hours: float, title: str) -> dict:
        return {"stem": stem, "name": f"{stem}.mp4", "sha": sha, "seconds": 90.0, "title": title, "uploaded": h(hours)}

    standup = video("explainer-standup", "a" * 64, 2, "Standup: task making")
    snap = snapshot()
    snap["explainers"] = {
        "BET-83": [
            video("explainer-skills", "b" * 64, 20, "What we learned about writing skills"),
            standup,
            dict(standup, stem="explainer-standup-copy", name="explainer-standup-copy.mp4"),  # same bytes: one video
        ],
        "OSSV-142": [video("explainer-fleet", "c" * 64, 3, "Fleet monitoring")],
    }
    page = v2_model.build(snap, NOW)
    sections = {
        v.st.issue: v2_render.stream_section(v, page, snap["explainers"].get(v.st.issue) or [])
        for v in page.streams
    }
    titles = {k: [t.split(" · ")[1] for t in re.findall(r"<option [^>]*>([^<]+)</option>", s)] for k, s in sections.items()}
    assert titles == {
        "BET-83": ["Standup: task making", "What we learned about writing skills"],
        "OSSV-142": ["Fleet monitoring"],
        "INFRA-9": [],
    }
    assert f'<video id="v-task-making" class="vplay" controls playsinline preload="metadata" src="/video/{"a" * 64}.mp4">' in sections["BET-83"]
    assert "No explainer video for this stream yet." in sections["INFRA-9"]
    assert "/explainers" not in "".join(sections.values())


def test_a_stream_that_falls_behind_moves_up():
    ok = model.build(snapshot(), NOW, NOW - timedelta(hours=24), "24 h")
    behind = board()
    behind["numbers"][0]["values"] = [{"at": h(48), "value": 0}, {"at": h(2), "value": 0}]  # flat: now behind
    late = model.build(snapshot([behind]), NOW, NOW - timedelta(hours=24), "24 h")
    assert [s.issue for s in ok.streams] == ["OSSV-142", "BET-83", "INFRA-9"]
    assert [s.issue for s in late.streams] == ["BET-83", "OSSV-142", "INFRA-9"]


def test_lead_waits_are_accepted_and_unknown_kinds_only_warn():
    from chief_dashboard import check

    b = well_formed(board(waits=[{"on": "lead", "count": 2, "oldest_hours": 3}, {"on": "review", "count": 1}]))
    errors, lines = check.report(b, NOW)
    assert errors == []
    assert any("waits[1] is on 'review'" in line for line in lines)
    assert not any("waits[0]" in line for line in lines)
    errors, _ = check.report(well_formed(board(waits=[{"on": "lead"}])), NOW)
    assert errors and "count" in errors[0]
    html = render.waits_line(b)
    assert "2 on their lead, the oldest for 3 h" in html and "1 on review" in html
