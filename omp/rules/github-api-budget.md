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

- Read `X-RateLimit-Remaining` and `X-RateLimit-Reset` from your own calls' responses (`gh api -i`, or `--include` on the first call of a batch), and keep at least 5,000 calls of the hour for the other sessions. Stop the script when `remaining` falls under that. **Do not use `gh api rate_limit` for this**: it can read a different counter from the one your calls spend. On 2026-11-27 theorem-labs' installation was refusing calls with `403` and `X-RateLimit-Remaining: 0` from 21:04Z, while `gh api rate_limit` at 21:12:26Z answered `core 14,601 remaining, reset 21:39:17Z`; the real reset was 21:16:17Z.
- A script that stops on the budget must tell a refusal from a result: a `403` body parses as an empty list, so a sweep that treats it as data reports "nothing found" instead of stopping. Print UNKNOWN on any error and test both ways before trusting it.
- Store each answer on disk and read the store before you fetch. A finished run attempt (`actions/runs/<id>/attempts/<n>/jobs`), a merged pull request and a commit do not change, so fetch each one once, ever. Look for an earlier sweep's store under `/mnt/nvme/<user>/tmp` and `/mnt/nvme/<user>/scratch` first.
- Use one GraphQL query for many objects (25 pull requests per query, with aliases) in place of one REST call each. GraphQL has a budget of its own.
- Run at most 4 calls in parallel.
