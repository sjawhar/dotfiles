# Review evidence and finding disposition

Read before you verify READY, and again when a new review artifact appears.

## Enumerate the population

| Surface (every page) | What it answers |
|---|---|
| PR body | Claims, verification, linked artifacts |
| GraphQL `reviewThreads` | Unresolved threads by author. Each disposition |
| Review bodies | Reviewed head and scope. Findings outside threads |
| Issue comments | Verdicts, unanchored findings, holds, closure evidence |
| Linked gate, pair and oracle artifacts | Coverage, head, gaps |

- Reviews and issue comments are different populations. Threads hold neither's full set of findings.
- REST: one `gh api --paginate` call per collection: `repos/O/R/pulls/N/reviews`, `.../files`, `.../commits`, `repos/O/R/issues/N/comments`.
- GraphQL: follow `reviewThreads.pageInfo.endCursor` while `hasNextPage`. Page the comments inside a thread too.
- Include every author, not only the bot the packet names. A scoped query or truncated body supports only a scoped count. Record the population and the pages you could not read.
- Read prose, not tokens such as P1, Major, BLOCK, MERGE. A finding can have no label. A positive verdict can quote a fixed historical blocker. Read each hit in context with the verdict's scope line.

## Bind each artifact

- Record its URL, author and seat (with any substitution disclosure), body SHA and any metadata mismatch, and scope. Add each finding with its disposition and confirmation, and the later artifact that closes it.
- "Findings: none" on a comment-only delta does not clear a gate review's open correctness finding. A gate ACCEPT cannot erase another reviewer's open finding. Seniority and timestamps do not change subjects.
- An outdated artifact can still hold an open finding. Trace each earlier finding to its closure.
- A head-naming verdict is a review or issue comment on the PR. A packet quote, an owner's claim or a private report is not enough.
- Do not call a finding harmless from a colored marker, or promote a non-blocking request into a new controller gate. A test strengthening request can be a non-blocking follow-up. The owner and reviewer decide.
- A resolved thread with no reply or destination can be a failed mutation. A batched GraphQL reply can fail while its resolution succeeds. Verify each one.

## Independence and identity

- An APPROVE under Sami's login can be an automated controller approval. Read its body before you attribute it.
- Your own earlier "Verified ... at head" approval never becomes your independent verdict on retry.
