"""The state of every pull request the streams' plans name, read from GitHub in one GraphQL query through `gh api
graphql` and cached for 5 minutes.

`gh` must be the dotfiles shim in agent routing (the service sets PATH and the gh-app-routes include): the query goes
out as a repository owner's GitHub App, never as Sami's own login. An App token covers one owner, so the query is
routed to the owner most of the pull requests belong to (`GH_REPO`); a pull request that token cannot see comes back
with its GitHub error, shown on the page as "state unknown"."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import Counter

from .dispatch_check import names_key

CACHE_SECONDS = 300
GH = os.environ.get("DASHBOARD_GH", "gh")
_REF = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)$")

_FIELDS = """number title url state merged mergedAt closedAt isDraft reviewDecision author{login} headRefName body
reviewRequests(first:1){totalCount}
commits(last:1){nodes{commit{statusCheckRollup{state contexts(first:100){nodes{__typename
... on CheckRun{conclusion completedAt} ... on StatusContext{state createdAt}}}}}}}"""
_ANY_KEY = re.compile(r"(?<![\w.-])([A-Za-z][A-Za-z0-9]*-\d+)(?!\w|\.\w)")


def named_keys(node: dict) -> list[str]:
    """The Dispatch-style keys the pull request names as its own work, by dispatch-check's rule (`names_key`: its
    title or branch names the key, or its body says it closes, fixes, resolves, implements or is part of it). Which
    of them are real Dispatch issues is the collector's to check."""
    pr = {"title": node.get("title") or "", "headRefName": node.get("headRefName") or "", "body": node.get("body") or ""}
    found = {m.upper() for m in _ANY_KEY.findall(" ".join(pr.values()))}
    return sorted(k for k in found if names_key(pr, k))


def parse_ref(ref) -> tuple[str, str, int] | None:
    """("owner", "repo", 123) of "owner/repo#123"; None for anything else."""
    m = _REF.match(str(ref or "").strip())
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def build_query(refs: list[str]) -> str:
    parts = []
    for i, ref in enumerate(refs):
        owner, repo, num = parse_ref(ref)
        parts.append(f"p{i}: repository(owner:{json.dumps(owner)},name:{json.dumps(repo)}){{pullRequest(number:{num}){{{_FIELDS}}}}}")
    return "{" + "\n".join(parts) + "}"


def _failing_since(rollup: dict) -> str | None:
    """When the earliest failing check of the head commit finished."""
    times = []
    for c in ((rollup.get("contexts") or {}).get("nodes")) or []:
        if c.get("__typename") == "CheckRun" and c.get("conclusion") in ("FAILURE", "TIMED_OUT", "STARTUP_FAILURE"):
            times.append(c.get("completedAt"))
        elif c.get("__typename") == "StatusContext" and c.get("state") in ("FAILURE", "ERROR"):
            times.append(c.get("createdAt"))
    times = [t for t in times if t]
    return min(times) if times else None


def normalize(node: dict | None) -> dict:
    """The fields the page uses, from one `pullRequest` node."""
    if not node:
        return {"error": "not found"}
    commits = ((node.get("commits") or {}).get("nodes")) or []
    rollup = ((commits[0].get("commit") or {}).get("statusCheckRollup") if commits else None) or {}
    return {
        "title": node.get("title"),
        "url": node.get("url"),
        "state": node.get("state"),  # OPEN, CLOSED, MERGED
        "merged_at": node.get("mergedAt"),
        "closed_at": node.get("closedAt"),
        "draft": bool(node.get("isDraft")),
        "review": node.get("reviewDecision"),  # APPROVED, CHANGES_REQUESTED, REVIEW_REQUIRED or None
        "review_requested": ((node.get("reviewRequests") or {}).get("totalCount") or 0) > 0,
        "author": (node.get("author") or {}).get("login"),
        "named_keys": named_keys(node),  # the keys it names as its own work (dispatch-check's `names_key`)
        "ci": rollup.get("state"),  # SUCCESS, FAILURE, ERROR, PENDING, EXPECTED or None
        "ci_failing_since": _failing_since(rollup) if rollup.get("state") in ("FAILURE", "ERROR") else None,
    }


def fetch(refs: list[str], timeout: float = 60) -> dict[str, dict]:
    """{ref: state} for every ref, in one query. A ref the query could not read carries `error`."""
    refs = [r for r in refs if parse_ref(r)]
    if not refs:
        return {}
    owners = Counter(parse_ref(r)[0] for r in refs)
    top = owners.most_common(1)[0][0]
    # The shim routes by the first repository the query names: put the owner with the most pull requests first.
    refs = sorted(refs, key=lambda r: parse_ref(r)[0] != top)
    env = {**os.environ, "GH_REPO": refs[0].split("#")[0]}
    # Outside any checkout: the shim routes by the working directory's git remote before GH_REPO.
    done = subprocess.run(
        [GH, "api", "graphql", "-f", f"query={build_query(refs)}"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd="/",
    )
    try:
        body = json.loads(done.stdout)
    except ValueError as e:
        raise RuntimeError(f"gh api graphql: exit {done.returncode}: {(done.stderr or done.stdout)[:300]}") from e
    data = body.get("data") or {}
    errors = {}
    for err in body.get("errors") or []:
        path = err.get("path") or []
        if path:
            errors[path[0]] = err.get("message") or err.get("type") or "error"
    out = {}
    for i, ref in enumerate(refs):
        alias = f"p{i}"
        if alias in errors:
            out[ref] = {"error": errors[alias]}
        else:
            out[ref] = normalize((data.get(alias) or {}).get("pullRequest"))
    return out


class PrCache:
    """Pull request states, refetched in one query when the last read is 5 minutes old or a new ref appears."""

    def __init__(self) -> None:
        self._at = 0.0
        self._refs: frozenset[str] = frozenset()
        self._data: dict[str, dict] = {}
        self._read_at: str | None = None

    def get(self, refs: set[str], errors: list[str]) -> dict:
        refs = frozenset(r for r in refs if parse_ref(r))
        fresh = time.monotonic() - self._at < CACHE_SECONDS
        if not (fresh and refs <= self._refs):
            try:
                self._data = fetch(sorted(refs))
                self._refs = refs
                self._at = time.monotonic()
                self._read_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
                errors.append(f"GitHub pull request states: {e}")
        return {"read_at": self._read_at, "states": {r: self._data[r] for r in refs if r in self._data}}
