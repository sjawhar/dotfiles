"""lead-times on recorded responses: BET-74's issue, events and first plan version, and a selection of the theorem-labs
pull requests merged 2026-10-21 (tests/data/lead_times/bet-74.json, read 2026-10-25, trimmed as its _note says); and
small issues built in the same shape for each other rule."""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chief_dashboard import dispatch_check as dc
from chief_dashboard import lead_times as lt
from chief_dashboard.sources import Dispatch, SourceError

RECORDED = json.loads((Path(__file__).parent / "data" / "lead_times" / "bet-74.json").read_text())
NOW = datetime(2026, 10, 25, 4, 0, tzinfo=UTC)  # the window of 7 days starts 10-18 04:00Z
SESSION = {"kind": "session", "id": "01a1live-0000-0000-0000-000000000000"}


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def at(day: int, hour: float = 0) -> str:
    return iso(datetime(2026, 10, day, tzinfo=UTC) + timedelta(hours=hour))


def ev(type_: str, when: str, status: str) -> dict:
    return {"type": type_, "created_at": when, "actor": SESSION, "payload": {"status": status}}


def built(key: str, created: str, evs: list[dict], labels=(), plan: dict | None = None) -> dict:
    """An issue in the recorded shape; PLAN is {version number: (created_at, markdown)}."""
    issue = {"key": key, "title": f"title of {key}", "status": evs[-1]["payload"]["status"] if evs else "triage",
             "labels": list(labels), "created_at": created, "updated_at": evs[-1]["created_at"] if evs else created,
             "events": [{**e, "seq": n} for n, e in enumerate(evs, 1)], "artifacts": [], "plan": {}}
    if plan:
        issue["artifacts"] = [{"id": f"id-{key}", "slug": "plan-md", "name": "plan.md",
                               "versions": [{"number": n, "created_at": c} for n, (c, _) in plan.items()]}]
        issue["plan"] = {str(n): {"markdown": md} for n, (_, md) in plan.items()}
    return issue


def recorded() -> dict:
    return {**RECORDED["issue"], "events": RECORDED["events"], "plan": RECORDED["plan_versions"]}


class FakeDispatch(Dispatch):
    """Serves the routes lead_times reads from in-memory issues; records every path it was asked for."""

    def __init__(self, issues: list[dict], streams=(), fail=(), listed=()) -> None:
        self.server = "https://dispatch.test"
        self.issues = {i["key"]: i for i in issues}
        self.streams = list(streams)
        self.fail = set(fail)
        self.paths: list[str] = []
        # Keys the issue list also names, last updated before any window (their issues are never read).
        self.listed = [{"key": k, "status": "done", "updated_at": "2026-01-01T00:00:00Z"} for k in listed
                       if k not in self.issues]

    def get(self, path: str):
        self.paths.append(path)
        url = urllib.parse.urlparse(path)
        q = dict(urllib.parse.parse_qsl(url.query))
        parts = url.path.removeprefix("/api/v1/").split("/")
        if q.get("project") in self.fail:
            raise SourceError(f"GET {path}: HTTP 500")
        if parts == ["issues"]:
            if q.get("label") == "stream":
                return self.streams
            return [{k: v for k, v in i.items() if k not in ("events", "plan", "artifacts")}
                    for i in [*self.issues.values(), *self.listed] if i["key"].startswith(q["project"] + "-")]
        i = self.issues[parts[1]]
        if len(parts) == 3:
            return [e for e in i["events"] if e["seq"] > int(q["after"])][: int(q["limit"])]
        if len(parts) == 6:
            return i["plan"][parts[5]]
        return {k: v for k, v in i.items() if k not in ("events", "plan")}


def prs_of(prs):
    return lambda since, now: [p for p in prs if since <= dc.ts(p["mergedAt"]) <= now]


def run(api: FakeDispatch, prs=(), argv=("--project", "BET"), capsys=None) -> tuple[int, list[str]]:
    code = lt.main(list(argv), api=api, prs=prs if callable(prs) else prs_of(prs), now=NOW)
    return code, capsys.readouterr().out.splitlines()


def row(lines: list[str], measure: str, group: str) -> list[str]:
    """The cells of the table row for MEASURE and GROUP: n, median, p85, then the note's words."""
    for ln in lines:
        rest = ln.removeprefix(measure).strip()
        if ln.startswith(measure + " ") and rest.startswith(group + " "):
            return rest.removeprefix(group).split()
    raise AssertionError(f"no row {measure} {group} in:\n" + "\n".join(lines))


def test_recorded_issue_prs_and_plan(capsys):
    api = FakeDispatch([recorded()], listed=RECORDED["bet_keys"])
    code, lines = run(api, RECORDED["prs"], capsys=capsys)
    # Only BET-74 was updated in the window, so only its issue is read.
    assert not [p for p in api.paths if p.startswith("/api/v1/issues/") and "BET-74" not in p]
    assert code == 0
    assert lines[0] == "== BET: 2026-10-18 04:00Z to 2026-10-25 04:00Z =="
    # Claimed 10-21 03:38:32Z, done 10-21 22:22:22Z.
    assert row(lines, "claim-to-done", "all")[:3] == ["1", "18.7h", "18.7h"]
    # BET-73's one PR and BET-40's three; the PRs that name BET-3, BET-63, BET-74 only as context are not counted.
    assert row(lines, "PR open-to-merge", "all")[:3] == ["4", "23.1h", "24.7h"]
    assert row(lines, "PR open-to-merge", "theorem-labs/binary-equivalence-taiga")[:3] == ["1", "1.5h", "1.5h"]
    assert row(lines, "PR open-to-merge", "theorem-labs/proof-efficiency-eval")[:3] == ["3", "23.8h", "25.0h"]
    # plan.md v1 (10-21 06:21Z) holds 2026-10-21 as its latest date: 17.6 h allowed over 16.0 h taken.
    assert row(lines, "plan date / done", "BET-74")[:2] == ["1", "1.10x"]
    assert "plan-md set 10-21 06:21Z for 10-21 23:59Z; done 10-21 22:22Z" in lines[-2]
    assert lines[-1] == ("Set dates from these, not from instinct: median claim-to-done 18.7 h, "
                         "PR open-to-merge 23.1 h.")


def test_recorded_pr_matching_is_dispatch_checks():
    prs = lt.project_prs("BET", set(RECORDED["bet_keys"]), RECORDED["prs"])
    assert [(p["url"].rsplit("/", 3)[1] + "#" + p["url"].rsplit("/", 1)[1], p["keys"]) for p in prs] == [
        ("binary-equivalence-taiga#1096", ["BET-73"]), ("proof-efficiency-eval#268", ["BET-40"]),
        ("proof-efficiency-eval#267", ["BET-40"]), ("proof-efficiency-eval#266", ["BET-40"])]
    assert all(any(dc.names_key(p, k) for k in p["keys"]) for p in prs)
    # A branch name in lower case names the key; a key of another project does not count for BET.
    pr = {"title": "x", "body": "", "headRefName": "sami/bet-73-fix"}
    assert lt.project_prs("BET", {"BET-73"}, [pr])[0]["keys"] == ["BET-73"]
    assert lt.project_prs("INFRA", {"INFRA-73"}, [pr]) == []


def test_start_end_and_window(capsys):
    api = FakeDispatch([
        # Claimed day 20 06:00, needs_review day 20 10:00 (the end), then done later: 4 h.
        built("BET-1", at(19), [ev("issue.created", at(19), "triage"), ev("issue.claimed", at(20, 6), "triage"),
                                ev("issue.updated", at(20, 10), "needs_review"), ev("issue.closed", at(22), "done")],
              labels=["infra"]),
        # Never claimed: from creation (day 21 00:00) to done (day 21 08:00): 8 h.
        built("BET-2", at(21), [ev("issue.created", at(21), "todo"), ev("issue.closed", at(21, 8), "done")],
              labels=["infra", "proofs"]),
        # Claimed only after it reached testing: from creation, 2 h.
        built("BET-3", at(22), [ev("issue.created", at(22), "todo"), ev("issue.updated", at(22, 2), "testing"),
                                ev("issue.claimed", at(23), "testing")]),
        # Finished before the window (and updated in it): not counted.
        built("BET-4", at(10), [ev("issue.created", at(10), "todo"), ev("issue.closed", at(11), "done"),
                                ev("issue.updated", at(24), "done")]),
        # Still running; a child's status event does not finish the parent.
        built("BET-5", at(20), [ev("issue.created", at(20), "todo"), ev("child.status", at(21), "done")]),
    ])
    code, lines = run(api, capsys=capsys)
    assert code == 0
    assert row(lines, "claim-to-done", "all") == ["3", "4.0h", "6.8h", "1", "from", "a", "claim,", "2", "from",
                                                  "creation", "(no", "claim)"]
    assert row(lines, "claim-to-done", "label infra")[:3] == ["2", "6.0h", "7.4h"]
    assert row(lines, "claim-to-done", "label proofs")[:3] == ["1", "8.0h", "8.0h"]
    assert row(lines, "PR open-to-merge", "all")[:3] == ["0", "-", "-"]
    assert lines[-1] == ("Set dates from these, not from instinct: median claim-to-done 4.0 h, "
                         "PR open-to-merge none in the window.")


def test_events_are_paged():
    evs = [ev("issue.created", at(20), "todo")] + [ev("comment.created", at(20, 1), "todo")] * 450 + [
        ev("issue.closed", at(21), "done")]
    api = FakeDispatch([built("BET-9", at(20), evs)])
    assert len(lt.events(api, "BET-9")) == 452
    assert [p for p in api.paths if "/events" in p] == [
        "/api/v1/issues/BET-9/events?after=0&limit=200", "/api/v1/issues/BET-9/events?after=200&limit=200",
        "/api/v1/issues/BET-9/events?after=400&limit=200"]


def test_plan_dates(capsys):
    done = lambda day, hour: [ev("issue.created", at(19), "todo"), ev("issue.claimed", at(19), "todo"),  # noqa: E731
                              ev("issue.closed", at(day, hour), "done")]
    api = FakeDispatch([
        # v1 has no date; v2 (day 20 00:00) is first set, for 10-22 (23:59Z); done day 21 00:00: 72 h allowed / 24 h.
        built("BET-1", at(19), done(21, 0), plan={1: (at(19), "# plan\nno date yet, segments 12-34"),
                                                  2: (at(20), "# plan\nship by 10-22\nreviewed 2026-10-19"),
                                                  3: (at(23), "# plan\nship by 10-30")}),
        # Dated only after it finished.
        built("BET-2", at(19), done(20, 0), plan={1: (at(21), "due 10-24")}),
        # The latest date is before the plan was written.
        built("BET-3", at(19), done(22, 0), plan={1: (at(21), "measured 2026-10-19 12:00")}),
        # No plan: no row.
        built("BET-4", at(19), done(22, 0)),
    ])
    code, lines = run(api, capsys=capsys)
    assert code == 0
    assert row(lines, "plan date / done", "BET-1")[:2] == ["1", "3.00x"]
    assert "plan-md set 10-20 00:00Z for 10-22 23:59Z; done 10-21 00:00Z" in " ".join(row(lines, "plan date / done",
                                                                                          "BET-1"))
    assert row(lines, "plan date / done", "BET-2")[-5:] == ["dated", "only", "after", "it", "finished"]
    assert row(lines, "plan date / done", "BET-3")[-6:] == ["date", "before", "the", "plan", "was", "written"]
    assert row(lines, "plan date / done", "all")[:3] == ["1", "3.00x", "3.00x"]
    assert not [ln for ln in lines if ln.startswith("plan date / done  BET-4")]
    # The versions after the first dated one are not read.
    assert "/api/v1/issues/BET-1/artifacts/plan-md/versions/3" not in api.paths


def test_github_failure_still_prints_dispatch(capsys):
    def broken(since, now):
        raise RuntimeError("app token: no key")

    api = FakeDispatch([built("BET-2", at(21), [ev("issue.created", at(21), "todo"),
                                                 ev("issue.closed", at(21, 8), "done")])])
    code, lines = run(api, broken, capsys=capsys)
    assert code == 1
    assert row(lines, "claim-to-done", "all")[:2] == ["1", "8.0h"]
    assert row(lines, "PR open-to-merge", "all") == ["-", "-", "-", "NOT", "READ", "(app", "token:", "no", "key)"]
    assert lines[-1].endswith("PR open-to-merge NOT READ.")


def test_all_streams_one_table_per_project_and_a_failed_read(capsys):
    api = FakeDispatch([built("BET-2", at(21), [ev("issue.created", at(21), "todo"),
                                                 ev("issue.closed", at(21, 8), "done")])],
                       streams=[{"key": "BET-59", "project": None}, {"key": "INFRA-6", "project": "INFRA"},
                                {"key": "BET-83"}], fail=["INFRA"])
    calls = []

    def prs(since, now):
        calls.append((since, now))
        return []

    code, lines = run(api, prs, argv=["--all-streams", "--days", "3"], capsys=capsys)
    assert code == 1
    heads = [ln for ln in lines if ln.startswith("==")]
    assert heads == ["== BET (stream BET-59 BET-83): 2026-10-22 04:00Z to 2026-10-25 04:00Z ==",
                     "== INFRA (stream INFRA-6): 2026-10-22 04:00Z to 2026-10-25 04:00Z =="]
    assert lines[-1].startswith("NOT READ (SourceError: GET /api/v1/issues?project=INFRA: HTTP 500")
    assert len(calls) == 1  # the org's merged pull requests are read once for every project


def test_merged_prs_pages_each_day_and_keeps_the_window():
    pages = {}

    class FakeGitHub:
        org = "theorem-labs"

        def gql(self, fields):
            q = fields["s"]
            day = q.split("merged:")[1][:10]
            cursor = q.split('after:"')[1].split('"')[0] if 'after:"' in q else None
            pages.setdefault(day, []).append(cursor)
            pr = lambda n, merged: {"url": f"u{day}-{n}", "mergedAt": merged}  # noqa: E731
            if day == "2026-10-24" and cursor is None:
                return {"s": {"pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                              "nodes": [pr(1, "2026-10-24T01:00:00Z"), None]}}
            if day == "2026-10-24":
                return {"s": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                              "nodes": [pr(2, "2026-10-24T05:00:00Z"), pr(1, "2026-10-24T01:00:00Z")]}}
            return {"s": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                          "nodes": [pr(1, f"{day}T02:00:00Z"), pr(2, f"{day}T05:00:00Z")]}}

    got = lt.MergedPrs(FakeGitHub())(datetime(2026, 10, 23, 3, tzinfo=UTC), datetime(2026, 10, 25, 4, tzinfo=UTC))
    assert sorted(pages) == ["2026-10-23", "2026-10-24", "2026-10-25"]
    assert pages["2026-10-24"] == [None, "c1"]
    # 10-23 02:00 is before the window, 10-25 05:00 after it; the repeated PR is counted once.
    assert sorted(p["url"] for p in got) == ["u2026-10-23-2", "u2026-10-24-1", "u2026-10-24-2", "u2026-10-25-1"]


def test_p85_and_due_of_skips_non_dates():
    assert lt.p85([1.0]) == 1.0
    assert abs(lt.p85([0.0, 10.0]) - 8.5) < 1e-9
    assert dc.due_of("segments 12-34, done 10-21", NOW) == datetime(2026, 10, 21, 23, 59, tzinfo=UTC)
    assert dc.due_of("segments 12-34", NOW) is None
