# Review, merge and issue status

## Review

- The final `reviewer` after acceptance checks correctness, security, dead code, shims, aliases, dual paths and half-migrations. Do not give it a depth or a file subset.
- Resolve grounded findings (ones the code or a command output confirms) before you send the merge packet.
  - No other approval or follow-up assignment waives grounded findings.
- Ungrounded preferences do not bind.
- A re-review after fixes resumes the same reviewer session and reads only the diff since the head it last reviewed.
  - That re-review raises no finding it raised before, and none on code that diff does not touch.
- A fresh reviewer reads the PR whole.
- After a review round, do not repeat the thermonuclear pair when the diff from the head the pair judged to the new head touches only comments and docs.
- A docs-only or runtime-free diff skips `ce-simplify-code`.
- A docs-only or runtime-free diff skips the thermonuclear pair.
- The changed paths decide the skip, not a claim that the change looks safe.

## Judging the result

- An unmet requirement fails the review, whatever the branch, PR body or a report declares, unless the requester recorded before the review that it is out of this work.
  - A record is an ask the requester answered, a comment they wrote, or a spec version they approved without the requirement.
  - Silence, a reaction or an agent's own spec edit is no record.
  - Moving the missing part to a new issue is not acceptance.
- The `reviewer` dispatches one subagent to red-team the result against the goal and requirements, and its verdict names each red-team finding and what it did with it. A verdict without them is incomplete, and the work does not pass.

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
- At each transition, run the owner audit over the project you own: read `dispatch issues --project <KEY> --all`, every priority and not only P0 and P1, and act on each open row as "The owner audit" in `skill://legion-controller/references/backlog-stewardship.md` says.
- **Producer-contract visibility:** when a plan, spec or implementation changes a producer contract in a way a downstream surface shows:
  - the `needs_review` packet and the PR body name every known downstream surface and its visible delta;
  - each affected consumer's owner reviews that account before merge;
  - no consumer known: record the search that showed it;
  - this needs no approval from Sami.
