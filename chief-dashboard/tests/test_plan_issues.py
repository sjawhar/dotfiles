"""Today's plan, one Dispatch issue per item: the issue the item names, else the one its pull request delivers (by
dispatch-check's rules), its priority and status, its owner, and a link to the owner's live conversation."""

from __future__ import annotations

import re

from test_dashboard import NOW, board, h, snapshot

from chief_dashboard import plan_issues as pi
from chief_dashboard import prs, v2_model, v2_render
from chief_dashboard.plan import Item, _items

WEB = "https://dispatch.example"
PR = "theorem-labs/binary-equivalence-taiga#1255"
PR_URL = "https://github.com/theorem-labs/binary-equivalence-taiga/pull/1255"
LEAD = "01a16a9e-a90d-7148-95ed-0e9e7839dbd2"
WORKER = "01a175dd-5d8a-75ac-99a0-68919637ef66"


def claim(sid: str, title: str) -> dict:
    return {"actor": {"kind": "session", "id": sid, "origin": {"session_title": title}}, "at": h(5)}


def issue(key: str, **over) -> dict:
    return {"key": key, "title": f"title of {key}", "status": "in_progress", "priority": 1, "assignee": None,
            "claim": None, "route_holder": None, "external_links": [], **over}


def test_named_issue_with_its_priority_status_and_claimed_owner_live_link():
    issues = {"BET-12": issue("BET-12", priority=0, claim=claim(WORKER, "Lead OSS Verify CI/CD"))}
    agents = {WORKER: {"title": "OSSV CI/CD (live title)"}}
    ref = pi.resolve(Item("Land the gate", PR, issue="BET-12"), {}, issues, agents, WEB)
    assert (ref.key, ref.how, ref.priority_text, ref.status_text) == ("BET-12", pi.NAMED, "P0", "in progress")
    assert ref.url == f"{WEB}/issues/BET-12"
    assert (ref.owner, ref.owner_running) == ("OSSV CI/CD (live title)", True)
    assert ref.owner_url == f"{WEB}/agents/{WORKER}/live"


def test_without_a_named_issue_the_issue_that_links_the_pull_request_as_delivering_it_wins():
    states = {PR: {"url": PR_URL, "named_keys": ["BET-5", "BET-6"]}}
    issues = {"BET-5": issue("BET-5"), "BET-6": issue("BET-6", external_links=[{"url": PR_URL, "kind": "pr"}])}
    ref = pi.resolve(Item("R acceptance", PR), states, issues, {}, WEB)
    assert (ref.key, ref.how) == ("BET-6", pi.DELIVERS)


def test_a_single_named_key_is_the_issue_and_several_unlinked_ones_are_none():
    one = {PR: {"url": PR_URL, "named_keys": ["BET-5"]}}
    assert pi.resolve(Item("x", PR), one, {"BET-5": issue("BET-5")}, {}, WEB).how == pi.NAMED_BY_PR
    two = {PR: {"url": PR_URL, "named_keys": ["BET-5", "BET-6"]}}
    assert pi.resolve(Item("x", PR), two, {"BET-5": issue("BET-5"), "BET-6": issue("BET-6")}, {}, WEB) is None
    assert pi.resolve(Item("no pull request, no issue", None), {}, {}, {}, WEB) is None


def test_owner_falls_back_to_route_holder_then_assignee():
    routed = issue("BET-7", route_holder=LEAD)
    owner, sid, running, url = pi.owner_of(routed, {}, WEB)
    assert (sid, running, url) == (LEAD, False, f"{WEB}/agents/{LEAD}/live") and owner.startswith("session 01a16a9e")
    person = issue("BET-8", assignee="jason@theorem.dev")
    assert pi.owner_of(person, {}, WEB) == ("jason@theorem.dev", None, False, None)
    claimed_by_person = issue("BET-9", claim={"actor": {"kind": "user", "id": "sami@theorem.dev"}}, route_holder=LEAD)
    assert pi.owner_of(claimed_by_person, {}, WEB)[0] == "sami@theorem.dev"


def test_plan_items_read_the_issue_key_and_github_pull_request_urls():
    items = _items({"items": [
        {"what": "a", "pr": "https://github.com/theorem-labs/oss-verify/pull/26", "issue": "dispatch://ossv-150"},
        {"what": "b", "pr": "theorem-labs/x#3", "issue": "not a key"},
    ]})
    assert [(i.pr, i.issue) for i in items] == [("theorem-labs/oss-verify#26", "OSSV-150"), ("theorem-labs/x#3", None)]


def test_keys_a_pull_request_names_as_its_work_follow_dispatch_check():
    node = {"title": "BET-12: harness refuses mismatched objects", "headRefName": "infra-3-gate",
            "body": "Closes OSSV-9. Context: see LINUX-4 and linux-4.19.y."}
    assert prs.named_keys(node) == ["BET-12", "INFRA-3", "OSSV-9"]
    items = [Item("a", "o/r#1"), Item("b", "o/r#2", issue="BET-40")]
    states = {"o/r#1": {"named_keys": ["BET-12", "UTF-8"]}, "o/r#2": {"named_keys": ["BET-13"]}}
    assert pi.wanted_keys(items, states, {"BET", "INFRA"}) == {"BET-12", "BET-40"}


class FakeDispatch:
    def __init__(self, issues):
        self.issues = issues

    def issue(self, key):
        from chief_dashboard.sources import SourceError

        if key not in self.issues:
            raise SourceError(f"GET /api/v1/issues/{key}: HTTP 404")
        return {**self.issues[key], "artifacts": [{"slug": "spec"}], "events": []}


def test_read_issues_keeps_only_the_shown_fields_and_records_an_unreadable_one():
    got = pi.read_issues(FakeDispatch({"BET-1": issue("BET-1")}), {"BET-1", "BET-404"})
    assert set(got["BET-1"]) == {"key", "title", "status", "priority", "assignee", "claim", "route_holder",
                                 "external_links"}
    assert "404" in got["BET-404"]["error"]


def test_front_page_plan_shows_each_items_issue_and_owner_link_and_marks_items_without_one():
    b = board(today={"date": "2026-10-24", "items": [
        {"what": "Land the gate", "pr": PR, "issue": "BET-12"},
        {"what": "Write the runbook", "pr": None},
    ]})
    snap = snapshot([b])
    for v in snap["scoreboards"]["BET-83"]["versions"]:
        v["created_at"] = h(10)  # before 09:00 Pacific on Oct 24 (16:00Z): the plan is fixed
    snap["plan_issues"] = {"BET-12": issue("BET-12", priority=1, status="needs_review", claim=claim(WORKER, "CI/CD"))}
    snap["agents"][WORKER] = {"title": "CI/CD lead"}
    page = v2_model.build(snap, NOW)
    lines = next(v for v in page.streams if v.st.issue == "BET-83").lines
    html = "".join(v2_render.plan_item(ln, NOW) for ln in lines)
    assert [ln.issue.key if ln.issue else None for ln in lines] == ["BET-12", None]
    assert 'href="https://dispatch.example/issues/BET-12"' in html
    assert f'href="https://dispatch.example/agents/{WORKER}/live"' in html and "CI/CD lead ›" in html
    assert re.search(r'class="pri p1">P1</span> <span class="ist">needs review', html)
    assert html.count("No Dispatch issue") == 1


def test_check_rejects_a_malformed_issue_key_and_warns_about_items_without_one():
    from test_dashboard import well_formed

    from chief_dashboard import check

    b = well_formed(board(today={"date": "2026-10-24", "items": [
        {"what": "a", "issue": "BET-12"}, {"what": "b", "issue": "the gate issue"}, {"what": "c", "pr": PR}]}))
    errors, lines = check.report(b, NOW)
    assert errors == ["today.items[1].issue: 'the gate issue' is not a Dispatch issue key such as OSSV-150"]
    assert [ln for ln in lines if "names no Dispatch issue" in ln] == [
        "Warning: today.items[2] ('c') names no Dispatch issue; the page shows the issue its pull request delivers, "
        "else 'No Dispatch issue'."]
