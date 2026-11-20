# Review findings and readiness evidence

Read when you find and close findings, or request a head-bound review. The repo's live reviewer and gate contract decide where they differ.

## Closure

- Read every surface: threads, review bodies, issue comments, bot summaries, "Suggestions" and findings with no line anchor. Resolved threads do not prove nothing is open. Use the babysitter snapshot as the index, fetch bodies by node ID with one GraphQL `nodes(ids: [...])` request per 100 IDs (GitHub refuses more in one request), and reconcile pagination.
- Four separate questions: Is the finding valid at the head? Does it have a durable disposition? Did the right actor resolve its thread? Did the required reviewer judge the resulting head?
- Keep the reviewer's finding IDs and numbering. Record each disposition as a GitHub reply (in its thread, or a PR comment for a summary finding): a disposition kept only in a local file or a message counts as none.
- Under review, push only a blocking fix (one for a defect that would do harm once merged: wrong behavior, a security hole, bad data, or a false claim in shipped prose), with the other valid fixes folded into that push; otherwise put them in one follow-up PR after the merge, cited as their disposition.
- Converging reviewers locate a defect; they do not prove the remedy. Run a remedy the reviewer did not run against the contract before you adopt it. An executed counterexample justifies rejecting it.
- Before you call a finding stale, check its definition, imports and live call sites at the head.
- A follow-up PR schedules non-blocking fixes, with `fixed in <PR>` as their disposition. It never closes a finding that has no disposition.
- Push before you reply, so each reply cites the commit, the current file and line from a file you reviewed, and proof. Then resolve bot threads; if you share the reviewer's GitHub identity, leave resolving to the thread's opener. Replying in a colleague's review thread on work you own needs no approval.

```sh
gh api repos/<owner>/<repo>/pulls/<N>/comments/<comment-id>/replies -F body=@<reply-file>
gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<node-id>"}) { thread { isResolved } } }'
```

- A summary finding has no thread. It still needs a reply with its ID and disposition.
- Resume the reviewer when each unresolved thread's newest comment is your `Fixed` or `Declined` with evidence, not when the push lands. Paginate threads before you compare counts.
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
- Set a re-review's depth from what the round changed, not from how long it took: by default a delta review of the change and every open finding ("Review gates tied to a head"); real re-proof, not a textual re-cite, for a change to what the code reads or reaches at run time (a config, a credential, a schema, a service); a full review at the highest effort for a rewritten head whose diff changed.
- For silent failure modes, several readings of one guard cannot show coverage outside its reach. Use complementary reviewers, and have the tester plant the defect where the guard does not look (`guard-probes.md`).

## Review gates tied to a head

- A delta review covers the change since the last reviewed head and every open finding. "Findings: none" on a delta cancels no earlier finding.
- An old verdict carries to a head it did not review only when the new head is a forward merge of the reviewed head, or `pr-gate`'s comparison of the PR's own old and new diffs ran. An identical diff needs a confirmation at the new head; a changed diff needs a review of the change. Acceptance still runs at the new head.
- Run a delta review at high effort, scoped to the change since the last reviewed head plus open findings, with a tool-call budget and a deadline inside the gate's time cap. Use the highest effort for a first full review and for a changed diff on a rewritten head. A budget never turns an incomplete review into an approval.
- Tell the gate to write its findings to its verdict file early, marked `VERDICT: PARTIAL`, and to update it as it works.
- A gate that ends at its cap with no verdict: read its transcript before you raise the cap or rerun it.

## Which verdicts count

- Bind a verdict to a commit ID, never a branch. If posting failed, the reviewer says so and posts it before you cite it. In a repo another person maintains, verdicts go on the tracking issue, and the PR links them in one line.
- The reviewer is a different agent, not a different account: dispatch your own fresh subagent of another model family to review the head, then post its verdict yourself, with an identity the repository's section of `repo-merge-rules.md` names or as the COMMENT below. Do not send your PR to another session for review, and do not review or place reviews for other sessions' PRs: a session that reviews for others becomes the queue every author waits on.
- GitHub rejects APPROVE and REQUEST_CHANGES from a shared App identity on its own PR. Post a COMMENT review or issue comment with the head-bound verdict first, in the verdict-line format `pr-gate` parses. When the base branch requires an approving review, explain the COMMENT state in that comment: a COMMENT never changes `reviewDecision`, so that rule still needs an approval from another identity (`repo-merge-rules.md`).
- A REST reply can create an empty COMMENTED review; check its reply link before you call it malformed.
- A malformed verdict can hold a real defect: act on it, but do not cite it, and ask the reviewer to fix the form, with a link to the original. A valid approval in the wrong format needs one new comment in the format `pr-gate` parses, then a gate rerun. `pr-gate` reads the sha from the verdict line, not the first hex string.
- A rejection stands until a positive verdict supersedes it; APPROVE, MERGE and LGTM are positive for all. The oracle's `Verdict: DOES NOT BLOCK at <sha>` only retracts its own BLOCKS at that sha. No verdict closes an open finding without its disposition.

## Public text

- Check the destination's audience before you share a citation or excerpt: a citation request is a publication request. Prefer a source the reader can open, and read it in full before you quote it.
- A redaction also covers inline comments' `diff_hunk`, replies on old content included. Edit out only the redacted text, and keep the rest of each comment (findings, replies, dispositions). Delete an excerpt only after the person who owns the work approves it; old commits and body edit history need the repo owner. Report which surfaces you cleared. A deleted branch does not delete history.
