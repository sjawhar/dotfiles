# Review findings and readiness evidence

Read when you close findings, request a head-bound review or prepare a packet. Use the repo's live reviewer and gate contract.

## Closure

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
  5. One comment, verdict first, naming the full head in the gate's grammar. Never write a verdict line in the request, because a parser can read it as an approval.
- Judge a round by its scope, not its minutes. A runtime-resource change needs real re-proof, not a textual re-cite.
- For silent failure modes, several readings of one guard cannot show coverage outside its reach. Use complementary reviewers, and have the tester plant the defect where the guard does not look ([proof design](proof-design.md)).

## Which verdicts count

- Bind a verdict to a commit ID, never a branch. If posting failed, the reviewer says so and posts it before you cite it. In a repo another person maintains, verdicts go on the tracking issue, and the PR links them in one line.
- GitHub rejects APPROVE and REQUEST_CHANGES from a shared App identity on its own PR. That is not a permissions defect. Post a COMMENT review or issue comment with the head-bound verdict first, in the gate's grammar. Explain the COMMENT state where needed.
- A REST reply can create an empty COMMENTED review. Check its reply link before you call it malformed.
- A malformed verdict can hold a real defect. Act on it, but do not cite it. Ask the reviewer to fix the form, with a link to the original. A valid approval in the wrong grammar needs one new comment in the right grammar, then a gate rerun.
- `pr-gate` reads the SHA from the verdict line, not the first hex string.
- A rejection stands until a positive verdict supersedes it. APPROVE, MERGE and LGTM are positive for all. The oracle's `Verdict: DOES NOT BLOCK at <sha>` only retracts its own BLOCKS at that SHA. No verdict closes an open finding without its disposition.

## The packet

- `pr-gate` checks head, scope, mergeability, threads, checks and verdicts. It does not read findings or prove paths for you. An initial UNKNOWN mergeability is not a conflict.
- Measure deploy consequence from the full file list and diff against the workflow filters, not the body or a stat. Read incidental changes. Reread rollback, version and deploy claims when the awaited event happens.
- Never call a missing required scenario "remaining work" in a ready report.
- No merge-queue organizer exists. The owner arms, and re-arms at each new sha.

## Public text

- Check the destination's audience before you share a citation or excerpt. A citation request is a publication request. Prefer a precedent the reader can open, read in full, and quote it.
- A redaction also covers inline comments' `diff_hunk`, replies on old content included. Keep needed discussion. Delete excerpts only with the required authority. Old commits and body edit history need the repo owner. Report which surfaces you cleared. A deleted branch does not delete history.
