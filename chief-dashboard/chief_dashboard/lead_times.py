"""chief-dashboard lead-times: how long a project's work took in the last N days, so a lead sets dates from measured
times and not from instinct (models estimate human time, 5 to 10 times too long).

Reads only. Dispatch, with the dashboard's client (`sources.Dispatch`): the project's issue list, then for each issue
updated in the window the issue and its whole event history, and for each issue that finished in the window the
versions of its plan document (slug plan-md, else plan-*) until the first one that holds a date. GitHub GraphQL, with
the GitHub App token `gh-app-token agent --owner ORG` mints (dispatch_check's `GitHub`): every pull request of ORG
merged in the window, one search per day.

Prints one table per project, then one line to set dates from:

  claim-to-done     an issue that finished in the window: from its first claim (else its creation) to the first time
                    its status became needs_review, testing or done; for all of them, then for each label;
  PR open-to-merge  a pull request of ORG merged in the window that is the work of one of the project's issues (title
                    or branch names the key exactly, or the body says it closes, fixes, resolves, implements or is
                    part of it: dispatch_check's `names_key`); open to merge, for all of them, then per repository;
  plan date / done  an issue that finished in the window whose plan holds a date: the time the plan allowed (from the
                    first plan version that holds a date to the latest date in that version; a date with no time is
                    due at 23:59Z) over the time it took (from that version to the finish). Above 1 the date was too
                    far out. A plan dated only after the finish, or whose date is before it was written, gets a row
                    with no ratio.

Hours are wall-clock. p85 is the 85th percentile (linear between ranks). Exits non-zero when a read fails (for one
project of several, the others still print)."""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import re
import statistics
import sys
import urllib.parse
from collections.abc import Callable

from .dispatch_check import GitHub, _get, due_of, names_key, plan_artifact, ts
from .sources import Dispatch, SourceError

FINISHED = ("needs_review", "testing", "done")
PAGE = 200
PR_FIELDS = "... on PullRequest{url title body headRefName createdAt mergedAt repository{nameWithOwner}}"

PrSource = Callable[[dt.datetime, dt.datetime], list[dict]]


# --- reading -------------------------------------------------------------------------------------------------------


def events(api: Dispatch, key: str) -> list[dict]:
    """Every event of the issue, oldest first, PAGE at a time."""
    out: list[dict] = []
    while True:
        page = _get(api, f"issues/{key}/events", after=out[-1]["seq"] if out else 0, limit=PAGE)
        out += page
        if len(page) < PAGE:
            return out


def span(issue: dict, evs: list[dict]) -> dict | None:
    """{start, by (claim|created), end, reached} of an issue: its first claim before it finished (else its creation),
    and the first issue event whose status is needs_review, testing or done; None when it never finished."""
    end = next((e for e in evs if e["type"].startswith("issue.") and e["payload"].get("status") in FINISHED), None)
    if end is None:
        return None
    finished = ts(end["created_at"])
    claims = [ts(e["created_at"]) for e in evs if e["type"] == "issue.claimed"]
    first = min(claims) if claims else None
    by = "claim" if first is not None and first <= finished else "created"
    start = first if by == "claim" else ts(issue["created_at"])
    return {"start": start, "by": by, "end": finished, "reached": end["payload"]["status"]}


def plan_date(api: Dispatch, issue: dict) -> dict | None:
    """{slug, set, due} for the first version of the issue's plan that holds a date: when that version was written and
    the latest date in it; None when the issue has no plan or no version holds a date."""
    plan = plan_artifact(issue)
    if plan is None:
        return None
    for v in sorted(plan.get("versions") or [], key=lambda v: v["number"]):
        set_at = ts(v["created_at"])
        text = _get(api, f"issues/{issue['key']}/artifacts/{urllib.parse.quote(plan['slug'])}/versions/{v['number']}")
        due = due_of(text.get("markdown") or "", set_at)
        if due is not None:
            return {"slug": plan["slug"], "set": set_at, "due": due}
    return None


def read_project(api: Dispatch, project: str, since: dt.datetime, now: dt.datetime) -> dict:
    """{keys, issues}: every key of the project, and each issue that finished in [SINCE, NOW] with its span, labels
    and plan date."""
    listed = _get(api, "issues", project=project)
    recent = [i["key"] for i in listed if ts(i["updated_at"]) >= since]

    def one(key: str) -> dict | None:
        issue = _get(api, f"issues/{key}")
        s = span(issue, events(api, key))
        if s is None or not since <= s["end"] <= now:
            return None
        return {"key": key, "title": issue["title"], "labels": issue.get("labels") or [], **s,
                "plan": plan_date(api, issue)}

    with cf.ThreadPoolExecutor(16) as pool:
        done = [r for r in pool.map(one, recent) if r]
    return {"keys": {i["key"] for i in listed}, "issues": sorted(done, key=lambda r: r["end"])}


class MergedPrs:
    """Every pull request of a GitHub organization merged in a window, read as the org's GitHub App."""

    def __init__(self, github: GitHub) -> None:
        self.github = github

    def day(self, day: dt.date) -> list[dict]:
        out, cursor = [], None
        while True:
            after = f',after:"{cursor}"' if cursor else ""
            q = f"org:{self.github.org} is:pr is:merged merged:{day.isoformat()}"
            d = self.github.gql({"s": f'search(type:ISSUE,first:100{after},query:"{q}")'
                                      f"{{pageInfo{{hasNextPage endCursor}} nodes{{{PR_FIELDS}}}}}"})["s"]
            out += [p for p in d["nodes"] if p]
            if not d["pageInfo"]["hasNextPage"]:
                return out
            cursor = d["pageInfo"]["endCursor"]

    def __call__(self, since: dt.datetime, now: dt.datetime) -> list[dict]:
        days = [since.date() + dt.timedelta(days=n) for n in range((now.date() - since.date()).days + 1)]
        with cf.ThreadPoolExecutor(4) as pool:
            prs = [p for found in pool.map(self.day, days) for p in found]
        return list({p["url"]: p for p in prs if since <= ts(p["mergedAt"]) <= now}.values())


def project_prs(project: str, keys: set[str], prs: list[dict]) -> list[dict]:
    """The pull requests that are the work of one of the project's issues, each with the keys it names."""
    pattern = re.compile(rf"(?<![\w.-]){re.escape(project)}-\d+", re.IGNORECASE)
    out = []
    for p in prs:
        named = {m.upper() for m in pattern.findall(" ".join((p["title"], p["headRefName"], p["body"] or "")))}
        mine = sorted(k for k in named & keys if names_key(p, k))
        if mine:
            out.append({**p, "keys": mine})
    return out


# --- the table -----------------------------------------------------------------------------------------------------


def hours(a: dt.datetime, b: dt.datetime) -> float:
    return (b - a).total_seconds() / 3600


def p85(xs: list[float]) -> float:
    s = sorted(xs)
    pos = (len(s) - 1) * 0.85
    lo = int(pos)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (pos - lo)


def _stats(measure: str, group: str, xs: list[float], unit: str) -> list[str]:
    if not xs:
        return [measure, group, "0", "-", "-", ""]
    fmt = (lambda x: f"{x:.1f}h") if unit == "h" else (lambda x: f"{x:.2f}x")
    return [measure, group, str(len(xs)), fmt(statistics.median(xs)), fmt(p85(xs)), ""]


def rows(data: dict, prs: list[dict] | str) -> list[list[str]]:
    """The table's rows: measure, group, n, median, p85, note. PRS is the error text when GitHub could not be read."""
    out = []
    issues = data["issues"]
    took = {r["key"]: hours(r["start"], r["end"]) for r in issues}
    by_claim = sum(r["by"] == "claim" for r in issues)
    head = _stats("claim-to-done", "all", list(took.values()), "h")
    head[5] = f"{by_claim} from a claim, {len(issues) - by_claim} from creation (no claim)" if issues else ""
    out.append(head)
    for label in sorted({lb for r in issues for lb in r["labels"]}):
        out.append(_stats("claim-to-done", f"label {label}", [took[r["key"]] for r in issues if label in r["labels"]],
                          "h"))

    if isinstance(prs, str):
        out.append(["PR open-to-merge", "all", "-", "-", "-", f"NOT READ ({prs})"])
    else:
        pr_h = [hours(ts(p["createdAt"]), ts(p["mergedAt"])) for p in prs]
        out.append(_stats("PR open-to-merge", "all", pr_h, "h"))
        for repo in sorted({p["repository"]["nameWithOwner"] for p in prs}):
            out.append(_stats("PR open-to-merge", repo, [h for p, h in zip(prs, pr_h, strict=True)
                                                        if p["repository"]["nameWithOwner"] == repo], "h"))

    ratios, plan_rows = [], []
    for r in issues:
        pl = r["plan"]
        if pl is None:
            continue
        note = (f"plan-{pl['slug'].removeprefix('plan-')} set {pl['set']:%m-%d %H:%M}Z for {pl['due']:%m-%d %H:%M}Z; "
                f"{r['reached']} {r['end']:%m-%d %H:%M}Z")
        if pl["set"] >= r["end"]:
            plan_rows.append(["plan date / done", r["key"], "1", "-", "-", f"{note}: dated only after it finished"])
        elif pl["due"] <= pl["set"]:
            plan_rows.append(["plan date / done", r["key"], "1", "-", "-", f"{note}: date before the plan was written"])
        else:
            ratio = hours(pl["set"], pl["due"]) / hours(pl["set"], r["end"])
            ratios.append(ratio)
            plan_rows.append(["plan date / done", r["key"], "1", f"{ratio:.2f}x", "-", note])
    out.append(_stats("plan date / done", "all", ratios, "x"))
    return out + plan_rows


def table(rs: list[list[str]]) -> list[str]:
    head = ["measure", "group", "n", "median", "p85", "note"]
    widths = [max(len(r[i]) for r in [head, *rs]) for i in range(5)]
    return [("  ".join(c.rjust(w) if i in (2, 3, 4) else c.ljust(w) for i, (c, w) in enumerate(zip(r, widths,
                                                                                                    strict=False)))
             + ("  " + r[5] if r[5] else "")).rstrip() for r in [head, *rs]]


def report(data: dict, prs: list[dict] | str) -> list[str]:
    """The table, then the line to set dates from. PRS is the error text when GitHub could not be read."""
    took = [hours(r["start"], r["end"]) for r in data["issues"]]
    fmt = lambda xs: f"{statistics.median(xs):.1f} h" if xs else "none in the window"  # noqa: E731
    merged = ("NOT READ" if isinstance(prs, str)
              else fmt([hours(ts(p["createdAt"]), ts(p["mergedAt"])) for p in prs]))
    return [*table(rows(data, prs)),
            f"Set dates from these, not from instinct: median claim-to-done {fmt(took)}, PR open-to-merge {merged}."]


# --- command line --------------------------------------------------------------------------------------------------


def stream_projects(api: Dispatch) -> dict[str, list[str]]:
    """{project: its open `stream` issue keys}."""
    out: dict[str, list[str]] = {}
    for i in api.stream_issues():
        out.setdefault(i.get("project") or i["key"].rsplit("-", 1)[0], []).append(i["key"])
    return {p: sorted(ks) for p, ks in sorted(out.items())}


def main(argv: list[str] | None = None, api: Dispatch | None = None, prs: PrSource | None = None,
         now: dt.datetime | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="chief-dashboard lead-times", description=__doc__.split("\n\n")[0])
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--project", help="the Dispatch project key, e.g. BET")
    who.add_argument("--all-streams", action="store_true",
                     help="every project that has an open issue labelled `stream`")
    p.add_argument("--days", type=float, default=7, help="the window, in days back from now (default 7)")
    p.add_argument("--github-org", default="theorem-labs", help="where to look for merged pull requests")
    args = p.parse_args(argv)
    now = now or dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(days=args.days)
    try:
        api = api or Dispatch()
        projects = stream_projects(api) if args.all_streams else {args.project: []}
    except (SourceError, OSError, KeyError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    prs = prs or MergedPrs(GitHub(args.github_org))
    merged: list[dict] | None = None
    pr_err = ""
    failed = False
    for project, streams in projects.items():
        stream = f" (stream {' '.join(streams)})" if streams else ""
        print(f"== {project}{stream}: {since:%Y-%m-%d %H:%M}Z to {now:%Y-%m-%d %H:%M}Z ==")
        if merged is None and not pr_err:
            try:
                merged = prs(since, now)
            except Exception as e:  # noqa: BLE001 - a GitHub failure is printed; the Dispatch measures still print
                pr_err = " ".join(str(e).split())[:200]
        try:
            data = read_project(api, project, since, now)
        except (SourceError, KeyError, ValueError) as e:
            failed = True
            print(f"NOT READ ({type(e).__name__}: {' '.join(str(e).split())[:300]})")
            continue
        print("\n".join(report(data, pr_err or project_prs(project, data["keys"], merged or []))))
        failed = failed or bool(pr_err)
        sys.stdout.flush()
    return 1 if failed else 0
