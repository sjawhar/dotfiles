# Review findings and readiness evidence

Read when you close findings, request a head-bound review or prepare the readiness report. The repo's live reviewer and gate contract decide where they differ.

## Closure

- Read every surface: threads, review bodies, issue comments, bot summaries, "Suggestions" and findings with no line anchor. Resolved threads do not prove nothing is open. Use the babysitter snapshot as the index, fetch bodies by node ID in one GraphQL request where possible, and reconcile pagination.
- Four separate questions: Is the finding valid at the head? Does it have a durable disposition? Did the right actor resolve its thread? Did the required reviewer judge the resulting head?
- Keep the reviewer's finding IDs and numbering. Keep no side ledger in place of GitHub replies.
- Converging reviewers locate a defect; they do not prove the remedy. Run a remedy the reviewer did not run against the contract before you adopt it. An executed counterexample justifies rejecting it.
- Before you call a finding stale, check its definition, imports and live call sites at the head.
- A follow-up PR schedules non-blocking fixes, with `fixed in <PR>` as their disposition. It never closes a finding that has no disposition.
- Push before you reply, so each reply cites the commit, the current file and line, and proof. Then resolve bot threads; if you share the reviewer's GitHub identity, leave resolving to the thread's opener. Replying in a colleague's review thread on work you own needs no approval.

```sh
gh api repos/<owner>/<repo>/pulls/<N>/comments/<comment-id>/replies -F body=@<reply-file>
gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<node-id>"}) { thread { isResolved } } }'
```

- A summary finding has no thread. It still needs a reply with its ID and disposition.
- Resume the reviewer when each unresolved thread's newest comment is your `Fixed` or `Declined` with evidence, not when the push lands.
- A verdict at the new head, or one carried from an older head, does not replace the `ce-simplify-code` pass, the `thermonuclear-deep-review` and `thermonuclear-code-quality` reviews, or the `oracle` agent's check of the end-to-end evidence.

## The next review

- Read the review workflow's triggers, path filters and concurrency: a manual comment can cancel a running review. When a trigger produced no run, or the paths are excluded from automatic review, request one by the repo's documented mechanism.
- An older approval, or an automatic engineer approval, does not review the fixes.
- A re-review request names:
  1. The old verdict sha and the new full sha, with ancestry or `pr-gate`'s diff comparison.
  2. Files and delta, one sentence per change. Cite only line ranges you read; otherwise name the diff.
  3. Each open finding's ID, status and evidence; for threads, the node ID and original commit ID.
  4. The probes or mutants each reviewer reruns at the new head.
  5. What to post: one comment, verdict first, naming the full head in the verdict-line format `pr-gate` parses (read its source for the pattern). Do not write a verdict line in the request itself: the parser can read it as an approval.
- Decide how deep a re-review goes from what the round changed. A runtime-resource change needs real re-proof, not a textual re-cite.
- For silent failure modes, several readings of one guard cannot show coverage outside its reach. Use complementary reviewers, and have the tester plant the defect where the guard does not look (`guard-probes.md`).

## Review gates tied to a head

- A delta review covers the change since the last reviewed head and every open finding. "Findings: none" on a delta cancels no earlier finding.
- An old verdict carries to a head it did not review only when the new head is a forward merge of the reviewed head, or `pr-gate`'s comparison of the PR's own old and new diffs ran. An identical diff needs a confirmation at the new head; a changed diff needs a review of the change. Acceptance still runs at the new head.
- Run a delta review at high effort, scoped to the change since the last reviewed head plus open findings, with a tool-call budget and a deadline inside the gate's time cap. Use the highest effort for a first full review and for a changed diff on a rewritten head. A budget never turns an incomplete review into an approval.
- Tell the gate to write its findings to its verdict file early, marked `VERDICT: PARTIAL`, and to update it as it works.
- A gate that ends at its cap with no verdict: read its transcript before you raise the cap or rerun it.

## Which verdicts count

- Bind a verdict to a commit ID, never a branch. In a repo another person maintains, verdicts go on the tracking issue, and the PR links them in one line.
- The reviewer is a different agent, not a different account: dispatch your own fresh subagent of another model family to review the head, then post its verdict yourself. Do not send your PR to another session for review, and do not review other sessions' PRs.
- GitHub rejects APPROVE and REQUEST_CHANGES from a shared App identity on its own PR. Post a COMMENT review or issue comment with the head-bound verdict instead. A COMMENT never changes `reviewDecision`, so a branch rule that requires an approval still needs one from another identity (`repo-merge-rules.md`).
- A REST reply can create an empty COMMENTED review; check its reply link before you call it malformed.
- A malformed verdict can hold a real defect: act on it, but do not cite it. A valid approval in the wrong format needs one new comment in the format `pr-gate` parses, then a gate rerun.
- A rejection stands until a positive verdict supersedes it. The oracle's `Verdict: DOES NOT BLOCK at <sha>` only retracts its own BLOCKS at that sha. No verdict closes an open finding without its disposition.

## The packet

The readiness report, after `pr-gate`:

- **Head and scope:** full sha, title, changed files, measured deploy consequence, production-behavior disclosure.
- **Verification:** scenarios, surface, outcome, duration and evidence links at this head. A waiver from the person who owns the work is a disclosed limit, never a pass.
- **CI:** full current-head check state and diagnosed failures.
- **Review state:** each review, comment and finding by its author's ID with its disposition: `fixed @ <commit>`, `rejected because <evidence>`, or `no reviews posted yet, waiting`. Cite verdicts by PR-comment URL, each bound to its sha, never by an agent message or session file.

Notes:

- `pr-gate` checks head, scope, mergeability, threads, checks and verdicts. It does not read findings or prove paths. An initial UNKNOWN mergeability is not a conflict.
- Measure deploy consequence from the full file list and diff against the workflow filters, not the body or a stat.
- Reread the PR title and body against the final head: the squash merge message is built from them, and old subjects or retracted claims carry into main.
- A missing required scenario is a blocker, not "remaining work".
- With no merge-queue controller on the PR (`running-the-merge-queue`), the session that owns the PR enables auto-merge, and after each push disables and enables it again with the new sha (`repo-merge-rules.md`).

## Public text

- Check the destination's audience before you share a citation or excerpt. Prefer a source the reader can open, and read it in full before you quote it.
- A redaction also covers inline comments' `diff_hunk`, replies on old content included. Delete an excerpt only after the person who owns the work approves it; old commits and body edit history need the repo owner. A deleted branch does not delete history.
