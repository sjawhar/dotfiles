---
description: The shared GitHub API budget, for a script that calls gh once per run, pull request or commit
condition:
  - 'xargs[^\n]*-P\s*\d+[^\n]*\bgh\b'
  - 'actions/runs/(\{|\$|%s|%d|"\s*\+|''\s*\+)'
  - '\bgh\b[^\n]*(pulls|commits|check-runs|check-suites)/(\{|\$\{?[A-Za-z_])'
scope: "tool:bash, tool:eval, tool:write"
interruptMode: never
---
# The GitHub API budget is shared

Every agent session reaches GitHub through one App installation per organization. Its REST budget (15,000 calls an hour on theorem-labs, 11,650 on sjawhar) is shared by every session on every machine, and a script that spends it stops every lane's `gh` until the hour resets. On 2026-11-03 three CI sweeps of `actions/runs/<id>/jobs` over about 10,000 runs spent the whole theorem-labs hour by 07:58Z.

Before a script makes more than about 500 calls:

- Run `gh api rate_limit` (free) and keep at least 5,000 calls of the hour for the other sessions. Stop the script when `remaining` falls under that.
- Store each answer on disk and read the store before you fetch. A finished run attempt (`actions/runs/<id>/attempts/<n>/jobs`), a merged pull request and a commit do not change, so fetch each one once, ever. Look for an earlier sweep's store under `/mnt/nvme/<user>/tmp` and `/mnt/nvme/<user>/scratch` first.
- Use one GraphQL query for many objects (25 pull requests per query, with aliases) in place of one REST call each. GraphQL has a budget of its own.
- Run at most 4 calls in parallel.
- Do not set `GH_READ_CACHE=0` for a sweep: the read cache answers repeated reads with no request.
