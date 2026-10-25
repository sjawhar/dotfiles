# Review findings and readiness evidence

Read when you close findings, request a head-bound review or prepare a packet. Use the repo's live reviewer and gate contract.

## Closure

- Under review, push only a blocking fix (one for a defect that would do harm once merged: wrong behavior, a security hole, bad data, or a false claim in shipped prose), with the other valid fixes folded in; otherwise put them in one follow-up PR after merge, cited as their disposition.
- Push before you reply, so each reply cites the commit and evidence; then resolve bot threads. If you share the reviewer's GitHub identity, leave resolving to the thread's opener. Replying in-thread to a colleague's review on work you own needs no approval.
- A review verdict at the new head, or one carried from an older head, does not replace the `ce-simplify-code` pass, the `thermonuclear-deep-review` and `thermonuclear-code-quality` reviews, or the `oracle` agent's check of the end-to-end evidence.
- Read every surface: threads, review bodies, issue comments, bot summaries, "Suggestions" and findings with no line anchor. Resolved threads do not prove nothing is open. Keep the reviewer's finding IDs.
- Use the babysitter snapshot as the index. Fetch bodies by node ID in one GraphQL request where possible, and reconcile pagination.
- Four separate questions: Is the finding valid at the head? Does it have a durable disposition? Did the right actor resolve its thread? Did the required reviewer judge the resulting head? A grep for stale wording answers none of the last three.
- Keep the reviewer's numbering. Keep no side ledger in place of GitHub replies.
- Converging reviewers locate a defect. They do not prove the remedy. A remedy or literal the reviewer did not execute is a candidate. Execute it against the contract. An executed counterexample justifies rejecting it, even when readers agree.
- Before you call a finding stale, check its definition, imports and live call sites at the head.
- The follow-up PR schedules non-blocking fixes, with `fixed in <PR>` as their disposition. It never closes an undisposed finding.
- Reply with the commit, current file and line, and proof, from a file you reviewed. Resolve a bot thread under another identity after that reply:

```sh
gh api repos/<owner>/<repo>/pulls/<N>/comments/<comment-id>/replies -F body=@<reply-file>
gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<node-id>"}) { thread { isResolved } } }'
```

- A summary finding has no thread. It still needs a reply with its ID and disposition.
- Resume the reviewer when each unresolved thread's newest comment is your `Fixed` or `Declined` with evidence, not when the push lands. Paginate threads before you compare counts.

## The next review

- Read the review workflow's triggers, path filters and concurrency. A manual comment can cancel a running review. When a trigger produced no run, request one by the repo's documented mechanism. Paths excluded from automatic review need that explicit request.
- An older approval or automatic engineer approval does not review the fixes.
- A re-review request names:
  1. The old verdict SHA and new full SHA, with ancestry or pr-gate's diff comparison.
  2. Files and delta, one sentence per change. Cite only line ranges you read. Otherwise name the diff.
  3. Each open finding's ID, status and evidence. For threads, the node ID and original commit ID.
  4. The probes or mutants each reviewer reruns at the new head.
  5. One comment, verdict first, naming the full head in the verdict-line format that `pr-gate` parses (read its source for the pattern). Never write a verdict line in the request, because a parser can read it as an approval.
- Decide how deep a re-review must go from what the round changed, not from how long it took. A runtime-resource change needs real re-proof, not a textual re-cite.
- For silent failure modes, several readings of one guard cannot show coverage outside its reach. Use complementary reviewers, and have the tester plant the defect where the guard does not look ([proof design](proof-design.md)).

## Review gates tied to a head

- A delta review covers the change since the last reviewed head and every open finding. "Findings: none" on a delta cancels no earlier finding, wherever it was posted.
- An old verdict carries to a head it did not review in two ways only: the new head is a forward merge of the reviewed head, or pr-gate's comparison of the PR's own old and new diffs ran. An identical diff needs a confirmation at the new head, not a new full review. A changed diff needs a review of the change. A comparison that did not run is a gap. Acceptance still runs at the new head.
- Run a delta review at high effort, scoped to the change since the last reviewed head plus open findings, with a tool-call budget and a deadline inside the gate's time cap. Use the highest effort for a first full review and for a review of a changed diff on a rewritten head. A budget or deadline never turns an incomplete review into an approval.
- Tell the gate to write its findings to its verdict file early, marked `VERDICT: PARTIAL`, and to update that file as it works, so a gate that stops at its cap still leaves its findings.
- A gate that ends at its cap with no verdict: read its transcript before you raise the cap or rerun it.

## Which verdicts count

- Bind a verdict to a commit ID, never a branch. If posting failed, the reviewer says so and posts it before you cite it. In a repo another person maintains, verdicts go on the tracking issue, and the PR links them in one line.
- GitHub rejects APPROVE and REQUEST_CHANGES from a shared App identity on its own PR. That is not a permissions defect. Post a COMMENT review or issue comment with the head-bound verdict first, in the verdict-line format `pr-gate` parses. Explain the COMMENT state where needed.
- A REST reply can create an empty COMMENTED review. Check its reply link before you call it malformed.
- A malformed verdict can hold a real defect. Act on it, but do not cite it. Ask the reviewer to fix the form, with a link to the original. A valid approval in the wrong format needs one new comment in the format `pr-gate` parses, then a gate rerun.
- `pr-gate` reads the SHA from the verdict line, not the first hex string.
- A rejection stands until a positive verdict supersedes it. APPROVE, MERGE and LGTM are positive for all. The oracle's `Verdict: DOES NOT BLOCK at <sha>` only retracts its own BLOCKS at that SHA. No verdict closes an open finding without its disposition.

## The packet

The readiness report, after `pr-gate`:

- **Head and scope:** full SHA, title, changed files, measured deploy consequence, production-behavior disclosure.
- **Verification:** scenarios, surface, outcome, duration, evidence links at this head. A waiver from the person who owns the work is a disclosed limit, never a pass.
- **CI:** full current-head check state and diagnosed failures.
- **Review state:** each review, comment and finding by its author's ID with its GitHub disposition: `fixed @ commit`, `rejected because <evidence>`, or `no reviews posted yet, waiting`. Cite verdicts by PR-comment URL, never an agent message or session file, each bound to its SHA.

- `pr-gate` checks head, scope, mergeability, threads, checks and verdicts. It does not read findings or prove paths for you. An initial UNKNOWN mergeability is not a conflict.
- Measure deploy consequence from the full file list and diff against the workflow filters, not the body or a stat. Read incidental changes. Reread rollback, version and deploy claims when the awaited event happens.
- Never call a missing required scenario "remaining work" in a ready report.
- With no merge-queue controller on the PR (`running-the-merge-queue`), the session that owns the PR runs `gh pr merge --auto --match-head-commit <sha>`; after each push it disables and enables auto-merge again with the new sha ([repo merge rules](repo-merge-rules.md)), because the old sha no longer matches the head.

## Public text

- Check the destination's audience before you share a citation or excerpt. A citation request is a publication request. Prefer to cite a source the reader can open. Read it in full before you quote it.
- A redaction also covers inline comments' `diff_hunk`, replies on old content included. Keep needed discussion. Delete an excerpt only after the person who owns the work approves it. Old commits and body edit history need the repo owner. Report which surfaces you cleared. A deleted branch does not delete history.
