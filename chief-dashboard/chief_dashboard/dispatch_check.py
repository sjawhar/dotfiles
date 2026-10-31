"""chief-dashboard dispatch-check: a project's Dispatch work that needs its lead or Sami, one line each.

Ported from the OSS Verify lead's `bin/ossv-dispatch-check` (theorem-labs/oss-verify-proofs-internal, branch `ops`),
which hard-coded the OSSV project, two INFRA issues and one audit table; here they are arguments, and `--all-streams`
runs it for the project of every open issue labelled `stream` (the dashboard's stream discovery).

Reads only, over Dispatch's HTTP API with the dashboard's client (`sources.Dispatch`, the machine's Envoy config): the
open `stream` issues and each one's latest scoreboard.json (for its lead), the project's issue list, the live sessions,
the open asks of the project and of each extra issue's project, then for each open issue of the project and each extra
issue the issue and its last 200 events, the parents of those issues, each claimed open issue's plan, and the audit
table. For each of Sami's comments and ask answers that nothing on its own issue followed, it also reads the comment or
ask (its replies, whether it is resolved) and what cites it from any issue (GET /api/v1/references, the `Referenced by`
of `dispatch read`). GitHub GraphQL, with the GitHub App token `gh-app-token agent --owner ORG` mints, gives the pull
requests of ORG that name each issue in triage or in_progress, plus the pull requests linked on the issue. Prints:

  DISPATCH-UNOWNED        an issue of the project in triage or in_progress, or an extra issue that is not done, with no
                          claim or with a claim whose session is not running; a release-queue issue in triage
                          (`RELEASE_QUEUE_TITLES`), or a child of a running Legion architect's claimed issue (the tree
                          runs it), is not one. A LEGION claim is never judged by process liveness: it is the tree's
                          identity, held for the tree's whole life and released only when the tree finishes, so
                          Dispatch shows it as "not running" whenever the session is suspended, waiting for a slot or
                          its pod is gone (INFRA Legion controller, 2026-10-27 07:29Z). On 2026-10-27 that printed 181
                          of 504 LINUX lines, every one of them expected: all LINUX pods were stopped at 23:33Z on
                          Sami's order. A legion-labelled issue whose claim is not live prints one DISPATCH-LEGION-
                          PARKED summary line for the project, naming the count and the daemon state to read
                          (`GET $LEGION_DAEMON_URL/legion/v1/state`: issues.<KEY>.phase and
                          workers.<role>.claim.state; `held` or a failed claim is a stall, a suspended worker under a
                          waiting tree is parking), not one line per issue;
  DISPATCH-QUIET          a claimed issue of the project that is not done or iceboxed, with no comment, message, ask,
                          status change or document version in the last 24 hours. An issue in `needs_review` whose
                          every linked delivering pull request is still open is NOT quiet: it waits on a reviewer or
                          a maintainer who is not ours, and an hourly row there only makes its lane answer each hour
                          to say the same thing. Link the pull request with `dispatch issue-update --external-link`
                          to get that treatment; a blocker named only in prose cannot be read;
  DISPATCH-OVERDUE        (with --audit) a row of the audit table (column 'Delivered by (UTC)') whose latest date has
                          passed and that is not done. A row is done when every issue key it names is done, or when its
                          'Delivered by' cell starts with 'done', 'delivered' or '~~'. A row with no key stays overdue
                          until the lead marks the cell that way. A date with no time is due at 23:59Z;
  DISPATCH-WAITING-ON-US  on an issue of the project or an extra issue: an open ask whose turn is an agent's;
                          a comment by Sami, not on an ask, that is not resolved, that no session replied to (in its
                          thread, or by a reply the comment lists) and that no session event on any issue cites;
                          an answer by him to an ask that is no longer open, followed by no event from the session that
                          opened the ask, no session reply in the ask's thread and no session event on any issue that
                          cites the ask; a message by him that no session comment, message or ask on the issue followed.
                          A citation counts as a session's when it comes from a session's comment, message or ask, or
                          from a document or issue (which only sessions write);
                          Each of these rows is RE-DRIVEN, not only printed (`redrive.py`): it gets an executor, one
                          message and a time to acknowledge by, and the line ends with `| drive: ...` saying what
                          was done. The measurement that forced this: on 2026-10-29 the 13:10Z run printed 15 such
                          rows, a person cleared all of them by hand in 13 minutes at 13:23Z, and the 17:10Z run
                          printed the same 12 INFRA rows;
  DISPATCH-PAST-ACK       a driven row with no reply in its thread after the time its executor was given. This is
                          the count to drive to zero, not the raw waiting count. The row is driven again;
  DISPATCH-NO-EXECUTOR    a waiting row that no session can take: nobody opened it, nobody claims the issue and its
                          stream names no live lead. It is listed for the chief to place by hand, never dropped;
  DISPATCH-STATUS         an issue in triage while work runs on it (a live claim, an agent comment or message in the
                          last 24 hours, or an open pull request DELIVERING it; agent comments on a release-queue issue
                          in triage do not count): it should be in_progress; and one
                          in in_progress whose delivering pull requests (closed unmerged ones aside) are all open,
                          not draft and green (should be needs_review) or all merged (should be testing or done).
                          A pull request delivers an issue only when the issue links it (Dispatch records that as
                          an `external_links` entry, what `dispatch issue-update --external-link` writes: "the pull
                          request that delivers it"). A pull request that merely names the key is tooling or
                          context and moves no status. The check is also skipped for an issue whose spec sets its
                          own status rule: a spec document (slug `spec`, else `spec-*`) with a `## Status` heading
                          or a line saying the status moves on something (`SPEC_SETS_STATUS`). A GitHub read that
                          failed prints `DISPATCH-STATUS check NOT READ (...)` and the rest still runs;
  DISPATCH-NO-PLAN        a claimed issue of the project that is not done or iceboxed with no plan document (slug
                          plan-md, else plan-*), or whose plan states neither a date (YYYY-MM-DD, MM-DD, 'Oct 27'
                          or 'due') nor its work items with a state (a table with an item column and a state
                          column, and a state on each item row);
  DISPATCH-SUMMARY        the counts, so a run that found nothing still shows it ran.

Each finding ends with `| owner: ...`, who must act: the claim holder when a running session (or a person) holds the
issue's claim; else the stream it belongs to (the first issue labelled `stream` among the issue and its parents) and
that stream's lead session when the stream's scoreboard names one; else `no owner`. An audit row's issue is the first
key it names, else the audit's own issue. `--owner PREFIX` prints only the findings whose owner session id starts with
PREFIX (a lead runs it for itself); the summary then counts those.

The re-drive is on by default: it sends over Envoy to agent sessions only, never to a person, and keeps its ledger
at `$DASHBOARD_STATE_DIR/redrive.json` (`--redrive-state`). `--redrive-dry-run` names the executor and the time for
each row and sends and records nothing; `--no-redrive` prints the rows only, and the summary then ends `re-drive
off`, so a zero PAST-ACK count is never read as "nothing is past its time". `--ack-minutes` sets how long an
executor has to reply. A session the agent registry does not list is looked up in this box's tmux panes and its
pane is read for the pause screen before it is passed over: a paused lane keeps the row and gets the message.
Nothing here calls a session dead, and nothing restarts one.

`--as-of TIME` judges the events, replies, resolutions and citations as they stood at TIME (later ones are dropped,
ages count from TIME); claims, live sessions, open asks, plans, pull requests and statuses are still read as they are
now.

Exits non-zero when a Dispatch read fails (for one project of several, the others still print)."""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .redrive import ACK_MINUTES, NOT_LEADS, STATE_DIR, Driver, Ledger, Wait
from .sources import Dispatch, SourceError

SAMI = "sami@theorem.dev"
QUIET = dt.timedelta(hours=24)
EVENTS = 200
# The person the check runs for: an issue assigned to anyone else is that person's, never unowned.
OPERATOR = os.environ.get("DASHBOARD_OPERATOR", "sami@theorem.dev")
KINDS = ("UNOWNED", "QUIET", "OVERDUE", "WAITING-ON-US", "PAST-ACK", "NO-EXECUTOR", "STATUS", "NO-PLAN",
         "LEGION-PARKED", "STALE-NUMBER")
# The summary always carries these, so a run that found nothing still shows every check ran, and a re-drive that
# matched no row says so instead of printing nothing. PAST-ACK is the number to drive to zero: the rows whose time
# to acknowledge has passed. The rest are about Legion's claim model and the scoreboards rather than this project's
# issues, and appear only when they happen.
SUMMARY_ALWAYS = ("UNOWNED", "QUIET", "OVERDUE", "WAITING-ON-US", "PAST-ACK", "NO-EXECUTOR", "STATUS", "NO-PLAN")
# One coaching tick. A stream's main number older than this on its scoreboard is read by everyone downstream as the
# stream's current state: on 2026-10-27 the 07:30Z round printed task making's 01:35Z value while the lead's own
# 07:05Z reading sat in its log, and instructed the lead to do work it had already done.
MAIN_NUMBER_MAX_AGE = dt.timedelta(hours=3)
SCOREBOARD_SLUG = "scoreboard-json"
GH_APP_TOKEN = Path(os.environ.get("DASHBOARD_GH_APP_TOKEN", Path.home() / ".dotfiles" / "shims" / "gh-app-token"))

DATE = re.compile(r"(?<![\d-])(?:(\d{4})-)?(\d\d)-(\d\d)(?:\s+(\d\d):(\d\d))?(?![\d-])")
PLAN_DATE = re.compile(r"\b\d{4}-\d\d-\d\d\b|\b1[0-2]-[0-3]\d\b|\b(?:Oct|Nov|Dec)[a-z]* \d{1,2}\b|\bdue\b", re.I)
# A plan that states its work items, each with a state, needs no date: Sami to the verified-grader lead, 2026-10-29
# 19:04Z (dispatch://BET-59, comment e4f28bb2): "I'm not interested in you setting dates ... design the work that
# needs to happen, the dependencies, and then work towards it." A work graph is a markdown table with an item column
# and a state column, and a state on every item row (a dependency column where items depend on others).
PLAN_GRAPH_ITEM = re.compile(r"^(?:#\s*|no\.?|item|work|task|step|slice|part|deliverable)s?$", re.I)
PLAN_GRAPH_STATE = re.compile(r"^(?:state|status)$", re.I)
# Titles of issues that wait in triage, unclaimed, for a release service to hand them to Legion; agents comment on
# them before release on purpose (LINUX lead, 2026-10-26 11:18Z and 11:23Z: the owner-release service releases each
# k=60 proof part, hub parts included, then the LINUX Legion daemon claims it and moves it out of triage). Released,
# they are checked like any issue.
RELEASE_QUEUE_TITLES = ("Linux proof part ", "Linux proof model part ", "Linux proof hub part ")
# The title a Legion architect session carries ("Legion architect · INFRA-156").
LEGION_ARCHITECT = "Legion architect"


@dataclass
class Scope:
    """One run: a project, the issues of other projects it also covers, its audit table, the streams it serves."""

    project: str
    issues: list[str] = field(default_factory=list)
    audit: tuple[str, str] | None = None
    streams: list[str] = field(default_factory=list)


# --- reading -------------------------------------------------------------------------------------------------------


def names_key(pr: dict, key: str) -> bool:
    """Whether pull request PR is KEY's work: its title or branch names the key, or its body says it closes, fixes,
    resolves, implements or is part of the key (a body that only mentions the key as context does not count).

    The key itself, not "linux-4.19.y" for LINUX-4: no word character, "-" or "." before it, no word character or
    ".<word>" after it (a sentence's final "." is fine); titles and bodies match its case, branch names (usually
    lowercase) any case."""
    exact = rf"(?<![\w.-]){re.escape(key)}(?!\w|\.\w)"
    claims = rf"\b(?i:close[sd]?|fix(?:e[sd])?|resolve[sd]?|implements?|part of)\s*:?\s*(?:dispatch://)?{exact}"
    return bool(re.search(exact, pr["title"]) or re.search(exact, pr["headRefName"], re.IGNORECASE)
                or re.search(claims, pr["body"] or ""))


PR_URL = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/\d+$")
# A spec that sets its own status rule: a "## Status" heading, or a sentence saying the status moves on something.
SPEC_SETS_STATUS = re.compile(r"(?im)^#{1,6}\s*status\b|\bstatus\b[^.\n]{0,60}\bmoves?\b[^.\n]{0,40}\b(on|when|by)\b")


def delivering(issue: dict) -> list[str]:
    """The pull requests the issue links as delivering it. Dispatch records that link in `external_links`, what
    `dispatch issue-update --external-link` writes ("the pull request that delivers it"); an entry is a URL or a
    {url, kind} object. A pull request that only names the key is tooling or context, and is not here."""
    out = []
    for link in issue.get("external_links") or []:
        url = link if isinstance(link, str) else (link or {}).get("url") if isinstance(link, dict) else None
        if isinstance(url, str) and PR_URL.match(url):
            out.append(url)
    return out


def spec_artifact(issue: dict) -> dict | None:
    """The issue's spec document: slug `spec` first, else any `spec-*` one; None when it has none."""
    specs = sorted((a for a in issue.get("artifacts", []) if a["slug"].startswith("spec")),
                   key=lambda a: a["slug"] != "spec")
    return specs[0] if specs else None


def _spec_sets_status(api: Dispatch, issue: dict) -> bool:
    """Whether the issue's spec states its own status rule, so pull-request state must not move its status."""
    spec = spec_artifact(issue)
    if spec is None:
        return False
    return bool(SPEC_SETS_STATUS.search(_get(api, f"artifacts/{spec['id']}/text")["markdown"]))


def plan_artifact(issue: dict) -> dict | None:
    """The issue's plan document: slug plan-md first, else any plan-* one; None when it has none."""
    plans = sorted((a for a in issue.get("artifacts", []) if a["slug"].startswith("plan")),
                   key=lambda a: a["slug"] != "plan-md")
    return plans[0] if plans else None


def _get(api: Dispatch, path: str, **query):
    q = f"?{urllib.parse.urlencode(query)}" if query else ""
    err: Exception | None = None
    for _ in (1, 2):
        try:
            return api.get(f"/api/v1/{path}{q}")
        except SourceError as e:
            err = e
    raise SourceError(str(err)[:300])


def _read_issue(api: Dispatch, key: str) -> dict:
    issue = _get(api, f"issues/{key}")
    issue["events"] = _get(api, f"issues/{key}/events", after=max(0, issue["last_seq"] - EVENTS), limit=EVENTS)
    return issue


def is_work_graph(markdown: str) -> bool:
    """Whether the plan states its work items with a state for each: a markdown table with an item column and a
    state column, and at least two item rows whose state cell says something. The dependency column such a plan
    carries (`Depends on`) is not required: a plan whose items depend on nothing still lists none."""
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in markdown.splitlines()
            if ln.strip().startswith("|")]
    head: tuple[int, int] | None = None
    items = 0
    for row in rows:
        if all(re.fullmatch(r":?-{3,}:?", c) for c in row if c):  # the header's own separator row
            continue
        item = next((i for i, c in enumerate(row) if PLAN_GRAPH_ITEM.match(c)), None)
        state = next((i for i, c in enumerate(row) if PLAN_GRAPH_STATE.match(c)), None)
        if item is not None and state is not None:
            head, items = (item, state), 0
            continue
        if head is None or len(row) <= max(head):
            continue
        if row[head[0]] and row[head[1]]:
            items += 1
            if items >= 2:
                return True
    return False


def _plan_of(api: Dispatch, issue: dict) -> tuple[str | None, bool]:
    """(slug, states its work) for the issue's plan document (`plan_artifact`), or (None, False). A plan states its
    work by a date it can be held to, or by a work graph (`is_work_graph`)."""
    plan = plan_artifact(issue)
    if plan is None:
        return None, False
    text = _get(api, f"artifacts/{plan['id']}/text")["markdown"]
    return plan["slug"], bool(PLAN_DATE.search(text)) or is_work_graph(text)


def _audit_text(api: Dispatch, audit: tuple[str, str]) -> str:
    key, slug = audit
    art = next((a for a in _get(api, f"issues/{key}")["artifacts"] if a["slug"] == slug), None)
    if art is None:
        raise SourceError(f"no document {slug} on {key}")
    return _get(api, f"artifacts/{art['id']}/text")["markdown"]


def audit_rows(doc: str) -> list[dict]:
    """Table rows of the audit as dicts by header, the header row found by its 'Delivered by' column."""
    lines = [ln for ln in doc.splitlines() if ln.startswith("|")]
    head = next((i for i, ln in enumerate(lines) if "Delivered by" in ln), None)
    if head is None:
        raise SourceError("audit table has no 'Delivered by' column")

    def cells(ln: str) -> list[str]:
        return [c.strip() for c in re.split(r"(?<!\\)\|", ln)[1:-1]]

    names = cells(lines[head])
    return [dict(zip(names, cells(ln), strict=False)) for ln in lines[head + 1:] if not re.match(r"\|\s*-", ln)]


def read_streams(api: Dispatch) -> dict[str, dict]:
    """{stream issue key: {project, lead, main}} for every open issue labelled `stream`. `lead` is the {title, session}
    its latest scoreboard.json names, or None (no scoreboard, or one that names no lead or cannot be read). `main` is
    {name, at} for the newest value of that scoreboard's main number, or None — the check reads a stream's number from
    the scoreboard, so a number that lives only in the lead's own log is a number the organization cannot see."""

    def board(key: str) -> tuple[dict | None, dict | None]:
        art = next((a for a in _get(api, f"issues/{key}").get("artifacts", []) if a["slug"] == SCOREBOARD_SLUG), None)
        if not art or not art.get("versions"):
            return None, None
        try:
            data = api.file_version(key, SCOREBOARD_SLUG, max(v["number"] for v in art["versions"]))
        except SourceError:
            return None, None
        if not isinstance(data, dict):
            return None, None
        found = data.get("lead")
        lead = found if isinstance(found, dict) and found.get("session") else None
        main = None
        for n in data.get("numbers") or ():
            if not isinstance(n, dict) or n.get("role") != "main":
                continue
            ats = [v.get("at") for v in (n.get("values") or ()) if isinstance(v, dict) and v.get("at")]
            if ats:
                # `cadence` says what produces the next reading, for a number that is not read on a clock. Rocq
                # speed's is a Taiga grade of 10 fixed trees on two images, 3-4 h, so re-reading an unchanged image
                # measures noise: its scoreboard says "on each grader image or Rocq build change" (2026-10-29).
                # Without this the check flagged a number that was correctly unchanged, which is a check that cannot
                # tell a real state from its own definition.
                cadence = n.get("cadence")
                main = {"name": str(n.get("name") or n.get("id") or "main"), "at": max(ats),
                        "cadence": str(cadence) if isinstance(cadence, str) and cadence.strip() else None}
            break
        return lead, main

    issues = api.stream_issues()
    with cf.ThreadPoolExecutor(8) as pool:
        boards = list(pool.map(lambda i: board(i["key"]), issues))
    return {i["key"]: {"project": i.get("project") or i["key"].rsplit("-", 1)[0], "lead": ld, "main": mn}
            for i, (ld, mn) in zip(issues, boards, strict=True)}


class GitHub:
    """The pull requests of one GitHub organization that name Dispatch issues, read as the org's GitHub App."""

    PR_FIELDS = ("... on PullRequest{url state merged isDraft title body headRefName "
                 "commits(last:1){nodes{commit{statusCheckRollup{state}}}}}")

    def __init__(self, org: str = "theorem-labs") -> None:
        self.org = org
        self._token: str | None = None

    def token(self) -> str:
        if self._token is None:
            try:
                self._token = subprocess.run([str(GH_APP_TOKEN), "agent", "--owner", self.org], capture_output=True,
                                             text=True, timeout=15, check=True).stdout.strip()
            except (OSError, subprocess.SubprocessError) as e:
                raise RuntimeError(f"app token: {' '.join(str(e).split())[:160]}") from e
        return self._token

    def gql(self, fields: dict[str, str]) -> dict:
        """One GraphQL query of aliased FIELDS ({alias: field text}); its data by alias."""
        q = "{" + " ".join(f"{a}:{f}" for a, f in fields.items()) + "}"
        req = urllib.request.Request("https://api.github.com/graphql", data=json.dumps({"query": q}).encode(),
                                     headers={"Authorization": f"bearer {self.token()}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
        if d.get("errors"):
            raise RuntimeError(f"graphql: {d['errors'][0].get('message', '')[:160]}")
        return d["data"]

    def __call__(self, keys: list[str], linked: dict[str, list[str]]) -> dict[str, dict[str, dict]]:
        """{key: {url: pr}}: the org's pull requests that are the key's work (`names_key`), one search per key, five
        keys per query, the queries in parallel; plus the pull requests LINKED on each issue."""
        chunks = [keys[i:i + 5] for i in range(0, len(keys), 5)]

        def query(ks: list[str]) -> dict[str, str]:
            return {f"k{i}": f'search(type:ISSUE,first:30,query:"org:{self.org} is:pr \\"{k}\\""){{nodes{{{self.PR_FIELDS}}}}}'
                    for i, k in enumerate(ks)}

        found: dict[str, dict[str, dict]] = {}
        if chunks:
            with cf.ThreadPoolExecutor(len(chunks)) as pool:
                for ks, d in zip(chunks, pool.map(lambda ks: self.gql(query(ks)), chunks), strict=True):
                    for i, k in enumerate(ks):
                        found[k] = {p["url"]: p for p in d[f"k{i}"]["nodes"] if p and names_key(p, k)}
        extra = [(k, u) for k, us in linked.items() for u in us if u not in found.get(k, {})]
        if extra:
            d = self.gql({f"l{i}": f'resource(url:"{u}"){{{self.PR_FIELDS}}}' for i, (k, u) in enumerate(extra)})
            for i, (k, u) in enumerate(extra):
                if d[f"l{i}"]:
                    found.setdefault(k, {})[u] = d[f"l{i}"]
        return found


PrReader = Callable[[list[str], dict[str, list[str]]], dict[str, dict[str, dict]]]


def _follow_ups(api: Dispatch, kind: str, key: str, node_id: str) -> tuple[list[tuple[str, str]], dict | None]:
    """(time, what) of every session act on a comment or ask outside its own issue's events: its resolution (comments),
    each session reply, and each citation of it from a session's comment, message or ask or from a document or issue;
    with the node's own author, which is the session the re-drive sends an unexecuted ask back to."""
    out: list[tuple[str, str]] = []
    node = _get(api, f"{kind}s/{node_id}")
    if kind == "comment" and node["comment"].get("resolved_at"):
        out.append((node["comment"]["resolved_at"], "resolved"))
    for r in node.get("replies") or []:
        if (r.get("author") or {}).get("kind") == "session":
            out.append((r["created_at"], f"session reply {r.get('id', '')}"))
    edges = _get(api, "references", to=f"dispatch://{key}/{kind}/{node_id}")["edges"]
    for e in edges:
        if e.get("direction", "in") != "in":
            continue
        n = e["node"]
        if n["kind"] in ("comment", "message", "ask"):
            author = (_get(api, f"{n['kind']}s/{n['id']}").get(n["kind"]) or {}).get("author") or {}
            if author.get("kind") != "session":
                continue
        out.append((e["created_at"], f"cited by {n.get('ref') or n['id']}"))
    return out, (node.get(kind) or {}).get("author")


def _ask_author(api: Dispatch, ask_id: str) -> dict | None:
    """The session that opened an ask, for an open ask whose follow-ups were not read."""
    return ((_get(api, f"asks/{ask_id}") or {}).get("ask") or {}).get("author")


def read(api: Dispatch, scope: Scope, github: PrReader, project_keys: list[str], streams: dict[str, dict],
         now: dt.datetime) -> dict:
    """Everything the checks look at, read in parallel. Raises SourceError when a Dispatch read fails."""
    key_re = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(project_keys))) + r")-\d+\b")
    ask_projects = sorted({scope.project} | {k.rsplit("-", 1)[0] for k in scope.issues})
    with cf.ThreadPoolExecutor(24) as pool:
        f_list = pool.submit(_get, api, "issues", project=scope.project)
        f_live = pool.submit(_get, api, "agents")
        f_asks = [pool.submit(_get, api, "asks/open", project=p) for p in ask_projects]
        f_audit = pool.submit(_audit_text, api, scope.audit) if scope.audit else None
        issues = {i["key"]: i for i in f_list.result()}
        open_keys = [k for k, v in issues.items() if v["status"] not in ("done", "icebox")]
        pr_keys = [k for k in open_keys if issues[k]["status"] in ("triage", "in_progress")]
        rows = audit_rows(f_audit.result()) if f_audit else []
        extras = [k for k in scope.issues if k not in open_keys]
        audit_keys = sorted({k for r in rows for k in key_re.findall(" ".join(r.values()))} - set(issues) - set(extras))
        keys = open_keys + extras
        reads = dict(zip(keys, pool.map(lambda k: _read_issue(api, k), keys), strict=True))
        others = dict(zip(audit_keys, pool.map(lambda k: _get(api, f"issues/{k}"), audit_keys), strict=True))
        if scope.audit and scope.audit[0] not in issues | reads | others:
            others[scope.audit[0]] = _get(api, f"issues/{scope.audit[0]}")
        # Each issue's parents, up to the root, for the stream it belongs to.
        meta = {k: {"parent": v.get("parent"), "labels": v.get("labels") or [], "claim": v.get("claim")}
                for k, v in (issues | others | reads).items()}
        while missing := sorted({m["parent"] for m in meta.values() if m["parent"] and m["parent"] not in meta}):
            for k, v in zip(missing, pool.map(lambda k: _get(api, f"issues/{k}"), missing), strict=True):
                meta[k] = {"parent": v.get("parent"), "labels": v.get("labels") or [], "claim": v.get("claim")}
        claimed = [k for k in open_keys if reads[k]["claim"]]
        plans = dict(zip(claimed, pool.map(lambda k: _plan_of(api, reads[k]), claimed), strict=True))
        live = {a["session_id"]: a["title"] for a in f_live.result()}
        asks = [a for f in f_asks for a in f.result()["asks"]]
        open_ask_ids = {a["id"] for a in asks}
        # What followed Sami's comments and answers outside their issue's events, read only for those still unfollowed.
        todo = [(kind, key, node_id) for key, r in reads.items()
                for kind, node_id in unfollowed(_until(r["events"], now), open_ask_ids)]
        follow = dict(zip([t[2] for t in todo], pool.map(lambda t: _follow_ups(api, *t), todo), strict=True))
        remote = {k: v[0] for k, v in follow.items()}
        # Who opened each ask that still waits: the first executor the re-drive names (`redrive.Driver.candidates`).
        ask_authors = {k: v[1] for k, v in follow.items() if v[1]}
        more = [a["id"] for a in asks if a["waiting_on"] == "agent" and a["id"] not in ask_authors]
        ask_authors |= dict(zip(more, pool.map(lambda i: _ask_author(api, i), more), strict=True))
        # Searches stay on triage and in_progress, where a pull request can move a status. The LINKED lookup also
        # covers needs_review, which costs one resource read per linked URL and tells `waits_on_open_pr` whether a
        # silent issue is waiting on a reviewer rather than stalled.
        linked = {k: delivering(reads[k]) for k in open_keys
                  if reads[k]["status"] in ("triage", "in_progress", "needs_review")}
        # An issue whose spec sets its own status rule: its status never follows pull-request state.
        own_rule = dict(zip(pr_keys, pool.map(lambda k: _spec_sets_status(api, reads[k]), pr_keys), strict=True))
        gh_err, prs = "", {}
        try:
            prs = github(pr_keys, {k: v for k, v in linked.items() if v})
        except Exception as e:  # noqa: BLE001 - any GitHub failure is reported on the STATUS line, the rest still runs
            gh_err = " ".join(str(e).split())[:200]
    return {"server": api.server, "issues": issues, "open_keys": open_keys, "pr_keys": pr_keys, "reads": reads,
            "audit_rows": rows, "audit_status": {k: v["status"] for k, v in others.items()}, "plans": plans,
            "prs": prs, "gh_err": gh_err, "live": live, "asks": asks, "key_re": key_re, "meta": meta,
            "streams": streams, "remote": remote, "delivering": linked, "own_status_rule": own_rule,
            "ask_authors": ask_authors}


# --- the checks ----------------------------------------------------------------------------------------------------


def ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.rstrip("Z")[:26]).replace(tzinfo=dt.UTC)


def age(t: dt.datetime, now: dt.datetime) -> str:
    m = int((now - t).total_seconds() // 60)
    return f"{m // 1440}d {m % 1440 // 60}h" if m >= 1440 else f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def _until(events: list[dict], now: dt.datetime) -> list[dict]:
    return [e for e in events if ts(e["created_at"]) <= now]


def actor(a: dict, titles: dict) -> str:
    """A readable name for an event or claim actor: a running session's current title, else the title it had."""
    if a.get("kind") == "session":
        title = titles.get(a["id"]) or (a.get("origin") or {}).get("session_title") or f"session {a['id']}"
        return f"{title} (for {a['owner']})" if a.get("owner") else title
    return a.get("id", "?")


def due_of(cell: str, now: dt.datetime) -> dt.datetime | None:
    """The latest date in a 'Delivered by' cell (MM-DD or YYYY-MM-DD, optional HH:MM), or None. A match that is no
    calendar date ("segments 12-34") is skipped."""
    ds = []
    for y, mo, d, h, mi in DATE.findall(cell):
        try:
            ds.append(dt.datetime(int(y or now.year), int(mo), int(d), int(h or 23), int(mi or 59), tzinfo=dt.UTC))
        except ValueError:
            continue
    return max(ds) if ds else None


def pr_state(p: dict) -> str:
    """merged, closed, draft, green, or open (checks failing, running or absent)."""
    if p["merged"] or p["state"] != "OPEN":
        return "merged" if p["merged"] else "closed"
    roll = ((p["commits"]["nodes"] or [{"commit": {}}])[0]["commit"] or {}).get("statusCheckRollup") or {}
    return "draft" if p["isDraft"] else "green" if roll.get("state") == "SUCCESS" else "open"


def waits_for_release(r: dict) -> bool:
    """Whether the issue is an unreleased release-queue issue (`RELEASE_QUEUE_TITLES`, still in triage)."""
    return r["status"] == "triage" and r["title"].startswith(RELEASE_QUEUE_TITLES)


def waits_on_open_pr(key: str, r: dict, data: dict) -> bool:
    """Whether the issue is in `needs_review` with at least one linked delivering pull request, every one of them
    still open (open, green or draft - not merged and not closed). Such an issue waits on a reviewer or an upstream
    maintainer, so its silence is the wait and not a stall: BET-123 was flagged hourly for six hours while
    rocq-prover/rocq#22607 sat rebased and green, and its lane answered every hour to say so."""
    if r["status"] != "needs_review":
        return False
    delivers = set(data.get("delivering", {}).get(key) or ())
    states = [pr_state(p) for u, p in (data.get("prs", {}).get(key) or {}).items() if u in delivers]
    return bool(states) and all(s in ("open", "green", "draft") for s in states)


def is_session(e: dict) -> bool:
    return e["actor"].get("kind") == "session"


def by_sami(e: dict) -> bool:
    return e["actor"].get("kind") == "user" and e["actor"].get("id") == SAMI


def activity(events: list[dict]) -> list[dict]:
    """The events that count as activity: comments, messages, asks, document versions, and status changes (an
    issue.updated whose status differs from the one before it)."""
    out, status = [], None
    for e in events:
        if e["type"] in ("issue.created", "issue.updated"):
            if e["type"] == "issue.updated" and status is not None and e["payload"].get("status") != status:
                out.append(e)
            status = e["payload"].get("status")
        elif re.match(r"(comment\.created|message\.|ask\.(opened|answered|resolved)|artifact\.(version|created))",
                      e["type"]):
            out.append(e)
    return out


def _followed_here(e: dict, later: list[dict]) -> bool:
    """Whether a session event later on the same issue followed Sami's event E (see the module docstring)."""
    p = e["payload"]
    if e["type"] == "comment.created":
        root = p.get("thread_root_id") or p.get("reply_to") or p["id"]
        return any(x["type"] == "comment.resolved" and x["payload"].get("id") in (p["id"], root) for x in later) or any(
            is_session(x) and x["type"] == "comment.created"
            and root in (x["payload"].get("thread_root_id"), x["payload"].get("reply_to")) for x in later)
    if e["type"] == "ask.answered":
        opener = (p.get("author") or {}).get("id")
        return any(x["actor"].get("id") == opener for x in later) or any(
            is_session(x) and x["type"] == "comment.created" and x["payload"].get("ask_id") == p.get("id")
            for x in later)
    return any(is_session(x) and re.match(r"(comment|message|ask)\.", x["type"]) for x in later)


def _sami_waits(events: list[dict], open_ask_ids: set):
    """(index, event) of each of Sami's comments (not on an ask), answers to asks no longer open, and messages."""
    for i, e in enumerate(events):
        if not by_sami(e):
            continue
        p = e["payload"]
        if e["type"] == "comment.created" and not p.get("ask_id"):
            yield i, e
        elif e["type"] == "ask.answered" and p.get("id") not in open_ask_ids:  # an open one: its open-asks line shows it
            yield i, e
        elif e["type"] == "message.created":
            yield i, e


def unfollowed(events: list[dict], open_ask_ids: set) -> list[tuple[str, str]]:
    """(comment|ask, id) of Sami's comments and answers that nothing later on their own issue followed."""
    return [("comment" if e["type"] == "comment.created" else "ask", e["payload"]["id"])
            for i, e in _sami_waits(events, open_ask_ids)
            if e["type"] != "message.created" and not _followed_here(e, events[i + 1:])]


def waiting_on_us(key: str, events: list[dict], open_ask_ids: set, remote: dict, authors: dict, server: str,
                  now: dt.datetime) -> list[Wait]:
    """Sami's comments, ask answers and messages that no session followed (see the module docstring), as the rows the
    re-drive works on: each carries the node it is about and the session that opened it, when there is one."""
    rows: list[Wait] = []
    for i, e in _sami_waits(events, open_ask_ids):
        p = e["payload"]
        if _followed_here(e, events[i + 1:]):
            continue
        at = ts(e["created_at"])
        if any(at < ts(t) <= now for t, _ in remote.get(p.get("id"), [])):
            continue
        waited, node = age(at, now), p.get("id") or ""
        if e["type"] == "comment.created":
            where = f" on {p['artifact_name']}" if p.get("artifact_name") else ""
            body = " ".join((p.get("body") or "").split())
            rows.append(Wait(key, "comment", node, waited=waited, question=body,
                             text=f"{key} comment{where} by sami {waited} ago, unanswered and unresolved: "
                                  f"{body[:180]} ({server}/issues/{key}/comments/{node})"))
            continue
        if e["type"] == "ask.answered":
            ans = p.get("answer") or {}
            text = f" {ans['text']}" if ans.get("text") else ""
            answer = f"{' / '.join(ans.get('selected') or [])}{text}"
            question = " ".join((p.get("question") or "").split())
            what = f'answer "{answer}" to: {question[:120]}'
            since = "no event from the session that asked, no reply in the ask's thread and no session citation since"
            kind, author = "ask", authors.get(node)
        else:
            answer, question, author, kind = "", " ".join((p.get("body") or "").split()), None, "message"
            what = f"message: {question[:160]}"
            since = "no agent comment, message or ask since"
        rows.append(Wait(key, kind, node, waited=waited, author=author, question=question, answer=answer,
                         text=f"{key} {what[:260]} (sami, {waited} ago; {since})"))
    return rows


Finding = tuple[str | None, str]  # (the issue it is about, the text)


def evaluate(data: dict, scope: Scope, now: dt.datetime) -> tuple[dict[str, list[Finding]], list[Wait]]:
    """The findings of each check and the rows that wait on us, from what `read` returned; events after NOW are
    ignored. The rows are what the re-drive works on (`redrive.Driver`)."""
    server, live, prs = data["server"], data["live"], data["prs"]
    reads = {k: {**r, "events": _until(r["events"], now)} for k, r in data["reads"].items()}
    out: dict[str, list[Finding]] = {k: [] for k in KINDS}
    status = ({k: v["status"] for k, v in data["issues"].items()} | {k: r["status"] for k, r in reads.items()}
              | data["audit_status"])
    in_project = lambda k: k.startswith(scope.project + "-")  # noqa: E731
    in_scope = lambda k: in_project(k) or k in scope.issues  # noqa: E731

    def claim(r: dict) -> tuple[str | None, bool]:
        """(holder name, live) of an issue's claim; (None, False) when unclaimed."""
        c = r.get("claim")
        if not c:
            return None, False
        a = c["actor"]
        return actor(a, live), a.get("kind") != "session" or a["id"] in live

    def under_live_architect(key: str) -> bool:
        """Whether a running Legion architect claims one of KEY's ancestors. Legion runs a tree's children under its
        architect's claim: it releases them from triage and its phase workers carry no claim of their own (LINUX lead
        and the INFRA Legion controller, 2026-10-26 11:2xZ). Under any other claim the child stays unowned."""
        seen: set[str] = set()
        k = data["meta"].get(key, {}).get("parent")
        while k and k not in seen:
            seen.add(k)
            c = (data["meta"].get(k) or {}).get("claim")
            a = (c or {}).get("actor") or {}
            if a.get("kind") == "session" and a.get("id") in live:
                titles = (live[a["id"]] or "", (a.get("origin") or {}).get("session_title") or "")
                if any(t.startswith(LEGION_ARCHITECT) for t in titles):
                    return True
            k = (data["meta"].get(k) or {}).get("parent")
        return False

    def in_legion_tree(key: str) -> bool:
        """Whether KEY or an ancestor carries the `legion` label. Only a person, the Legion controller or the daemon
        writes such an issue's status: a status out of Legion's flow parks a child and stops its workers
        (skill://dispatch/references/issues.md; the Verified Linux owner line, 2026-10-27 00:17Z)."""
        seen: set[str] = set()
        k: str | None = key
        while k and k not in seen:
            seen.add(k)
            m = data["meta"].get(k) or {}
            if "legion" in m.get("labels", []):
                return True
            k = m.get("parent")
        return False

    legion_parked: list[str] = []
    for key, r in reads.items():
        holder, is_live = claim(r)
        others = (r.get("assignee") or OPERATOR) != OPERATOR  # another person's issue: theirs, not ours to own
        if (key in scope.issues or r["status"] in ("triage", "in_progress")) and r["status"] != "done" and not is_live \
                and not others and not waits_for_release(r) and not under_live_architect(key):
            # A Legion claim outlives its process by design, so "not running" says nothing about whether the tree
            # stalled; only the daemon's state does. Count these, print one line per project, and never ask a lead to
            # own an issue Legion still holds.
            if holder is not None and in_legion_tree(key):
                legion_parked.append(key)
            else:
                why = "no claim" if holder is None else f"claim by {holder} since {r['claim']['at'][:16]}Z: not running"
                out["UNOWNED"].append((key, f"{key} [{r['status']}] {r['title'][:90]} ({why})"))
        if in_project(key) and holder is not None and r["status"] not in ("done", "icebox") \
                and not waits_on_open_pr(key, r, data):
            act = activity(r["events"])
            last = act[-1] if act else None
            if last is None or now - ts(last["created_at"]) > QUIET:
                when = (f"last {last['type']} {age(ts(last['created_at']), now)} ago" if last
                        else f"no activity in its last {EVENTS} events")
                out["QUIET"].append((key, f"{key} [{r['status']}] {r['title'][:90]} (claimed by {holder}; {when})"))

    open_ask_ids = {a["id"] for a in data["asks"]}
    waits: list[Wait] = []
    for a in data["asks"]:
        k = ((a.get("owner") or {}).get("issue") or {}).get("key", "")
        if a["waiting_on"] == "agent" and in_scope(k):
            waited = age(dt.datetime.now(dt.UTC) - dt.timedelta(seconds=a["age_seconds"]), dt.datetime.now(dt.UTC))
            question = " ".join(a["question"].split())
            waits.append(Wait(k, "ask", a["id"], waited=waited, author=data["ask_authors"].get(a["id"]),
                              question=question,
                              text=f"{k} ask waiting on an agent for {waited}: "
                                   f"{question[:180]} ({server}{a['ref']})"))
    for key, r in reads.items():
        waits += waiting_on_us(key, r["events"], open_ask_ids, data["remote"], data["ask_authors"], server, now)
    out["WAITING-ON-US"] = [(w.key, w.text) for w in waits]

    for row in data["audit_rows"]:
        cell = row.get("Delivered by (UTC)", "")
        n = row.get("\\#", row.get("#", "?"))
        due = due_of(cell, now)
        if due is None:
            continue
        keys = data["key_re"].findall(" ".join(row.values()))
        done = re.match(r"(?i)\s*(~~|done\b|delivered\b)", cell) or (keys and all(status.get(k) == "done" for k in keys))
        if due < now and not done:
            st = ", ".join(f"{k} {status.get(k, '?')}" for k in keys) or "no issue key: mark the cell 'done' when delivered"
            out["OVERDUE"].append((keys[0] if keys else scope.audit[0],
                                   f"#{n} {row.get('What you asked', '')[:70]} (owner {row.get('Owner', '?')}; due "
                                   f"{due:%m-%d %H:%M}Z, {age(due, now)} ago; \"{cell[:90]}\"; {st})"))

    # Status that does not match the work (sdd.md section 6: an issue left at triage while work runs is a defect).
    if data["gh_err"]:
        out["STATUS"].append((None, f"check NOT READ for pull requests ({data['gh_err']}); claims and comments still read"))

    def short(us) -> str:
        return " ".join(u.replace("https://github.com/", "").replace("/pull/", "#") for u in sorted(us))

    # An issue with open children (an index or umbrella) stays in progress while they run, whatever its own PRs did.
    open_children = {v.get("parent") for v in data["issues"].values() if v.get("status") not in ("done", "icebox")}
    for key in data["pr_keys"]:
        r = reads[key]
        if data.get("own_status_rule", {}).get(key):
            continue  # the issue's spec sets its own status rule; pull-request state says nothing about it
        if in_legion_tree(key):
            continue  # Legion owns the status of every issue in its trees; report nothing an agent may not write
        delivers = set(data.get("delivering", {}).get(key) or ())
        mine = {u: s for u, s in ((u, pr_state(p)) for u, p in prs.get(key, {}).items())
                if u in delivers and s != "closed"}
        if key in open_children and r["status"] == "in_progress":
            continue
        if r["status"] == "triage":
            why = []
            holder, is_live = claim(r)
            if is_live:
                why.append(f"claimed by {holder}")
            talk = [e for e in r["events"] if is_session(e) and re.match(r"(comment|message)\.created", e["type"])
                    and now - ts(e["created_at"]) < QUIET and not waits_for_release(r)]
            if talk:
                why.append(f"agent {talk[-1]['type']} {age(ts(talk[-1]['created_at']), now)} ago")
            opened = [u for u, s in mine.items() if s != "merged"]
            if opened:
                why.append(f"open PR {short(opened)}")
            if why:
                out["STATUS"].append((key, f"{key} [triage] {r['title'][:80]}: work is running ({'; '.join(why)}); "
                                           "should be in_progress"))
        elif r["status"] == "in_progress" and mine and all(s == "green" for s in mine.values()):
            out["STATUS"].append((key, f"{key} [in_progress] {r['title'][:80]}: every delivering PR open and green "
                                       f"({short(mine)}); should be needs_review"))
        elif r["status"] == "in_progress" and mine and all(s == "merged" for s in mine.values()):
            out["STATUS"].append((key, f"{key} [in_progress] {r['title'][:80]}: every delivering PR merged "
                                       f"({short(mine)}); should be testing or done"))

    for key, (slug, stated) in sorted(data["plans"].items()):
        # A Legion issue's plan is its tree: the architect's spec and its waves, written and dated by the daemon's
        # flow, not a hand-written plan document on the issue. Asking for one printed 125 of LINUX's 129 NO-PLAN lines
        # on 2026-10-27, every one of them a `Linux proof part` Legion runs (Verified Linux lead, 07:29Z: only 4 were
        # real). A person's or a lead's own issue still needs a plan that states its work.
        if not stated and not in_legion_tree(key):
            what = ("no plan document" if slug is None else
                    f"plan dispatch://{key}/artifact/{slug} states neither a date nor its work items with a state")
            out["NO-PLAN"].append((key, f"{key} [{reads[key]['status']}] {reads[key]['title'][:80]} ({what}; "
                                        f"claimed by {claim(reads[key])[0]})"))
    if legion_parked:
        out["LEGION-PARKED"].append((legion_parked[0], f"{len(legion_parked)} legion-labelled issues hold a claim "
                                     f"whose session is not running ({short(sorted(legion_parked)[:5])}...). A Legion "
                                     "claim is the tree's identity and outlives its process, so this is parking until "
                                     "the daemon says otherwise: read GET $LEGION_DAEMON_URL/legion/v1/state, "
                                     "issues.<KEY>.phase and workers.<role>.claim.state (held or a failed claim is a "
                                     "stall; a suspended worker under a waiting tree is parking)"))
    for key, st in sorted(data["streams"].items()):
        main = st.get("main")
        if main is None or (st.get("project") or key.rsplit("-", 1)[0]) != scope.project:
            continue  # a stream with no scoreboard reading at all already shows as having no lead
        if now - ts(main["at"]) > MAIN_NUMBER_MAX_AGE:
            if main.get("cadence"):
                continue  # the number says what produces its next reading; its age is not evidence of anything
            out["STALE-NUMBER"].append((key, f"{key} main number '{main['name'][:60]}' last read "
                                             f"{age(ts(main['at']), now)} ago ({main['at'][:16]}Z); post this tick's "
                                             "reading, with its basis, into the number's values, or give the number a "
                                             "`cadence` saying what produces the next one"))
    return out, waits


def owner_of(key: str | None, data: dict) -> tuple[str, str | None]:
    """(who must act on KEY, their session id or None): a running claim holder, else the stream's lead, else none."""
    meta, live = data["meta"], data["live"]
    m = meta.get(key) if key else None
    if m is None:
        return "no owner", None
    c = (m.get("claim") or {}).get("actor")
    if c and (c.get("kind") != "session" or c["id"] in live):
        if c.get("kind") != "session":
            return c.get("id", "?"), None
        return f"{actor(c, live)} ({c['id']})", c["id"]
    seen, k = set(), key
    while k and k not in seen:
        seen.add(k)
        if "stream" in meta.get(k, {}).get("labels", []) or k in data["streams"]:
            lead = (data["streams"].get(k) or {}).get("lead") or {}
            if lead.get("session") and lead["session"] not in NOT_LEADS:
                return f"stream {k} lead {lead.get('title', '?')} ({lead['session']})", lead["session"]
            return f"stream {k} (its scoreboard names no lead)", None
        k = meta.get(k, {}).get("parent")
    return "no owner", None


def report(out: dict[str, list[Finding]], data: dict, scope: Scope, now: dt.datetime,
           owner: str | None = None, suffix: str = "") -> list[str]:
    """The printed lines: one per finding with its owner, then the summary. OWNER keeps only the findings whose owner
    session id starts with it (a check that could not run is always printed)."""
    lines, counts = [], {}
    for kind, found in out.items():
        counts[kind] = 0
        for key, text in found:
            if key is None:
                lines.append(f"DISPATCH-{kind} {text}")
                continue
            who, sid = owner_of(key, data)
            if owner and not (sid or "").startswith(owner):
                continue
            counts[kind] += 1
            lines.append(f"DISPATCH-{kind} {text} | owner: {who}")
    if scope.audit:
        undated = [r.get("\\#", r.get("#", "?")) for r in data["audit_rows"]
                   if due_of(r.get("Delivered by (UTC)", ""), now) is None]
        audit = (f"audit dispatch://{scope.audit[0]}/artifact/{scope.audit[1]}: {len(data['audit_rows'])} rows, "
                 f"{len(undated)} with no date ({' '.join('#' + u for u in undated)})")
    else:
        audit = "no audit table"
    extra = [k for k in scope.issues if k not in data["open_keys"]]
    only = f" (owner {owner}* only)" if owner else ""
    # The last two kinds are not work on this project's issues, so each appears only when it happened.
    shown = {k: n for k, n in counts.items() if k in SUMMARY_ALWAYS or n}
    lines.append(f"DISPATCH-SUMMARY {scope.project}: {len(data['open_keys'])} open {scope.project} + {len(extra)} other "
                 f"issues read; {', '.join(f'{n} {k}' for k, n in shown.items())}{only}; {audit}{suffix}")
    return lines


def run(api: Dispatch, scope: Scope, github: PrReader, project_keys: list[str], streams: dict[str, dict],
        now: dt.datetime, owner: str | None = None, drive: Driver | None = None) -> list[str]:
    """One project's run. With a DRIVE, each row that waits on us is re-driven (an executor, a message and a time to
    acknowledge by) instead of only printed; without one the summary says the re-drive is off, so zero PAST-ACK is
    never read as "nothing is past its time"."""
    data = read(api, scope, github, project_keys, streams, now)
    out, waits = evaluate(data, scope, now)
    if drive is not None:
        drive(out, waits, data, now, scope.project, owner_of=owner_of, owner=owner)
    return report(out, data, scope, now, owner, "" if drive is not None else "; re-drive off")


# --- command line --------------------------------------------------------------------------------------------------


def _for(value: str, project: str) -> str | None:
    """VALUE as it applies to PROJECT's run: 'PROJ:X' only to PROJ, a bare X to every run."""
    proj, sep, rest = value.partition(":")
    if not sep:
        return value
    return rest if proj == project else None


def scopes(streams: dict[str, dict], project: str | None, all_streams: bool, issues: list[str],
           audits: list[str]) -> list[Scope]:
    """The runs to make: one for --project, or one per project that has an open `stream` issue."""
    if all_streams:
        by_project: dict[str, list[str]] = {}
        for key, s in streams.items():
            by_project.setdefault(s["project"], []).append(key)
        found = [Scope(p, streams=sorted(ks)) for p, ks in sorted(by_project.items())]
    else:
        found = [Scope(project)]
    for s in found:
        s.issues = [k for k in (_for(v, s.project) for v in issues) if k]
        for a in audits:
            key, sep, slug = a.partition(":")
            if not sep or not slug:
                raise SystemExit(f"--audit {a}: expected KEY:artifact-slug")
            if key.rsplit("-", 1)[0] == s.project:
                s.audit = (key, slug)
    return found


def main(argv: list[str] | None = None, api: Dispatch | None = None, github: PrReader | None = None,
         send=None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="chief-dashboard dispatch-check", description=__doc__.split("\n\n")[0])
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--project", help="the Dispatch project key, e.g. OSSV")
    who.add_argument("--all-streams", action="store_true",
                     help="every project that has an open issue labelled `stream`")
    p.add_argument("--issue", action="append", default=[], metavar="[PROJECT:]KEY",
                   help="an issue of another project to cover too (with PROJECT:, only in that project's run)")
    p.add_argument("--audit", action="append", default=[], metavar="KEY:artifact-slug",
                   help="an audit table with a 'Delivered by (UTC)' column, for the project of KEY")
    p.add_argument("--owner", metavar="SESSION_ID",
                   help="print only the findings whose owner's session id starts with this (give the full id: "
                        "two sessions can share the first 8 characters)")
    p.add_argument("--github-org", default="theorem-labs", help="where to look for pull requests naming each issue")
    p.add_argument("--as-of", type=lambda s: ts(s.replace("+00:00", "")),
                   help="judge events as they stood at this UTC time (YYYY-MM-DDTHH:MM[:SS]Z)")
    p.add_argument("--no-redrive", action="store_true",
                   help="only print the rows that wait on us; send nothing and record nothing")
    p.add_argument("--redrive-dry-run", action="store_true",
                   help="name the executor and the time for each row, and send and record nothing")
    p.add_argument("--ack-minutes", type=int, default=ACK_MINUTES,
                   help=f"how long an executor has to reply in the ask's thread (default {ACK_MINUTES})")
    p.add_argument("--redrive-state", type=Path, default=STATE_DIR / "redrive.json",
                   help="the ledger of what was driven, to whom and by when")
    args = p.parse_args(argv)
    now = args.as_of or dt.datetime.now(dt.UTC)
    try:
        api = api or Dispatch()
        project_keys = [x["key"] for x in api.projects()]
        streams = read_streams(api)
        runs = scopes(streams, args.project, args.all_streams, args.issue, args.audit)
    except (SourceError, OSError, KeyError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    github = github or GitHub(args.github_org)
    drive = None
    if not args.no_redrive:
        kw = {"send": send} if send is not None else {}
        drive = Driver(Ledger.load(args.redrive_state), ack_minutes=args.ack_minutes,
                       dry_run=args.redrive_dry_run, **kw)
    failed = False
    for s in runs:
        if args.all_streams:
            print(f"== {s.project} (stream {' '.join(s.streams)}) ==")
        try:
            print("\n".join(run(api, s, github, project_keys, streams, now, args.owner, drive)))
        except (SourceError, KeyError, ValueError) as e:
            failed = True
            print(f"DISPATCH-SUMMARY {s.project}: NOT READ ({type(e).__name__}: {' '.join(str(e).split())[:300]})")
        sys.stdout.flush()
    return 1 if failed else 0
