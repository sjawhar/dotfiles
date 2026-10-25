# Review evidence and finding disposition

Read before you verify READY, and again when a new review artifact appears.

## Enumerate the population

- Enumerate all authors and all pages of review threads, review bodies, issue comments, the PR body and linked verdict artifacts. Zero unresolved threads is not zero open findings. Read each whole verdict, not a grep for severity markers.
| Surface (every page) | What it answers |
|---|---|
| PR body | Claims, verification, linked artifacts |
| GraphQL `reviewThreads` | Unresolved threads by author. Each disposition |
| Review bodies | Reviewed head and scope. Findings outside threads |
| Issue comments | Verdicts, unanchored findings, holds, closure evidence |
| Linked gate, pair and oracle artifacts | Coverage, head, gaps |

- Read reviews, issue comments and threads separately: a finding can appear in any one of them and not the others.
- REST: one `gh api --paginate` call per collection: `repos/O/R/pulls/N/reviews`, `.../files`, `.../commits`, `repos/O/R/issues/N/comments`.
- GraphQL: follow `reviewThreads.pageInfo.endCursor` while `hasNextPage`. Page the comments inside a thread too.
- Include every author, not only the bot the packet names. A scoped query or truncated body supports only a scoped count. Record the population and the pages you could not read.
- Read prose, not tokens such as P1, Major, BLOCK, MERGE. A finding can have no label. A positive verdict can quote a fixed historical blocker. Read each hit in context with the verdict's scope line.

## Bind each artifact

- Bind each artifact to its subject, reviewed SHA, author and finding scope. An unrelated green artifact never cancels another's open finding, in either direction. A narrow delta review does not clear the whole PR.
- Take the reviewed SHA from the review body, not `commit_id`, which can name the head at posting time. The body SHA is still a claim to check. If it is missing or contradictory, ask. Never substitute metadata.
- Record its URL, author and seat (with any substitution disclosure), body SHA and any metadata mismatch, and scope. Add each finding with its disposition and confirmation, and the later artifact that closes it.
- "Findings: none" on a comment-only delta does not clear a gate review's open correctness finding. A gate ACCEPT cannot erase another reviewer's open finding. A later or more senior reviewer's verdict on a different subject does not close this finding.
- An outdated artifact can still hold an open finding. Trace each earlier finding to its closure.
- A head-naming verdict is a review or issue comment on the PR. A packet quote, an owner's claim or a private report is not enough.
- Do not call a finding harmless from a colored marker, or promote a non-blocking request into a new controller gate. A test strengthening request can be a non-blocking follow-up. The owner and reviewer decide.
- A resolved thread with no reply or destination can be a failed mutation. A batched GraphQL reply can fail while its resolution succeeds. Verify each one.
- Before merging, find each claimed closure posted on the PR.

## Disposition of each finding

| Finding | Disposition |
|---|---|
| Correctness or security defect in shipped behavior | Blocking. Fixed in this PR |
| Test that would pass on the bug it is offered to prove | Blocking. Fixed in this PR |
| Other valid finding | Fixed, or a named destination (follow-up PR, existing backlog item, next task of that session) in the thread reply and merge record, thread resolved. "Later" is not a destination |
| Refuted | Owner's evidence plus independent confirmation, never a silently resolved thread |
| Human thread | Blocking until the human resolves it |

- One push per review round, not per finding. Never tell an owner to stop pushing a correctness fix. Do not turn minor wording or disposition updates into new review rounds.

## Simplify and the thermonuclear pair

For runtime code, CI configuration, scripts, Dockerfiles and infrastructure, in this order:

1. Finish the ordinary review rounds.
2. The owner (under `sdd`, the coordinator) runs `ce-simplify-code` once, with GPT reviewers, on the PR's own diff. If it changes code, that is the final code head: rerun CI and acceptance for the affected surface there.
3. The owner (under `sdd`, the coordinator) runs the thermonuclear pair (the `thermonuclear-deep-review` and `thermonuclear-code-quality` agents) once at the final code head, as the last review before merge. Both verdicts and dispositions go on the PR.

Not per push, not after merge. The only exceptions, none of them a waiver for runtime code:

| Scoped exception | Needs |
|---|---|
| Documented minor-only prose round after the pair | Keeps verdict and pair. Check its scope by [head changes](head-changes.md), not the owner's word |
| Prose, comment or docstring-only PR | One reviewer. No simplify, no pair |
| Test-only PR | One reviewer, no pair, if adversarial exercises show the assertions are non-vacuous in both directions and no gate was deleted or loosened |
| Test-only delta on a paired PR | Keeps the pair, with reviewer confirmation of the new head |

For `sdd` work, the packet also names the head that the FINAL reviewer (the review `sdd` runs after acceptance) read; the pair does not replace that review or the independent review.

## Independence and identity

- Seats: read the actual reports and substitution disclosures. A rubric loaded into a different seat is not the named review. A rubric name does not prove the model, depth, tool access or cross-model independence. A quota error, provider error or refusal is neither a verdict nor a finding. Use the approved route, disclose the actual seat, and keep the failed attempt recorded. Never silently swap a cross-model review for the same family. Never turn one failed call into a fleet-wide policy.

- An APPROVE under Sami's login can be an automated controller approval. Read its body before you attribute it.
- Your own earlier "Verified ... at head" approval never becomes your independent verdict on retry.
