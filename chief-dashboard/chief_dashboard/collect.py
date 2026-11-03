"""One read of every source into a plain snapshot dict, the only input the page model takes.

A source that fails leaves its part empty and adds a line to `errors`; the page shows those lines. Nothing here
invents a value: a stream with no issue or no scoreboard file is recorded as exactly that."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from . import sessions
from .plan import refs_in
from .plan_issues import plan_items, read_issues, wanted_keys
from .prs import PrCache
from .sources import Dispatch, SourceError
from .videos import VideoStore

# Optional overrides kept by the chief of staff: each stream's priority rank and whether it is one of Sami's
# attention items, matched to a discovered stream by issue key or slug. Everything else comes from the scoreboard.
REGISTRY = Path(os.environ.get("STREAMS_REGISTRY", Path.home() / "src/scrum/workstreams/streams.json"))
SCOREBOARD_SLUG = "scoreboard-json"
# The pilot's state directory (`chief-dashboard pilot ... --state`): each stream's members.txt and pilot-sessions.txt,
# which `sessions.stream_census` reads and extends.
PILOT_STATE = Path(os.environ.get("DASHBOARD_PILOT_STATE", "/mnt/nvme/sami/chief/streams"))


class VersionCache:
    """Scoreboard versions are immutable, so each (issue, version) is downloaded once."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, int], object] = {}

    def get(self, api: Dispatch, key: str, number: int):
        if (key, number) not in self._data:
            self._data[(key, number)] = api.file_version(key, SCOREBOARD_SLUG, number)
        return self._data[(key, number)]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_registry(errors: list[str]) -> dict:
    if not REGISTRY.exists():
        return {"streams": []}
    try:
        return json.loads(REGISTRY.read_text())
    except (OSError, ValueError) as e:
        errors.append(f"stream overrides {REGISTRY}: {e}")
        return {"streams": []}


def scoreboard(api: Dispatch, cache: VersionCache, key: str) -> dict:
    """The issue's scoreboard.json over time, oldest first: {number, created_at, data | error}.

    Only the newest version of each UTC day carries `data`, plus the newest version overall. A lead uploads its
    scoreboard many times a day and each copy embeds every dated reading of every number, so keeping them all
    duplicated the same series hundreds of times: on 2026-11-02 OSS Verify's 404 versions were 340 MB of the
    snapshot's 408 MB, the service held 2.0 GB and a refresh took 757 s. Every reader works at day granularity or
    takes the newest (`board_at`, `day_plan`, `day_record` over the last 7 days), and a version with no `data` is
    skipped by each of them.
    """
    issue = api.issue(key)
    art = next((a for a in issue.get("artifacts", []) if a["slug"] == SCOREBOARD_SLUG), None)
    out = {"issue_title": issue.get("title"), "versions": [], "artifacts": issue.get("artifacts", [])}
    if art is None:
        return out
    versions = sorted(art["versions"], key=lambda v: v["number"])
    keep = {v["number"] for v in {(v.get("created_at") or "")[:10]: v for v in versions}.values()}
    if versions:
        keep.add(versions[-1]["number"])
    for v in versions:
        row = {"number": v["number"], "created_at": v["created_at"]}
        if v["number"] in keep:
            try:
                data = cache.get(api, key, v["number"])
                if not isinstance(data, dict):
                    raise SourceError("the file is not a JSON object")
                row["data"] = data
            except SourceError as e:
                row["error"] = str(e)
        out["versions"].append(row)
    return out


def collect(
    cache: VersionCache, api: Dispatch | None = None, videos: VideoStore | None = None, prs: PrCache | None = None
) -> dict:
    errors: list[str] = []
    snap: dict = {"read_at": _now(), "errors": errors}
    snap["registry"] = read_registry(errors)
    # Live agent sessions per tmux window, read from this machine's processes and tmux (sessions.py), first: it
    # needs no outside service, so a Dispatch outage still shows them.
    snap["sessions"] = sessions.read(errors)
    # Each windowed stream's live sessions on every machine (`sessions.stream_census`): a session moved to another
    # machine, or a read from an agent box, still counts for its stream.
    live = sessions.read_envoy(errors)
    try:
        snap["stream_sessions"] = {issue: sessions.stream_census(PILOT_STATE, issue, snap["sessions"], live)
                                   for issue in sorted({i for i in sessions.WINDOW_STREAMS.values() if i})}
    except OSError as e:
        errors.append(f"stream sessions: {PILOT_STATE}: {e}")
    try:
        api = api or Dispatch()
    except (OSError, KeyError, ValueError) as e:
        errors.append(f"Dispatch config: {e}")
        return snap
    snap["dispatch_web"] = api.server

    # Streams are discovered: every open issue with the label `stream`, in any project. The lead owns that issue's
    # scoreboard.json; streams.json only overrides rank and attention.
    boards = {}
    stream_issues: list[dict] = []
    try:
        stream_issues = [
            {
                "key": i["key"],
                "title": i.get("title"),
                "project": i.get("project") or i["key"].split("-")[0],
                "claim": (i.get("claim") or {}).get("actor"),
            }
            for i in api.stream_issues()
        ]
    except SourceError as e:
        errors.append(f"stream issues (label 'stream'): {e}")
    for s in stream_issues:
        try:
            boards[s["key"]] = scoreboard(api, cache, s["key"])
        except SourceError as e:
            errors.append(f"scoreboard of {s['key']}: {e}")
            boards[s["key"]] = {"error": str(e), "versions": []}
    snap["stream_issues"] = stream_issues
    snap["scoreboards"] = boards

    # Each stream issue's videos, downloaded once each so the stream's own player can play them (see videos.py).
    store = videos or VideoStore()
    by_issue = {s["key"]: store.explainers(api, s["key"], boards[s["key"]].get("artifacts", []), errors)
                for s in stream_issues}
    snap["explainers"] = by_issue
    if stream_issues and not errors:  # prune only after a complete read, so a failed one never deletes a video
        store.prune({r["sha"] for rows in by_issue.values() for r in rows if r.get("sha")})

    # The state of every pull request the plans of the last 8 days name, in one GitHub query (cached 5 min).
    snap["prs"] = (prs or PrCache()).get(refs_in(boards, datetime.now(UTC)), errors)

    asks: list[dict] = []
    projects: dict[str, str] = {}
    try:
        for p in api.projects():
            projects[p["key"]] = p["name"]
            try:
                for a in api.open_asks(p["key"]):
                    asks.append({**a, "project": p["key"]})
            except SourceError as e:
                errors.append(f"open asks of {p['key']}: {e}")
    except SourceError as e:
        errors.append(f"Dispatch projects: {e}")

    # The Dispatch issue of every item in today's plans: named by the item, or delivered by its pull request.
    items = plan_items(boards, datetime.now(UTC))
    states = (snap["prs"] or {}).get("states") or {}
    snap["plan_issues"] = read_issues(api, wanted_keys(items, states, set(projects)))

    # Each ask's issue, and its parent chain up to 6 levels: an ask sits on a child issue far more often than on
    # the stream issue itself (BET-250 under BET-83, INFRA-26 under INFRA-49), so matching only the ask's own key
    # left every stream reading "no decisions waiting on you" while the header counted them (2026-10-26).
    issues: dict[str, dict] = {}
    todo = sorted({(a.get("owner") or {}).get("issue", {}).get("key") for a in asks} - {None})
    for _ in range(6):
        parents: set[str] = set()
        for key in todo:
            if key in issues:
                continue
            try:
                i = api.issue(key)
            except SourceError as e:
                errors.append(f"issue {key} (for its open asks): {e}")
                continue
            issues[key] = {"assignee": i.get("assignee"), "title": i.get("title"), "status": i.get("status"),
                           "parent": i.get("parent")}
            if i.get("parent"):
                parents.add(i["parent"])
        todo = sorted(parents - issues.keys())
        if not todo:
            break
    snap["projects"] = projects
    snap["asks"] = asks
    snap["ask_issues"] = issues

    try:
        snap["agents"] = {a["session_id"]: a for a in api.agents()}
    except SourceError as e:
        errors.append(f"Dispatch live sessions: {e}")
        snap["agents"] = {}
    return snap
