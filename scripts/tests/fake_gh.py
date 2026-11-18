#!/usr/bin/env python3
"""A stand-in for `gh pr create|view|merge`, as scripts/dotfiles-publish calls them, over the bare
repository FAKE_GH_ORIGIN, with its pull requests kept in the JSON file FAKE_GH_STATE.

A merge is GitHub's squash merge: a new commit on origin's main whose tree is the three-way merge
of main and the pull request's head from their merge base, refused when that merge conflicts, when
the head is not the one --match-head-commit names, or, with `require_review` set in the state, when
the pull request is not approved. Before a merge it runs, once each, the shell commands the state
lists under `before_merge`, so a test can move main under an open pull request. Every call is
appended to the state's `calls`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ORIGIN = os.environ["FAKE_GH_ORIGIN"]
STATE = Path(os.environ["FAKE_GH_STATE"])
VALUED = {"-R", "--repo", "--json", "--jq", "-q", "--base", "-B", "--head", "-H", "--title", "-t", "--body", "-b",
          "--match-head-commit", "--subject", "-t"}


def load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"next": 1, "prs": [], "calls": [], "require_review": False, "before_merge": []}


def save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=1))


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", ORIGIN, *args], capture_output=True, text=True, check=check)


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def parse(argv: list[str]) -> tuple[list[str], dict[str, str], set[str]]:
    words, values, flags = [], {}, set()
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--") and "=" in a:
            name, value = a.split("=", 1)
            values[name] = value
        elif a in VALUED:
            values[a] = argv[i + 1]
            i += 1
        elif a.startswith("-"):
            flags.add(a)
        else:
            words.append(a)
        i += 1
    return words, values, flags


def repo(values: dict[str, str]) -> str:
    return values.get("-R") or values.get("--repo") or "sjawhar/dotfiles"


def find(state: dict, target: str) -> dict | None:
    m = re.search(r"/pull/(\d+)$", target)
    if m or target.isdigit():
        number = int(m.group(1) if m else target)
        return next((p for p in state["prs"] if p["number"] == number), None)
    by_head = [p for p in state["prs"] if p["head"] == target]
    open_ = [p for p in by_head if p["state"] == "OPEN"]
    return (open_ or by_head or [None])[-1]


def head_sha(pr: dict) -> str:
    return git("rev-parse", "-q", "--verify", f"refs/heads/{pr['head']}", check=False).stdout.strip()


def merged_tree(pr: dict) -> str | None:
    base = git("rev-parse", "refs/heads/main").stdout.strip()
    head = head_sha(pr)
    mb = git("merge-base", base, head).stdout.strip()
    out = git("merge-tree", "--write-tree", f"--merge-base={mb}", base, head, check=False)
    return out.stdout.split()[0] if out.returncode == 0 else None


def approved(pr: dict) -> bool:
    return pr.get("reviewDecision") == "APPROVED"


def fields(state: dict, pr: dict, slug: str) -> dict:
    if pr["state"] != "OPEN":
        merge_state = "UNKNOWN"
    elif state.get("require_review") and not approved(pr):
        merge_state = "BLOCKED"
    elif merged_tree(pr) is None:
        merge_state = "DIRTY"
    else:
        merge_state = "CLEAN"
    return {
        "number": pr["number"],
        "url": f"https://github.com/{slug}/pull/{pr['number']}",
        "state": pr["state"],
        "headRefName": pr["head"],
        "headRefOid": head_sha(pr),
        "reviewDecision": pr.get("reviewDecision", ""),
        "latestReviews": pr.get("latestReviews", []),
        "mergeStateStatus": merge_state,
    }


def emit(data: dict, values: dict[str, str]) -> None:
    wanted = values["--json"].split(",")
    out = json.dumps({k: data[k] for k in wanted})
    expr = values.get("--jq") or values.get("-q")
    if expr is None:
        print(out)
        return
    jq = shutil.which("jq", path="/usr/bin:/bin")
    if not jq:
        fail("fake gh: --jq needs jq in /usr/bin or /bin")
    result = subprocess.run([jq, "-r", expr], input=out, capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"fake gh: jq: {result.stderr}")
    sys.stdout.write(result.stdout)


def main() -> None:
    state = load()
    state["calls"].append(sys.argv[1:])
    words, values, flags = parse(sys.argv[1:])
    slug = repo(values)
    try:
        if words[:2] == ["pr", "create"]:
            head = values.get("--head") or values.get("-H")
            base = values.get("--base") or values.get("-B")
            if any(p["head"] == head and p["state"] == "OPEN" for p in state["prs"]):
                fail(f'a pull request for branch "{head}" into branch "{base}" already exists')
            if not git("rev-parse", "-q", "--verify", f"refs/heads/{head}", check=False).stdout.strip():
                fail(f"pull request create failed: head branch {head} does not exist")
            pr = {"number": state["next"], "head": head, "base": base, "state": "OPEN",
                  "title": values.get("--title") or values.get("-t"), "body": values.get("--body") or values.get("-b")}
            state["next"] += 1
            state["prs"].append(pr)
            print(f"https://github.com/{slug}/pull/{pr['number']}")
        elif words[:2] == ["pr", "view"]:
            pr = find(state, words[2])
            if pr is None:
                fail(f'no pull requests found for branch "{words[2]}"')
            emit(fields(state, pr, slug), values)
        elif words[:2] == ["pr", "merge"]:
            pr = find(state, words[2])
            if pr is None or pr["state"] != "OPEN":
                fail(f"pull request {words[2]} is not open")
            while state.get("before_merge"):
                command = state["before_merge"].pop(0)
                subprocess.run(command, shell=True, check=True)
            if "--squash" not in flags:
                fail("fake gh: only --squash merges are modelled")
            if values.get("--match-head-commit") and values["--match-head-commit"] != head_sha(pr):
                fail(f"head branch was modified. Review and try the merge again.")
            if state.get("require_review") and not approved(pr):
                fail(f"X Pull request {slug}#{pr['number']} is not mergeable: the base branch policy prohibits the merge.")
            tree = merged_tree(pr)
            if tree is None:
                fail(f"X Pull request {slug}#{pr['number']} is not mergeable: the merge commit cannot be cleanly created.")
            base = git("rev-parse", "refs/heads/main").stdout.strip()
            message = values.get("--subject", pr["title"] or "")
            if values.get("--body"):
                message += "\n\n" + values["--body"]
            env = {**os.environ, "GIT_COMMITTER_NAME": "GitHub", "GIT_COMMITTER_EMAIL": "noreply@github.com"}
            commit = subprocess.run(["git", "-C", ORIGIN, "commit-tree", tree, "-p", base, "-m", message],
                                    capture_output=True, text=True, check=True, env=env).stdout.strip()
            git("update-ref", "refs/heads/main", commit, base)
            pr["state"] = "MERGED"
            pr["merge_commit"] = commit
        else:
            fail(f"fake gh: unmodelled call {sys.argv[1:]}")
    finally:
        save(state)


if __name__ == "__main__":
    main()
