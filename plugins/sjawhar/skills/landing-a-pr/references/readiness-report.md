# The readiness report

Read before you write the readiness report (the packet), after `pr-gate`. Which verdicts count is in `review-and-readiness.md`.

## The packet

- **Head and scope:** full sha, title, changed files, measured deploy consequence, production-behavior disclosure.
- **Verification:** scenarios, surface, outcome, duration and evidence links at this head. A waiver from the person who owns the work is a disclosed limit, never a pass.
- **CI:** full current-head check state and diagnosed failures.
- **Review state:** each review, comment and finding by its author's ID with its disposition: `fixed @ <commit>`, `rejected because <evidence>`, or `no reviews posted yet, waiting`. Cite verdicts by PR-comment URL, each bound to its sha, never by an agent message or session file.

## Notes

- `pr-gate` checks head, scope, mergeability, threads, checks and verdicts. It does not read findings or prove paths. An initial UNKNOWN mergeability is not a conflict.
- Measure deploy consequence from the full file list and diff against the workflow filters, not the body or a stat, and read the incidental changes too.
- Reread each rollback, version and deploy claim when the event it waits on happens (the deploy, the release, the base move).
- Reread the PR title and body against the final head: the squash merge message is built from them, and old subjects or retracted claims carry into main.
- A missing required scenario is a blocker, not "remaining work".
- With no merge-queue controller on the PR (`running-the-merge-queue`), the session that owns the PR enables auto-merge, and after each push disables and enables it again with the new sha (`repo-merge-rules.md`).
