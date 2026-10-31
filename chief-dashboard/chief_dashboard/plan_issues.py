"""Each plan item's Dispatch issue, its priority and status, the agent that owns it, and the address of that agent's
live conversation in Dispatch (Sami, 2026-10-26: "today's plan should map one to one with issues in dispatch. And
should also show the priorities ... which agent is owning it and also I can just click on that and then jump into the
live conversation with the agent").

- The issue: the item's own `issue` key when the lead names one (the scoreboard contract, dispatch://INFRA-224). Else
  the issue its pull request delivers, by dispatch-check's rules: among the keys the pull request names as its own work
  (`dispatch_check.names_key`: its title or branch names the key, or its body says it closes, fixes, resolves,
  implements or is part of it), the issue that links the pull request in `external_links`
  (`dispatch_check.delivering`, "the pull request that delivers it"). When none links it, the one key it names is the
  issue, shown as "named by its pull request"; with several and none linked the item has no issue.
- The owner: the issue's claim holder (a session or a person), else its route holder (a session), else its assignee
  (a person). Only a session has a live conversation.
- The live conversation: Dispatch's web route `/agents/<session id>/live`, the route its own web app links a running
  session to (`Qv="/agents/:sessionId/live"` in the served app bundle, read 2026-10-26)."""

from __future__ import annotations

import concurrent.futures as cf
import urllib.parse
from dataclasses import dataclass
from datetime import datetime

from .dispatch_check import delivering
from .plan import Item, day_plan, pacific_day, week_plan
from .sources import Dispatch, SourceError

NAMED, DELIVERS, NAMED_BY_PR = "named in the plan", "its pull request delivers it", "named by its pull request"
STATUS_WORDS = {"in_progress": "in progress", "needs_review": "needs review"}


@dataclass
class IssueRef:
    key: str
    url: str
    how: str  # NAMED, DELIVERS or NAMED_BY_PR
    title: str | None = None
    status: str | None = None
    priority: int | None = None
    owner: str | None = None  # who owns it, in words
    owner_session: str | None = None
    owner_running: bool = False
    owner_url: str | None = None  # the owner's live conversation in Dispatch
    error: str | None = None  # the issue could not be read

    @property
    def priority_text(self) -> str:
        return f"P{self.priority}" if isinstance(self.priority, int) else "no priority"

    @property
    def status_text(self) -> str:
        return STATUS_WORDS.get(self.status or "", self.status or "status unknown")


def live_url(web: str, session_id: str) -> str:
    return f"{web.rstrip('/')}/agents/{urllib.parse.quote(session_id, safe='')}/live"


def plan_items(scoreboards: dict, now: datetime) -> list[Item]:
    """The items of every stream's plan for today (Pacific), as the page shows them, removed and added ones too, and of
    its plan for this week."""
    today = pacific_day(now)
    out = []
    for entry in (scoreboards or {}).values():
        versions = (entry or {}).get("versions") or []
        out += day_plan(versions, today, now).items + week_plan(versions, now).items
    return out


def wanted_keys(items: list[Item], states: dict[str, dict], project_keys: set[str]) -> set[str]:
    """Every issue key the items name, and every key their pull requests name as their own work in a known project."""
    out = {i.issue for i in items if i.issue}
    for i in items:
        if i.pr and not i.issue:
            out.update(k for k in (states.get(i.pr) or {}).get("named_keys") or [] if k.rsplit("-", 1)[0] in project_keys)
    return out


def slim(issue: dict) -> dict:
    return {k: issue.get(k) for k in ("key", "title", "status", "priority", "assignee", "claim", "route_holder",
                                      "external_links")}


def read_issues(api: Dispatch, keys: set[str]) -> dict[str, dict]:
    """{key: the issue's fields the plan shows}, read in parallel. An unreadable issue is {"error": ...}: the item
    that names it says so, and a key a pull request merely names is then not its issue."""
    out: dict[str, dict] = {}
    if not keys:
        return out

    def one(key: str):
        try:
            return key, slim(api.issue(key))
        except SourceError as e:
            return key, {"error": str(e)[:200]}

    with cf.ThreadPoolExecutor(12) as pool:
        for key, row in pool.map(one, sorted(keys)):
            out[key] = row
    return out


def owner_of(issue: dict, agents: dict, web: str) -> tuple[str | None, str | None, bool, str | None]:
    """(owner in words, session id, whether that session runs now, its live conversation URL)."""
    actor = (issue.get("claim") or {}).get("actor") or {}
    if actor.get("kind") == "session" and actor.get("id"):
        sid = actor["id"]
        title = (agents.get(sid) or {}).get("title") or (actor.get("origin") or {}).get("session_title")
        return title or f"session {sid[:8]}", sid, sid in agents, live_url(web, sid)
    if actor.get("id"):
        return str(actor["id"]), None, False, None
    sid = issue.get("route_holder")
    if isinstance(sid, str) and sid:
        title = (agents.get(sid) or {}).get("title")
        return title or f"session {sid[:8]}", sid, sid in agents, live_url(web, sid)
    if issue.get("assignee"):
        return str(issue["assignee"]), None, False, None
    return None, None, False, None


def pick(item: Item, states: dict[str, dict], issues: dict[str, dict]) -> tuple[str, str] | None:
    """(key, how) of the item's issue, or None."""
    if item.issue:
        return item.issue, NAMED
    if not item.pr:
        return None
    st = states.get(item.pr) or {}
    named = [k for k in st.get("named_keys") or [] if k in issues and not issues[k].get("error")]
    url = st.get("url")
    linked = [k for k in named if url and url in delivering(issues[k])]
    if linked:
        return linked[0], DELIVERS
    if len(named) == 1:
        return named[0], NAMED_BY_PR
    return None


def resolve(item: Item, states: dict[str, dict], issues: dict[str, dict], agents: dict, web: str) -> IssueRef | None:
    picked = pick(item, states, issues)
    if picked is None:
        return None
    key, how = picked
    url = f"{web.rstrip('/')}/issues/{urllib.parse.quote(key)}"
    issue = issues.get(key)
    if issue is None or issue.get("error"):
        return IssueRef(key, url, how, error=(issue or {}).get("error") or "not read yet")
    owner, sid, running, live = owner_of(issue, agents, web)
    return IssueRef(key, url, how, issue.get("title"), issue.get("status"), issue.get("priority"), owner, sid, running,
                    live)
