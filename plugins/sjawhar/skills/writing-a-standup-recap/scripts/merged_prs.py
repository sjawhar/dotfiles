"""Pull every merged pull request in a window into one JSON file, once, for all recap drafters.

GitHub's search has one secondary rate limit for the whole machine, so every drafter reads this file instead of
searching. A day with more than 1,000 merges is split into halves, because search returns at most 1,000 results.

    uv run python merged_prs.py --start 2026-10-26 --end 2026-11-02 --out merged_prs.json \
        --owner theorem-labs --owner-repo theorem-labs/<any-repo> \
        --user sjawhar --user-repo sjawhar/dotfiles

`--owner-repo` and `--user-repo` give `gh` a repository to route the App token through (`GH_REPO`); any repository
of that owner works.
"""
import argparse
import json
import os
import subprocess
import time
from datetime import date, timedelta


def _search(query: str, repo_env: str) -> tuple[int, list[dict]]:
    """Return (the total GitHub reports, the items). Raise when a page cannot be read, because a partial file
    read as complete loses a whole stream's work."""
    items: list[dict] = []
    page = 1
    while True:
        env = dict(os.environ, GH_REPO=repo_env)
        for _ in range(6):
            run = subprocess.run(
                ["gh", "api", "-X", "GET", "search/issues", "-f", f"q={query}", "-f", "per_page=100",
                 "-f", f"page={page}"],
                capture_output=True, text=True, env=env, timeout=120,
            )
            if run.returncode == 0:
                break
            time.sleep(20)  # a secondary rate limit clears in minutes
        else:
            raise SystemExit(f"search failed, coverage incomplete: {query}\n{run.stderr[-400:]}")
        data = json.loads(run.stdout)
        items += data["items"]
        total = data["total_count"]
        if len(data["items"]) < 100 or page >= 10:
            return total, items
        page += 1
        time.sleep(3)


def _row(item: dict) -> dict:
    return {
        "repo": item["repository_url"].split("repos/")[1],
        "number": item["number"],
        "title": item["title"],
        "user": item["user"]["login"],
        "merged_at": (item.get("pull_request") or {}).get("merged_at"),
        "url": item["html_url"],
        "labels": [label["name"] for label in item["labels"]],
        "body": (item.get("body") or "")[:600],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--owner", action="append", default=[])
    ap.add_argument("--owner-repo", action="append", default=[])
    ap.add_argument("--user", action="append", default=[])
    ap.add_argument("--user-repo", action="append", default=[])
    args = ap.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if len(args.owner) != len(args.owner_repo) or len(args.user) != len(args.user_repo):
        raise SystemExit("each --owner needs an --owner-repo and each --user a --user-repo, to route the token")
    scopes = [(f"org:{o}", r) for o, r in zip(args.owner, args.owner_repo)]
    scopes += [(f"user:{u}", r) for u, r in zip(args.user, args.user_repo)]
    if not scopes:
        raise SystemExit("give at least one --owner or --user, else the file is silently empty")

    rows: dict[tuple[str, int], dict] = {}
    for scope, repo_env in scopes:
        for day in days:
            total, items = _search(f"{scope} is:pr is:merged merged:{day}", repo_env)
            if total > 1000:  # search returns at most 1,000 results: split the day and check each half
                items = []
                for lo, hi in (("T00:00:00Z", "T11:59:59Z"), ("T12:00:00Z", "T23:59:59Z")):
                    half_total, half = _search(f"{scope} is:pr is:merged merged:{day}{lo}..{day}{hi}", repo_env)
                    if half_total > 1000:
                        raise SystemExit(f"{scope} {day} {lo}: {half_total} merges in one half, split it further")
                    items += half
            print(f"{scope} {day}: total {total}, fetched {len(items)}")
            for item in items:
                rows[(item["repository_url"].split("repos/")[1], item["number"])] = _row(item)
            time.sleep(2)

    with open(args.out, "w") as handle:
        json.dump(sorted(rows.values(), key=lambda r: (r["merged_at"] or "", r["repo"])), handle, indent=1)
    print(f"wrote {args.out}: {len(rows)} pull requests")


if __name__ == "__main__":
    main()
