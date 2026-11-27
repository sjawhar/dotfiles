# Review evidence and finding disposition

Read before you verify READY, and again when a new review artifact appears.

## Enumerate the population

- Read every review surface as `landing-a-pr`'s review-and-readiness reference says ("Closure"), on every page. The queue adds:
  - the PR body (claims, verification, linked artifacts) and the linked gate, pair and oracle artifacts (coverage, head, gaps);
  - threads, review bodies and issue comments, each read on its own, since a finding can appear in one of them and not the others: GraphQL `reviewThreads` (unresolved threads by author, each disposition), review bodies (reviewed head and scope, findings outside threads), issue comments (verdicts, unanchored findings, holds, closure evidence);
  - every page of each: in REST, one `gh api --paginate` call per collection (`repos/O/R/pulls/N/reviews`, `.../files`, `.../commits`, `repos/O/R/issues/N/comments`); in GraphQL, follow `reviewThreads.pageInfo.endCursor` while `hasNextPage`, and page the comments inside each thread too;
  - every author, not only the bot the packet names. A scoped query or truncated body supports only a scoped count: record the population and the pages you could not read;
  - prose, not tokens such as P1, Major, BLOCK, MERGE. A finding can have no label, and a positive verdict can quote a fixed historical blocker. Read each hit in context with the verdict's scope line.

## Bind each artifact

- Bind each artifact to its subject, reviewed SHA, author and finding scope. An unrelated green artifact never cancels another's open finding, in either direction. A narrow delta review does not clear the whole PR.
- Take the reviewed SHA from the review body, not `commit_id`, which can name the head at posting time. The body SHA is still a claim to check. If it is missing or contradictory, ask; never substitute metadata.
- Record its URL, author and seat (with any substitution disclosure), body SHA and any metadata mismatch, and scope. Add each finding with its disposition and confirmation, and the later artifact that closes it.
- "Findings: none" on a comment-only delta does not clear a gate review's open correctness finding. A gate ACCEPT cannot erase another reviewer's open finding. A later or more senior reviewer's verdict on a different subject does not close this finding.
- An outdated artifact can still hold an open finding. Trace each earlier finding to its closure.
- A head-naming verdict is a review or issue comment on the PR. A packet quote, an owner's claim or a private report is not enough.
- Do not call a finding harmless from a colored marker, or promote a non-blocking request into a new controller gate. A test-strengthening request can be a non-blocking follow-up; the owner and reviewer decide.
- A resolved thread with no reply or destination can be a failed mutation: a batched GraphQL reply can fail while its resolution succeeds. Verify each one.
- Before merging, find each claimed closure posted on the PR.

## Disposition of each finding

- Correctness or security defect in shipped behavior: blocking, fixed in this PR.
- Test that would pass on the bug it is offered to prove: blocking, fixed in this PR.
- Other valid finding: fixed, or a named destination (follow-up PR, existing backlog item, next task of that session) in the thread reply and merge record, thread resolved. "Later" is not a destination.
- Refuted: the owner's evidence plus independent confirmation, never a silently resolved thread.
- Human thread: blocking until the human resolves it.
- One push per review round, not per finding. Never tell an owner to stop pushing a correctness fix. Do not turn a wording change in text that nothing executes, or a change to a finding's disposition reply, into a new review round.

## Simplify and the thermonuclear pair

For runtime code, CI configuration, scripts, Dockerfiles and infrastructure, in this order:

1. Finish the ordinary review rounds.
2. The owner (under `sdd`, the coordinator) runs `ce-simplify-code` once, with GPT reviewers, on the PR's own diff. If it changes code, that is the final code head: rerun CI and acceptance for the affected surface there.
3. The owner (under `sdd`, the coordinator) runs the thermonuclear pair (`thermonuclear-deep-review` and `thermonuclear-code-quality`) once at the final code head, as the last review before merge. Both verdicts and dispositions go on the PR.

Not per push, not after merge. The only exceptions, none of them a waiver for runtime code:

- A round after the pair that the owner documents as prose-only, and whose diff between the two heads touches only comments and docs, keeps the verdict and the pair. Check that diff by [head changes](head-changes.md) section 3, not the owner's word.
- A prose, comment or docstring-only PR: one reviewer, no simplify, no pair.
- A test-only PR: one reviewer and no pair, if adversarial exercises show the assertions are non-vacuous in both directions and no gate was deleted or loosened.
- A test-only delta on a paired PR keeps the pair, with reviewer confirmation of the new head.

For `sdd` work the packet also names the head the FINAL reviewer (the review `sdd` runs after acceptance) read. The pair does not replace that review or the independent review.

## Independence and identity

- Seats: read the actual reports and substitution disclosures. A rubric loaded into a different seat is not the named review, and a rubric name does not prove the model, depth, tool access or cross-model independence.
- A quota error, provider error or refusal is neither a verdict nor a finding. Use the approved route, disclose the actual seat, and keep the failed attempt recorded. Never silently swap a cross-model review for the same family, and never turn one failed call into a fleet-wide policy.
- An APPROVE under Sami's login can be an automated controller approval. Read its body before you attribute it.
- Your own earlier "Verified ... at head" approval never becomes your independent verdict on retry.
