# Review, merge and issue status

## Review

- The final `reviewer` after acceptance checks correctness, security, dead code, shims, aliases, dual paths and half-migrations. Do not give it a depth or a file subset.
- Resolve grounded findings (ones the code or a command output confirms) before you send the merge packet; no other approval waives them. Ungrounded preferences do not bind.
- A re-review after fixes resumes the same reviewer session and reads only the diff since the head it last reviewed: it raises no finding it raised before and none on code that diff does not touch. A fresh reviewer reads the PR whole.
- A prose-only or minor change does not repeat the thermonuclear pair after a review round. A docs-only or runtime-free diff skips `ce-simplify-code` and the pair. The changed paths decide the skip, not a claim that the change looks safe.

## Gate ledger

Keep one row per PR and one cell per required gate. At the packet, fill each cell from the artifact at the head (the comment, the check run, the summary), never from memory: the artifacts show a skipped gate, an automated review of an earlier commit, or a non-blocking item left unapplied. `~/.dotfiles/plugins/sjawhar/scripts/pr-gates.sh <PR>...` lists which gates have judged each PR's current head (`REPO=owner/name` for a repository other than Legion).

## PRs

- The implementer opens each PR under `opening-a-pr` and owns `landing-a-pr` for its checks, reviews and conflicts.
- When a merge-queue controller runs (`running-the-merge-queue`), send it the packet and it merges. Otherwise the implementer runs `gh pr merge --auto`.
- A stacked PR follows the same path, plus a reviewer as it lands: `gh-stack` for dependent PRs, `squash-stack` for a finished stack.
- If consolidation changes the delivered diff, refresh acceptance and the PR gates. Do not stop to ask about stacking.

## Issue status

- Do not ask Sami or the requester to look at or approve the result before `done`. Close with `dispatch issue-update --issue <KEY> --status done --reason '<what shipped; the check>'`.
- An issue left at `triage` while work runs is a defect: the Dispatch roadmap view reads these statuses.
- Waiting for a deploy or a CI job is not a status: the issue stays `needs_review` until your own production check passes. Do not announce the wait.
- A producer-contract change: the `needs_review` packet carries the downstream delta `opening-a-pr` asks of the PR.
