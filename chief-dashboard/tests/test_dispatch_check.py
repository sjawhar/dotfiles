"""dispatch-check on Dispatch responses: BET-3's recorded issue and events (tests/data/dispatch_check/bet-3.json, read
from Dispatch 2026-10-25, document diffs trimmed) and small issues built in the same shape for each other check."""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chief_dashboard import dispatch_check as dc
from chief_dashboard.sources import Dispatch, SourceError

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
LIVE = "01a1live-0000-0000-0000-000000000000"
DEAD = "01a1dead-0000-0000-0000-000000000000"
SAMI = {"kind": "user", "id": "sami@theorem.dev"}
RECORDED = json.loads((Path(__file__).parent / "data" / "dispatch_check" / "bet-3.json").read_text())


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def ago(hours: float) -> str:
    return iso(NOW - timedelta(hours=hours))


def session(sid: str = LIVE, title: str = "LEAD: the lead") -> dict:
    return {"kind": "session", "id": sid, "origin": {"session_title": title}}


def ev(type_: str, who: dict, hours: float, **payload) -> dict:
    return {"type": type_, "actor": who, "created_at": ago(hours), "payload": payload}


def issue(key: str, status: str = "in_progress", claim: dict | None = None, events=(), artifacts=(), links=(),
          parent: str | None = None, labels=(), assignee: str = "sami@theorem.dev") -> dict:
    return {"key": key, "title": f"title of {key}", "status": status, "last_seq": len(events),
            "claim": {"actor": claim, "at": ago(48)} if claim else None, "events": list(events),
            "artifacts": list(artifacts), "external_links": list(links), "parent": parent, "labels": list(labels),
            "assignee": assignee}


class FakeDispatch(Dispatch):
    """Serves the routes dispatch_check reads from in-memory issues; records every path it was asked for."""

    def __init__(self, issues: list[dict], asks=(), live=(LIVE,), texts=None, streams=(), fail=(), nodes=None,
                 refs=None, boards=None) -> None:
        self.server = "https://dispatch.test"
        self.issues = {i["key"]: i for i in issues}
        self.asks = list(asks)
        self.live = list(live)
        self.texts = texts or {}
        self.streams = list(streams)
        self.fail = set(fail)
        self.nodes = nodes or {}  # comment, ask and message reads by id: {"comment": {...}, "replies": [...]}
        self.refs = refs or {}  # dispatch:// ref: the edges citing it
        self.boards = boards or {}  # stream issue key: its latest scoreboard.json
        self.paths: list[str] = []

    def get(self, path: str):
        self.paths.append(path)
        url = urllib.parse.urlparse(path)
        q = dict(urllib.parse.parse_qsl(url.query))
        parts = url.path.removeprefix("/api/v1/").split("/")
        if parts[0] in self.fail or q.get("project") in self.fail:
            raise SourceError(f"GET {path}: HTTP 500")
        if parts == ["projects"]:
            return [{"key": k, "name": k} for k in ("BET", "INFRA", "OSSV")]
        if parts == ["agents"]:
            return [{"session_id": s, "title": f"live {s[:8]}"} for s in self.live]
        if parts == ["asks", "open"]:
            return {"asks": [a for a in self.asks if a["owner"]["issue"]["key"].startswith(q["project"] + "-")]}
        if parts[0] == "artifacts":
            return {"markdown": self.texts.get(parts[1], "")}
        if parts == ["references"]:
            return {"node": {}, "edges": self.refs.get(q["to"], [])}
        if parts[0] in ("comments", "asks", "messages"):
            return self.nodes.get(parts[1], {parts[0][:-1]: {}, "replies": []})
        if parts == ["issues"]:
            if q.get("label") == "stream":
                return self.streams
            return [{k: v for k, v in i.items() if k != "events"} for i in self.issues.values()
                    if i["key"].startswith(q["project"] + "-")]
        if parts[1] not in self.issues:
            raise SourceError(f"GET {path}: HTTP 404")
        i = self.issues[parts[1]]
        if len(parts) == 3:
            return [e for n, e in enumerate(i["events"], 1) if n > int(q["after"])][: int(q["limit"])]
        if len(parts) == 6:  # a scoreboard.json version
            return self.boards[parts[1]]
        out = {k: v for k, v in i.items() if k != "events"}
        if i["key"] in self.boards:
            out["artifacts"] = [*out["artifacts"], {"slug": "scoreboard-json", "id": "sb", "versions": [{"number": 3}]}]
        return out


def no_prs(keys, linked):
    return {}


def check(api: FakeDispatch, scope: dc.Scope, github=no_prs, now: datetime = NOW, owner: str | None = None,
          streams: dict | None = None) -> list[str]:
    return dc.run(api, scope, github, [p["key"] for p in api.projects()], streams or {}, now, owner)


def found(lines: list[str], kind: str) -> list[str]:
    """The findings of KIND, without their owner."""
    return [ln.removeprefix(f"DISPATCH-{kind} ").split(" | owner: ")[0] for ln in lines
            if ln.startswith(f"DISPATCH-{kind} ")]


def owners(lines: list[str]) -> list[str]:
    return [ln.split(" | owner: ")[1] for ln in lines if " | owner: " in ln]


def test_unowned_no_claim_dead_claim_and_extra_issue():
    api = FakeDispatch([
        issue("BET-1"),
        issue("BET-2", claim=session(DEAD, "OLD: gone")),
        issue("BET-3", claim=session()),
        issue("BET-4", status="backlog"),
        issue("INFRA-9", status="backlog"),
    ])
    lines = found(check(api, dc.Scope("BET", issues=["INFRA-9"])), "UNOWNED")
    assert lines == [
        "BET-1 [in_progress] title of BET-1 (no claim)",
        f"BET-2 [in_progress] title of BET-2 (claim by OLD: gone since {ago(48)[:16]}Z: not running)",
        "INFRA-9 [backlog] title of INFRA-9 (no claim)",
    ]


def test_an_issue_assigned_to_another_person_is_theirs_not_unowned():
    # FRAC, 10-25: BET-62 is Jason's (assignee jason@theorem.dev, his own agent wrote it, its spec waits on his ask);
    # his agent holds no claim, so every BET lead got it as unowned each hour.
    api = FakeDispatch([issue("BET-62", assignee="jason@theorem.dev"), issue("BET-63")])
    assert found(check(api, dc.Scope("BET")), "UNOWNED") == ["BET-63 [in_progress] title of BET-63 (no claim)"]


def test_quiet_after_24_hours_without_activity():
    plan = [{"slug": "plan-md", "id": "p"}]
    api = FakeDispatch([
        issue("BET-1", claim=session(), artifacts=plan, events=[ev("comment.created", session(), 30, id="c")]),
        issue("BET-2", claim=session(), artifacts=plan, events=[ev("comment.created", session(), 2, id="c")]),
        issue("BET-3", claim=session(), artifacts=plan, events=[
            ev("issue.created", session(), 50, status="triage"),
            ev("issue.updated", session(), 3, status="in_progress"),  # a status change counts
        ]),
    ], texts={"p": "due 2026-10-30"})
    assert found(check(api, dc.Scope("BET")), "QUIET") == [
        "BET-1 [in_progress] title of BET-1 (claimed by live 01a1live; last comment.created 1d 6h ago)"]


def test_needs_review_waiting_on_an_open_linked_pr_is_not_quiet():
    """A needs_review issue whose linked pull request is still open waits on a reviewer or an upstream maintainer,
    so an hourly QUIET row there only makes its lane answer every hour to say the same thing. BET-123 was flagged
    six times in six hours while rocq-prover/rocq#22607 sat rebased and green."""
    plan = [{"slug": "plan-md", "id": "p"}]
    url = "https://github.com/o/r/pull/22607"
    api = FakeDispatch([
        issue("BET-123", status="needs_review", claim=session(), artifacts=plan, links=[url],
              events=[ev("comment.created", session(), 30, id="c")]),
        issue("BET-124", status="needs_review", claim=session(), artifacts=plan, links=[url],
              events=[ev("comment.created", session(), 30, id="c")]),
    ], texts={"p": "due 2026-10-30"})

    open_pr = {"BET-123": {url: pr(url)}, "BET-124": {url: pr(url, state="MERGED", merged=True)}}
    rows = found(check(api, dc.Scope("BET"), github=lambda keys, linked: open_pr), "QUIET")
    # BET-123 waits on the open pull request. BET-124's is merged, so its silence is its own and it stays quiet.
    assert rows == [
        "BET-124 [needs_review] title of BET-124 (claimed by live 01a1live; last comment.created 1d 6h ago)"]


AUDIT = """# Audit

| \\# | What you asked | Owner | Delivered by (UTC) |
|---|---|---|---|
| 1 | Ship BET-1 | LEAD | 10-24 |
| 2 | Ship BET-2 | LEAD | 10-24 18:00 |
| 3 | Ship the report | LEAD | done 10-20 |
| 4 | Ship BET-1 later | LEAD | 10-30 |
| 5 | Ship with no date | LEAD | when ready |
| 6 | Ship the slides | LEAD | 2026-10-23 |
"""


def test_overdue_rows_of_the_audit_table():
    api = FakeDispatch([
        issue("BET-1", claim=session()),
        issue("BET-2", status="done"),
        issue("BET-142", status="in_progress", claim=session(), artifacts=[{"slug": "audit-md", "id": "a"}]),
    ], texts={"a": AUDIT})
    lines = check(api, dc.Scope("BET", audit=("BET-142", "audit-md")))
    assert found(lines, "OVERDUE") == [
        '#1 Ship BET-1 (owner LEAD; due 10-24 23:59Z, 12h 1m ago; "10-24"; BET-1 in_progress)',
        "#6 Ship the slides (owner LEAD; due 10-23 23:59Z, 1d 12h ago; \"2026-10-23\"; "
        "no issue key: mark the cell 'done' when delivered)",
    ]
    assert lines[-1].endswith("audit dispatch://BET-142/artifact/audit-md: 6 rows, 1 with no date (#5); re-drive off")


def at(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_waiting_on_us_keeps_the_recorded_bet3_answer_until_a_session_cites_the_ask():
    """Recorded: CHIEF opened ask cd767725 on BET-3, Sami answered it 10-24 19:48Z; PIPE then opened another ask and
    commented on BET-3 (00:31Z, 00:33Z), which does not follow this answer; PIPE's spec version of 02:51:55Z cites it."""
    rec = dict(RECORDED["issue"], events=RECORDED["events"], last_seq=len(RECORDED["events"]))
    ask_id = "cd767725-a0e3-4c07-b672-d56e99337e24"
    refs = {r: v["edges"] for r, v in RECORDED["references"].items()}
    parent = issue(rec["parent"], status="done")  # BET-3's parent, read for its stream
    api = FakeDispatch([rec, parent], live=(), refs=refs, nodes={ask_id: RECORDED["ask"]})
    for t in ("2026-10-25T00:30:00Z", "2026-10-25T00:34:00Z", "2026-10-25T02:46:00Z"):
        lines = found(check(api, dc.Scope("BET"), now=at(t)), "WAITING-ON-US")
        assert len(lines) == 1, t
        assert lines[0].startswith('BET-3 answer "The author merges after review Delete that rule immediately" to: ')
        assert lines[0].endswith("no event from the session that asked, no reply in the ask's thread and no session "
                                 "citation since)")
    assert found(check(api, dc.Scope("BET"), now=at("2026-10-25T02:52:00Z")), "WAITING-ON-US") == []
    assert f"/api/v1/asks/{ask_id}" in api.paths


def answered(hours: float, opener: str = DEAD) -> dict:
    return ev("ask.answered", SAMI, hours, id="a1", author={"kind": "session", "id": opener}, question="Merge?",
              answer={"selected": ["Yes"], "text": None})


def test_waiting_on_us_an_answer_is_followed_only_by_its_asker_its_thread_or_a_citation():
    other = session(LIVE, "OTHER: busy")
    asker = session(DEAD, "ASKER: asked")
    cases = {
        "other session acts": ([answered(5), ev("comment.created", other, 4, id="x"),
                                ev("ask.opened", other, 3, id="a2")], {}, {}, True),
        "asker acts": ([answered(5), ev("artifact.version", asker, 4)], {}, {}, False),
        "thread reply": ([answered(5), ev("comment.created", other, 4, id="x", ask_id="a1")], {}, {}, False),
        "session reply read from the ask": ([answered(5)], {"a1": {"ask": {}, "replies": [
            {"id": "r", "author": other, "created_at": ago(4)}]}}, {}, False),
        "cited by a document": ([answered(5)], {}, {"dispatch://BET-1/ask/a1": [
            {"direction": "in", "node": {"kind": "artifact", "id": "d", "ref": "dispatch://INFRA-2/spec"},
             "created_at": ago(4)}]}, False),
        "cited only after now": ([answered(5)], {}, {"dispatch://BET-1/ask/a1": [
            {"direction": "in", "node": {"kind": "artifact", "id": "d"}, "created_at": ago(-1)}]}, True),
    }
    for name, (events, nodes, refs, flagged) in cases.items():
        api = FakeDispatch([issue("BET-1", status="backlog", events=events)], nodes=nodes, refs=refs)
        lines = found(check(api, dc.Scope("BET")), "WAITING-ON-US")
        assert bool(lines) == flagged, name


def test_waiting_on_us_a_comment_is_followed_by_a_session_citation_reply_or_resolution_anywhere():
    ref = "dispatch://INFRA-107/comment/c1"
    events = [ev("comment.created", SAMI, 50, id="c1", body="I already have an agent for my omp fork")]
    cite = lambda kind, nid: {ref: [{"direction": "in", "created_at": ago(40),  # noqa: E731
                                     "node": {"kind": kind, "id": nid, "ref": f"dispatch://INFRA-26/{kind}/{nid}"}}]}
    cases = {
        "nothing": ({}, {}, True),
        # INFRA-107 comment 79972b4e was acted on through INFRA-26 ask 69f0af1d.
        "cited by a session's ask": ({"q": {"ask": {"author": session()}, "replies": []}}, cite("ask", "q"), False),
        "cited by sami's own comment": ({"k": {"comment": {"author": SAMI}, "replies": []}}, cite("comment", "k"),
                                        True),
        "resolved": ({"c1": {"comment": {"resolved_at": ago(10)}, "replies": []}}, {}, False),
        "session reply": ({"c1": {"comment": {}, "replies": [{"author": session(), "created_at": ago(45)}]}}, {},
                          False),
    }
    for name, (nodes, refs, flagged) in cases.items():
        api = FakeDispatch([issue("INFRA-107", status="backlog", events=events)], nodes=nodes, refs=refs)
        lines = found(check(api, dc.Scope("INFRA")), "WAITING-ON-US")
        assert bool(lines) == flagged, name


def test_each_line_names_its_owner_and_owner_filters():
    lead = "01a1lead-0000-0000-0000-000000000000"
    api = FakeDispatch([
        issue("BET-1", status="triage", claim=session()),  # a running claim holder
        issue("BET-2", claim=session(DEAD, "OLD: gone"), parent="BET-5"),  # dead claim: the stream's lead
        issue("BET-5", status="backlog", parent="BET-59"),
        issue("BET-59", status="backlog", labels=["stream"]),
        issue("BET-3", parent="BET-83"),  # a stream with no lead named
        issue("BET-83", status="backlog", labels=["stream"]),
        issue("BET-4"),  # no stream
    ])
    streams = {"BET-59": {"project": "BET", "lead": {"title": "FRAC", "session": lead}},
               "BET-83": {"project": "BET", "lead": None}}
    lines = check(api, dc.Scope("BET"), streams=streams)
    by = {ln.split(" ")[1]: ln.split(" | owner: ")[1] for ln in lines if ln.startswith("DISPATCH-UNOWNED")}
    assert by == {"BET-2": f"stream BET-59 lead FRAC ({lead})",
                  "BET-3": "stream BET-83 (its scoreboard names no lead)", "BET-4": "no owner"}
    assert "DISPATCH-STATUS BET-1 [triage] title of BET-1: work is running (claimed by live 01a1live); should be " \
           f"in_progress | owner: live 01a1live ({LIVE})" in lines
    mine = check(api, dc.Scope("BET"), streams=streams, owner="01a1lead")
    # BET-2's dead claim makes it UNOWNED, QUIET (no activity) and NO-PLAN, all for the stream's lead.
    assert [ln.split(" ")[0] for ln in mine[:-1]] == ["DISPATCH-UNOWNED", "DISPATCH-QUIET", "DISPATCH-NO-PLAN"]
    assert set(owners(mine)) == {f"stream BET-59 lead FRAC ({lead})"}
    assert mine[-1].startswith("DISPATCH-SUMMARY BET: 7 open BET + 0 other issues read; 1 UNOWNED, 1 QUIET, "
                               "0 OVERDUE, 0 WAITING-ON-US, 0 PAST-ACK, 0 NO-EXECUTOR, 0 STATUS, 1 NO-PLAN")
    assert "(owner 01a1lead* only)" in mine[-1]


def test_waiting_on_us_open_asks_and_comments():
    ask = {"id": "q1", "waiting_on": "agent", "age_seconds": 7200, "question": "Which  gate?",
           "ref": "/issues/BET-1/asks/q1", "owner": {"issue": {"key": "BET-1"}}}
    theirs = {"id": "q2", "waiting_on": "human", "age_seconds": 60, "question": "Mine?",
              "ref": "/issues/BET-1/asks/q2", "owner": {"issue": {"key": "BET-1"}}}
    plan = [{"slug": "plan-md", "id": "p"}]
    api = FakeDispatch([issue("BET-1", claim=session(), artifacts=plan, events=[
        ev("comment.created", SAMI, 5, id="c1", body="Why  is this late?"),
        ev("comment.created", SAMI, 4, id="c2", body="Answered one"),
        ev("comment.created", session(), 3, id="c3", reply_to="c2", body="Because."),
        ev("comment.created", SAMI, 4, id="c4", body="Resolved one"),
        ev("comment.resolved", SAMI, 3, id="c4"),
        ev("comment.created", SAMI, 2, id="c5", ask_id="q9", body="On an ask"),
        ev("message.created", SAMI, 1, body="Status?"),
    ])], asks=[ask, theirs], texts={"p": "due 10-30"})
    assert found(check(api, dc.Scope("BET")), "WAITING-ON-US") == [
        "BET-1 ask waiting on an agent for 2h 0m: Which gate? (https://dispatch.test/issues/BET-1/asks/q1)",
        "BET-1 comment by sami 5h 0m ago, unanswered and unresolved: Why is this late? "
        "(https://dispatch.test/issues/BET-1/comments/c1)",
        "BET-1 message: Status? (sami, 1h 0m ago; no agent comment, message or ask since)",
    ]


def test_streams_lead_and_main_number_come_from_the_latest_scoreboard():
    api = FakeDispatch([issue("BET-59", labels=["stream"]), issue("OSSV-142", labels=["stream"])],
                       streams=[{"key": "BET-59", "project": "BET"}, {"key": "OSSV-142", "project": None}],
                       boards={"BET-59": {"lead": {"title": "FRAC", "session": "s-1"},
                                          "numbers": [{"id": "side", "role": "supporting",
                                                       "values": [{"at": "2026-10-27T07:00:00Z", "value": 9}]},
                                                      {"id": "usable-tasks", "role": "main", "name": "Usable tasks",
                                                       "values": [{"at": "2026-10-27T01:35:00Z", "value": 0},
                                                                  {"at": "2026-10-27T04:35:00Z", "value": 0}]}]}})
    assert dc.read_streams(api) == {
        "BET-59": {"project": "BET", "lead": {"title": "FRAC", "session": "s-1"},
                   "main": {"name": "Usable tasks", "at": "2026-10-27T04:35:00Z", "cadence": None}},
        "OSSV-142": {"project": "OSSV", "lead": None, "main": None}}


def test_a_stream_whose_main_number_is_older_than_a_tick_is_flagged_to_its_lead():
    # 2026-10-27: the 07:30Z round printed task making's 01:35Z scoreboard value while the lead's own 07:05Z reading
    # sat in its log, and told the lead to re-read a number it had already read. The scoreboard is what everyone
    # downstream reads, so a stale value there is the stream's state as far as the organization is concerned.
    streams = {"BET-59": {"project": "BET", "lead": {"title": "FRAC", "session": "s-1"},
                          "main": {"name": "Usable tasks", "at": "2026-10-27T01:35:00Z"}},
               "BET-83": {"project": "BET", "lead": None,
                          "main": {"name": "Fresh one", "at": "2026-10-27T07:00:00Z"}},
               "OSSV-142": {"project": "OSSV", "lead": None,
                            "main": {"name": "Another project's", "at": "2026-10-26T01:00:00Z"}}}
    lines = check(FakeDispatch([issue("BET-1", claim=session())]), dc.Scope("BET"), streams=streams,
                  now=datetime(2026, 10, 27, 7, 30, tzinfo=UTC))
    found_lines = found(lines, "STALE-NUMBER")
    assert len(found_lines) == 1
    assert found_lines[0].startswith("BET-59 main number 'Usable tasks' last read 5h")
    assert "2026-10-27T01:35Z" in found_lines[0]
    assert "1 STALE-NUMBER" in lines[-1]


def test_a_main_number_with_a_cadence_is_not_flagged_for_its_age():
    # 2026-10-29: this check told Rocq speed its 4 h old number was stale. The number is a Taiga grade of 10 fixed
    # trees on two images, 3-4 h of farm time, and no image had changed, so a re-reading would have measured noise.
    # Its scoreboard says what produces the next one ("on each grader image or Rocq build change"). A check that
    # reads a value's age and not the condition that should produce a new one cannot tell a real state from its own
    # definition, which is the fault it exists to catch in other people's numbers.
    streams = {"BET-59": {"project": "BET", "lead": {"title": "FRAC", "session": "s-1"},
                          "main": {"name": "Paced number", "at": "2026-10-27T01:35:00Z"}},
               "BET-83": {"project": "BET", "lead": {"title": "SPEEDCOACH", "session": "s-2"},
                          "main": {"name": "Grade wall time", "at": "2026-10-27T01:35:00Z",
                                   "cadence": "on each grader image or Rocq build change"}}}
    lines = check(FakeDispatch([issue("BET-1", claim=session())]), dc.Scope("BET"), streams=streams,
                  now=datetime(2026, 10, 27, 7, 30, tzinfo=UTC))
    found_lines = found(lines, "STALE-NUMBER")
    assert len(found_lines) == 1
    assert found_lines[0].startswith("BET-59 main number 'Paced number'")
    assert not any("Grade wall time" in line for line in found_lines)


def pr(url: str, state="OPEN", merged=False, draft=False, checks="SUCCESS") -> dict:
    return {"url": url, "state": state, "merged": merged, "isDraft": draft, "title": "", "body": "",
            "headRefName": "", "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": checks}}}]}}


def test_status_does_not_match_the_work():
    api = FakeDispatch([
        issue("BET-1", status="triage", claim=session()),
        issue("BET-2", status="in_progress", claim=session(), links=["https://github.com/o/r/pull/2"]),
        issue("BET-3", status="in_progress", claim=session(),
              links=["https://github.com/o/r/pull/3", "https://github.com/o/r/pull/4"]),
        issue("BET-4", status="in_progress", claim=session(), links=["https://github.com/o/r/pull/5"]),
        issue("BET-5", status="triage", events=[ev("comment.created", session(), 1, id="c")]),
    ])
    asked = {}

    def github(keys, linked):
        asked.update(keys=keys, linked=linked)
        return {"BET-2": {"https://github.com/o/r/pull/2": pr("https://github.com/o/r/pull/2")},
                "BET-3": {"https://github.com/o/r/pull/3": pr("https://github.com/o/r/pull/3", merged=True,
                                                              state="MERGED"),
                          "https://github.com/o/r/pull/4": pr("https://github.com/o/r/pull/4", state="CLOSED")},
                "BET-4": {"https://github.com/o/r/pull/5": pr("https://github.com/o/r/pull/5", checks="FAILURE")}}

    lines = found(check(api, dc.Scope("BET"), github=github), "STATUS")
    assert asked["keys"] == ["BET-1", "BET-2", "BET-3", "BET-4", "BET-5"]
    assert asked["linked"] == {"BET-2": ["https://github.com/o/r/pull/2"],
                               "BET-3": ["https://github.com/o/r/pull/3", "https://github.com/o/r/pull/4"],
                               "BET-4": ["https://github.com/o/r/pull/5"]}
    assert lines == [
        "BET-1 [triage] title of BET-1: work is running (claimed by live 01a1live); should be in_progress",
        "BET-2 [in_progress] title of BET-2: every delivering PR open and green (o/r#2); should be needs_review",
        "BET-3 [in_progress] title of BET-3: every delivering PR merged (o/r#3); should be testing or done",
        "BET-5 [triage] title of BET-5: work is running (agent comment.created 1h 0m ago); should be in_progress",
    ]


def test_a_proof_part_waiting_in_triage_for_release_is_neither_unowned_nor_running():
    # LINUX lead, 2026-10-26 11:18Z: each 'Linux proof part' / 'Linux proof model part' issue is a Legion root that
    # waits in triage, unclaimed, until the owner-release service releases it; running owners comment on unreleased
    # parts on purpose. Released (a claim, a PR, or out of triage), it is checked like any other issue.
    def part(key: str, title: str, **kw) -> dict:
        return {**issue(key, **kw), "title": title}

    api = FakeDispatch([
        part("BET-1", "Linux proof part k60-p00003: 60 nodes, fs, kernel, mm", status="triage",
             events=[ev("comment.created", session(), 1, id="c1")]),
        part("BET-2", "Linux proof model part k60-c00001: lock root", status="triage"),
        part("BET-3", "Linux proof part k60-p00004: 60 nodes", status="triage", claim=session()),
        part("BET-4", "Linux proof part k60-p00005: 60 nodes", status="in_progress"),
    ])
    lines = check(api, dc.Scope("BET"))
    assert found(lines, "UNOWNED") == ["BET-4 [in_progress] Linux proof part k60-p00005: 60 nodes (no claim)"]
    assert found(lines, "STATUS") == [
        "BET-3 [triage] Linux proof part k60-p00004: 60 nodes: work is running (claimed by live 01a1live); "
        "should be in_progress"]


def test_no_status_write_is_proposed_inside_a_legion_tree():
    # Verified Linux owner line, 2026-10-27 00:17Z: agent comments on a triage child under a `legion` root are its
    # architect explaining why it is NOT released; a status write there parks it and stops its workers, and only a
    # person, the Legion controller or the daemon may write it. The same issue outside a Legion tree is still reported.
    url = "https://github.com/o/r/pull/2"
    api = FakeDispatch([
        issue("BET-9", status="in_progress", labels=["legion"]),
        issue("BET-10", status="triage", parent="BET-9", events=[ev("comment.created", session(), 1, id="c1")]),
        issue("BET-2", status="in_progress", claim=session(), labels=["legion"], links=[url]),
        issue("BET-5", status="triage", events=[ev("comment.created", session(), 1, id="c5")]),
    ])
    lines = found(check(api, dc.Scope("BET"), github=lambda keys, linked: {"BET-2": {url: pr(url)}}), "STATUS")
    assert lines == ["BET-5 [triage] title of BET-5: work is running (agent comment.created 1h 0m ago); "
                     "should be in_progress"]


def test_a_child_under_a_live_legion_architect_is_run_by_its_tree():
    # LINUX lead and the INFRA Legion controller, 2026-10-26 11:2xZ: Legion runs a tree's children under its
    # architect's claim (INFRA-366, in progress under INFRA-156's live architect, has no claim of its own). Under any
    # other live claim a child is still unowned (INFRA-353, a child of the chief's INFRA-323, was a real gap), and so
    # is a child whose architect is not running.
    api = FakeDispatch([
        issue("BET-9", claim=session(LIVE, "Legion architect · BET-9")),
        issue("BET-10", status="triage", parent="BET-9"),
        issue("BET-11", status="in_progress", parent="BET-9"),
        issue("BET-20", claim=session(LIVE, "CHIEF: chief of staff")),
        issue("BET-21", status="triage", parent="BET-20"),
        issue("BET-30", claim=session(DEAD, "Legion architect · BET-30")),
        issue("BET-31", status="triage", parent="BET-30"),
    ])
    assert found(check(api, dc.Scope("BET")), "UNOWNED") == [
        "BET-21 [triage] title of BET-21 (no claim)",
        f"BET-30 [in_progress] title of BET-30 (claim by Legion architect · BET-30 since {ago(48)[:16]}Z: not running)",
        "BET-31 [triage] title of BET-31 (no claim)"]


def test_a_parked_legion_claim_is_not_unowned_and_prints_one_line():
    # INFRA Legion controller, 2026-10-27 07:29Z: a Legion claim is the tree's identity, held for the tree's whole life
    # and released only when the tree finishes, so Dispatch shows it "not running" whenever the session is suspended,
    # waiting for a slot or its pod is gone. On 2026-10-27 that printed 181 of 504 LINUX lines as unowned — every one
    # expected, since all LINUX pods were stopped at 23:33Z on Sami's order — and sent a lead chasing them.
    api = FakeDispatch([
        issue("BET-40", labels=["legion"], claim=session(DEAD, "Legion architect · BET-40")),
        issue("BET-41", labels=["legion"], claim=session(DEAD, "Legion architect · BET-41")),
        issue("BET-42", claim=session(DEAD, "CHIEF: chief of staff")),
        issue("BET-43", labels=["legion"]),
    ])
    lines = check(api, dc.Scope("BET"))
    # The dead non-Legion claim and the unclaimed legion issue still need an owner; the two parked trees do not.
    assert found(lines, "UNOWNED") == [
        f"BET-42 [in_progress] title of BET-42 (claim by CHIEF: chief of staff since {ago(48)[:16]}Z: not running)",
        "BET-43 [in_progress] title of BET-43 (no claim)"]
    parked = found(lines, "LEGION-PARKED")
    assert len(parked) == 1
    assert parked[0].startswith("2 legion-labelled issues are held by a Legion claim whose session is not running")
    assert "legion/v1/state" in parked[0]
    # The summary counts LINES, as it does for every kind: one line here, naming two issues.
    assert "1 LEGION-PARKED" in lines[-1]


def test_a_child_of_a_suspended_legion_architect_is_parked_not_unowned():
    # INFRA Legion controller, 2026-10-31 21:24Z: during a restart of the Legion pods, INFRA-514's architect claim was
    # suspended and five of its children (no claim of their own) read as unowned, though their implementers worked.
    api = FakeDispatch([
        issue("BET-60", labels=["legion"], claim=session(DEAD, "Legion architect · BET-60")),
        issue("BET-61", parent="BET-60"),
        issue("BET-62", parent="BET-60", claim=session(DEAD, "Legion implementer · BET-62")),
        issue("BET-63", labels=["legion"]),
    ])
    lines = check(api, dc.Scope("BET"))
    # The child of the suspended architect is Legion's; an unclaimed legion issue with no architect above it is not.
    assert found(lines, "UNOWNED") == ["BET-63 [in_progress] title of BET-63 (no claim)"]
    assert found(lines, "LEGION-PARKED")[0].startswith("3 legion-labelled issues")

def test_a_legion_issue_is_not_asked_for_a_plan_document():
    # Verified Linux lead, 2026-10-27 07:29Z: of 129 LINUX NO-PLAN lines, 125 were `Linux proof part` issues Legion
    # runs, whose plan is the architect's tree, not a document on the issue. Only 4 were real.
    api = FakeDispatch([
        issue("BET-50", labels=["legion"], claim=session()),
        issue("BET-51", parent="BET-50", claim=session()),
        issue("BET-52", claim=session()),
    ])
    assert found(check(api, dc.Scope("BET")), "NO-PLAN") == [
        "BET-52 [in_progress] title of BET-52 (no plan document; claimed by live 01a1live)"]

def test_a_green_pull_request_the_issue_does_not_link_moves_no_status():
    # BET-130, 2026-10-25: a task issue whose deliverable is tasks; its tooling PRs name the key and are green.
    api = FakeDispatch([issue("BET-9", status="in_progress", claim=session())])
    tooling = {"https://github.com/o/r/pull/1414": pr("https://github.com/o/r/pull/1414")}
    lines = found(check(api, dc.Scope("BET"), github=lambda keys, linked: {"BET-9": tooling}), "STATUS")
    assert lines == []


def test_an_issue_whose_spec_sets_its_own_status_rule_is_left_alone():
    spec = {"slug": "spec", "id": "s1", "versions": [{"number": 1}]}
    api = FakeDispatch([issue("BET-9", status="in_progress", claim=session(), artifacts=[spec],
                              links=["https://github.com/o/r/pull/7"])],
                       texts={"s1": "## Problem\n\nThe status moves only on the criteria below, measured on Taiga."})
    green = {"https://github.com/o/r/pull/7": pr("https://github.com/o/r/pull/7")}
    lines = found(check(api, dc.Scope("BET"), github=lambda keys, linked: {"BET-9": green}), "STATUS")
    assert lines == []
    # The same issue without that sentence in its spec still gets the line, from its delivering pull request.
    plain = FakeDispatch([issue("BET-9", status="in_progress", claim=session(), artifacts=[spec],
                                links=["https://github.com/o/r/pull/7"])],
                         texts={"s1": "## Problem\n\nPort the grader to the new API."})
    assert found(check(plain, dc.Scope("BET"), github=lambda keys, linked: {"BET-9": green}), "STATUS") == [
        "BET-9 [in_progress] title of BET-9: every delivering PR open and green (o/r#7); should be needs_review",
    ]


def test_status_leaves_an_issue_with_open_children_in_progress():
    # TASKCOACH, 03:53Z: BET-2 indexes the task-type families, 13 of 22 children open; its own PR merging is not done.
    url = "https://github.com/o/r/pull/1"
    api = FakeDispatch([
        issue("BET-2", status="in_progress", claim=session(), links=[url]),
        issue("BET-9", status="in_progress", claim=session(), parent="BET-2"),
        issue("BET-7", status="in_progress", claim=session(), links=[url]),
        issue("BET-8", status="done", parent="BET-7"),
    ])
    merged = {url: pr(url, merged=True, state="MERGED")}
    lines = found(check(api, dc.Scope("BET"), github=lambda keys, linked: {"BET-2": merged, "BET-7": merged}),
                  "STATUS")
    assert lines == ["BET-7 [in_progress] title of BET-7: every delivering PR merged (o/r#1); should be testing or done"]


def test_status_reports_a_failed_github_read_and_the_rest_still_runs():
    def github(keys, linked):
        raise RuntimeError("app token: no key")

    lines = check(FakeDispatch([issue("BET-1")]), dc.Scope("BET"), github=github)
    assert found(lines, "STATUS") == ["check NOT READ for pull requests (app token: no key); claims and comments still read"]
    assert found(lines, "UNOWNED") == ["BET-1 [in_progress] title of BET-1 (no claim)"]
    assert "0 STATUS" in lines[-1]


def test_no_plan():
    api = FakeDispatch([
        issue("BET-1", claim=session()),
        issue("BET-2", claim=session(), artifacts=[{"slug": "plan-week-md", "id": "p2"}]),
        issue("BET-3", claim=session(), artifacts=[{"slug": "plan-old-md", "id": "x"}, {"slug": "plan-md", "id": "p3"}]),
        issue("BET-4", status="icebox", claim=session()),
    ], texts={"p2": "Ship it soon.", "p3": "Ship by Oct 27."})
    assert found(check(api, dc.Scope("BET")), "NO-PLAN") == [
        "BET-1 [in_progress] title of BET-1 (no plan document; claimed by live 01a1live)",
        "BET-2 [in_progress] title of BET-2 (plan dispatch://BET-2/artifact/plan-week-md states neither a date nor "
        "its work items with a state; claimed by live 01a1live)",
    ]


def test_a_plan_that_states_its_work_items_with_a_state_needs_no_date():
    # Sami to the verified-grader lead, 2026-10-29 19:04Z (dispatch://BET-59, comment e4f28bb2): "I'm not interested
    # in you setting dates ... design the work that needs to happen, the dependencies, and then work towards it."
    graph = ("# Plan\n\n## Work graph\n\n"
             "| Item | Depends on | State | Done means |\n| --- | --- | --- | --- |\n"
             "| 1\\. cut the kernel | none | **done**: #1842 | merged |\n"
             "| 2\\. rebuild the versions | 1 | **in flight** | published |\n"
             "| 3\\. deliver them | 2 | blocked by 2 | delivered |\n")
    prose = "# Plan\n\nWe will cut the kernel, then rebuild the versions, then deliver them.\n"
    headerless = ("# Plan\n\n| 1\\. cut the kernel | none | **done** |\n"
                  "| 2\\. rebuild the versions | 1 | **in flight** |\n")
    api = FakeDispatch([
        issue("BET-1", claim=session(), artifacts=[{"slug": "plan-md", "id": "p1"}]),
        issue("BET-2", claim=session(), artifacts=[{"slug": "plan-md", "id": "p2"}]),
        issue("BET-3", claim=session(), artifacts=[{"slug": "plan-md", "id": "p3"}]),
    ], texts={"p1": graph, "p2": prose, "p3": headerless})
    assert found(check(api, dc.Scope("BET")), "NO-PLAN") == [
        "BET-2 [in_progress] title of BET-2 (plan dispatch://BET-2/artifact/plan-md states neither a date nor its "
        "work items with a state; claimed by live 01a1live)",
        "BET-3 [in_progress] title of BET-3 (plan dispatch://BET-3/artifact/plan-md states neither a date nor its "
        "work items with a state; claimed by live 01a1live)",
    ]


def test_a_work_graph_needs_a_state_on_each_item_row():
    # One item with a state is a note, not a plan; a state column with empty cells states nothing.
    one_item = "| Item | State |\n| --- | --- |\n| 1\\. cut the kernel | **done** |\n"
    empty = "| Step | Status |\n| --- | --- |\n| 1\\. cut the kernel |  |\n| 2\\. rebuild |  |\n"
    two_items = "| Step | Status |\n| --- | --- |\n| 1\\. cut the kernel | done |\n| 2\\. rebuild | ready |\n"
    assert dc.is_work_graph(one_item) is False
    assert dc.is_work_graph(empty) is False
    assert dc.is_work_graph(two_items) is True


def test_summary_when_nothing_is_found():
    lines = check(FakeDispatch([issue("BET-1", status="done")]), dc.Scope("BET"))
    # With no re-drive the summary says so: a zero PAST-ACK count must never read as "nothing is past its time".
    assert lines == ["DISPATCH-SUMMARY BET: 0 open BET + 0 other issues read; 0 UNOWNED, 0 QUIET, 0 OVERDUE, "
                     "0 WAITING-ON-US, 0 PAST-ACK, 0 NO-EXECUTOR, 0 STATUS, 0 NO-PLAN; no audit table; "
                     "re-drive off"]


def test_all_streams_runs_each_stream_project_and_scopes_issue_and_audit(capsys):
    streams = [{"key": "OSSV-142", "project": None}, {"key": "BET-59", "project": "BET"},
               {"key": "BET-83", "project": "BET"}]
    api = FakeDispatch([
        issue("BET-1"), issue("OSSV-1"), issue("OSSV-2", status="done"), issue("INFRA-9", status="backlog"),
        issue("BET-59", status="backlog", labels=["stream"]), issue("BET-83", status="backlog", labels=["stream"]),
        issue("OSSV-142", claim=session(), artifacts=[{"slug": "audit-md", "id": "a"}, {"slug": "plan-md", "id": "p"}]),
    ], streams=streams, texts={"a": AUDIT.replace("BET-", "OSSV-"), "p": "due 10-30"})
    code = dc.main(["--all-streams", "--issue", "OSSV:INFRA-9", "--audit", "OSSV-142:audit-md", "--as-of",
                    iso(NOW), "--no-redrive"], api=api, github=no_prs)
    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert [ln for ln in out if ln.startswith(("==", "DISPATCH-SUMMARY"))] == [
        "== BET (stream BET-59 BET-83) ==",
        "DISPATCH-SUMMARY BET: 3 open BET + 0 other issues read; 1 UNOWNED, 0 QUIET, 0 OVERDUE, 0 WAITING-ON-US, "
        "0 PAST-ACK, 0 NO-EXECUTOR, 0 STATUS, 0 NO-PLAN; no audit table; re-drive off",
        "== OSSV (stream OSSV-142) ==",
        "DISPATCH-SUMMARY OSSV: 2 open OSSV + 1 other issues read; 2 UNOWNED, 1 QUIET, 2 OVERDUE, 0 WAITING-ON-US, "
        "0 PAST-ACK, 0 NO-EXECUTOR, 0 STATUS, 0 NO-PLAN; audit dispatch://OSSV-142/artifact/audit-md: 6 rows, "
        "1 with no date (#5); re-drive off",
    ]
    assert "INFRA-9 [backlog] title of INFRA-9 (no claim)" in found(out, "UNOWNED")
    # The audit row with no issue key belongs to the audit's issue, claimed by a running session.
    assert [ln.split(" | owner: ")[1] for ln in out if ln.startswith("DISPATCH-OVERDUE")] == [
        "no owner", f"live 01a1live ({LIVE})"]
    # Open asks are read for the extra issue's project too, only in the run that covers it.
    assert "/api/v1/asks/open?project=INFRA" in api.paths


def test_a_failed_dispatch_read_fails_that_project_only(capsys):
    streams = [{"key": "OSSV-142", "project": "OSSV"}, {"key": "BET-59", "project": "BET"}]
    api = FakeDispatch([issue("BET-1"), issue("BET-59", status="backlog", labels=["stream"]),
                        issue("OSSV-142", claim=session())], streams=streams, fail={"BET"})
    assert dc.main(["--all-streams", "--no-redrive"], api=api, github=no_prs) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("DISPATCH-SUMMARY BET: NOT READ (SourceError: GET /api/v1/issues?project=BET: HTTP 500")
    assert out[-1].startswith("DISPATCH-SUMMARY OSSV: 1 open OSSV")


def test_a_pull_request_that_only_mentions_the_issue_is_not_its_work():
    # theorem-labs/agent-infra#116 named BET-9 in its body as background ("trees left from the shutdown: BET-9,
    # BET-10") and the check then said BET-9 should be testing or done.
    def pr(url, title="t", body="", branch="b"):
        return {"url": url, "title": title, "body": body, "headRefName": branch}

    class Fake(dc.GitHub):
        def gql(self, fields):
            return {"k0": {"nodes": [
                pr("u/mention", body="Trees left from the 10-17 shutdown: BET-9, BET-10 and BET-12."),
                pr("u/title", title="BET-9: fix the grader"),
                pr("u/branch", branch="bet-9-fix"),
                pr("u/closes", body="Closes dispatch://BET-9."),
                pr("u/partof", body="Part of BET-9"),
                pr("u/other", title="BET-90: something else"),
            ]}}

    assert sorted(Fake()(["BET-9"], {})["BET-9"]) == ["u/branch", "u/closes", "u/partof", "u/title"]

    class Kernel(dc.GitHub):  # CAS, 03:33Z: "linux-4.19.y" and "Linux-4.14" counted as LINUX-4
        def gql(self, fields):
            return {"k0": {"nodes": [
                pr("u/kernel-branch", branch="linux-4.19.y"),
                pr("u/kernel-title", title="Backport the fix to Linux-4.14"),
                pr("u/kernel-closes", body="Fixes linux-4.19.y builds"),
                pr("u/real", title="LINUX-4: check-only integrate gate"),
            ]}}

    assert sorted(Kernel()(["LINUX-4"], {})["LINUX-4"]) == ["u/real"]
