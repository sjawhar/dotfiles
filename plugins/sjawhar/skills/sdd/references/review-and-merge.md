# Review, merge and issue-status detail

## Review

- The final `reviewer` after acceptance checks correctness, security, dead code, shims, aliases, dual paths and half-migrations. It decides for itself how deeply to read; do not give it a depth or a file subset.
- Resolve grounded findings (ones the code or a command output confirms) before you send the merge packet; no other approval or follow-up assignment waives them. Ungrounded preferences do not bind.
- Runtime changes: `ce-simplify-code` once per PR after the last review round, scoped to its diff. An applied change becomes the final head for CI, review and refreshed real-surface proof. A pass with no change is normal.
- Then dispatch `thermonuclear-deep-review` and `thermonuclear-code-quality` yourself, once at that final head (the `reviewer` agent cannot dispatch them), and give their findings to the final reviewer to weigh.
- Prose-only or minor changes: do not repeat the pair. Docs-only or runtime-free diffs skip both simplify and the pair. The changed paths decide the skip, never a claim that the change looks safe.

## Gate ledger

Keep one row per PR and one cell per required gate. At the packet, fill each cell from the artifact
at the head (the comment, the check run, the summary), never from memory or a summary: the
artifacts show a skipped gate, an automated review on an earlier commit, and a non-blocking item
left unapplied under an approved head.

## PRs

- The implementer opens each PR under `opening-a-pr` and owns `landing-a-pr`, resumed for checks, reviews and conflicts.
- When a merge-queue controller runs (`running-the-merge-queue`), send it the packet and it merges. Otherwise the implementer runs `gh pr merge --auto`.
- Each stacked PR follows the same path, plus a reviewer as it lands. Use `gh-stack` for dependent PRs and `squash-stack` for finished stacks it applies to.
- If consolidation changes the delivered diff, refresh acceptance and PR gates. Do not stop to ask about stacking.

## Issue status

- A green suite, a harness screenshot or a merge is groundwork, not `done`.
- Never ask Sami or the requester to look at or approve the result before `done`. Close with a reason: `dispatch issue-update --issue <KEY> --status done --reason '<what shipped; the check>'` (it refuses `done` without one).
- An issue left at `triage` while work runs is a defect: the Dispatch roadmap view reads these statuses, and they are his only view of delivery that does not interrupt a session.
- Waiting for the deploy session or CI job is not a status. The issue stays at `needs_review` until your own production check passes, then you move it to `done`; do not announce the wait.
- At each transition, run the owner audit from the `dispatch` skill over the project you own.
- **Producer-contract visibility.** When a plan, spec or implementation changes a producer contract in a way a downstream surface shows, the `needs_review` packet and the PR body name every known downstream surface and its visible delta, and each affected consumer's owner reviews that account before merge. No consumer known: record the search that showed it. This needs no approval from Sami.
