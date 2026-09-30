# Review evidence and finding disposition

Read this before verifying READY, and again when a new review artifact appears.
The controller verifies counts, scope and dispositions. The owner and independent
reviewer decide whether a finding is blocking or refuted.

## Enumerate the actual population

Read the PR body and every page of these surfaces:

| Surface | Questions it must answer |
|---|---|
| GraphQL `reviewThreads` | Which authors/threads remain unresolved? What is each disposition? |
| Review bodies | What head and scope were reviewed? Any findings outside threads? |
| Issue comments | Any verdicts, unanchored findings, holds or closure evidence? |
| Linked gate/pair/oracle artifacts | What do they cover, at which head, with which gaps? |

The review object API and issue-comment API are different populations. Review
threads do not contain every finding from either. For REST collections use
pagination, for example:

```bash
gh api --paginate repos/O/R/pulls/N/reviews
gh api --paginate repos/O/R/issues/N/comments
gh api --paginate repos/O/R/pulls/N/files
gh api --paginate repos/O/R/pulls/N/commits
```

For GraphQL, follow `reviewThreads.pageInfo.endCursor` while `hasNextPage` is true.
If comments within a thread are paginated, read those pages too. Include every
author, not only the bot named in the owner's packet. A scoped query or truncated
body supports only a scoped count. Record the population and any unavailable pages.

Read the prose, not just tokens such as P1, Major, BLOCK or MERGE. A finding can
have no severity label; a positive verdict can quote a fixed historical blocker.
Never take the first few grep hits as the verdict. Read each hit in context and
the verdict's scope line before deciding what it answers.

## Bind each artifact to its question

For each verdict record:

- artifact URL and author/seat, including any substitution disclosure;
- reviewed SHA from its body and any mismatch with posting metadata;
- whole PR, appended delta, specific evidence or another stated scope;
- each finding and its owner's disposition, with the independent confirmation;
- which later artifact actually closes it, if any.

“Findings: none” for a comment-only delta does not clear a gate review's open
correctness finding about the PR. A gate ACCEPT likewise cannot erase another
reviewer's open finding. Neither seniority nor timestamp changes their subjects.
Read every applicable artifact at the current head and trace earlier open findings
to their closures; an outdated artifact can still contain an unresolved finding.

A head-naming verdict must be posted on the PR, as a review or issue comment. A
packet quotation, an owner's claim that it ran, or a private report alone does not
satisfy that requirement. Search the body and all comments for a claimed close,
then read it before merging. Do not merge first and locate the evidence afterward.

## Check dispositions without reviewing the code

A valid blocking finding is fixed in this PR. The owner records that disposition
and the independent reviewer confirms no blocker remains. A valid non-blocking
finding may instead name a follow-up PR, existing backlog item or next lane task;
its thread is replied to and resolved, and the merge record carries that destination.
A refutation needs the owner's evidence and the independent reviewer's confirmation.
Human threads remain blocking until the human resolves them.

If the packet's count disagrees with the census, return the undispositioned list.
Do not decide from a colored marker that a finding is harmless, nor promote a
non-blocking request into a new controller gate. Test-quality findings vary: a test
that passes on the bug it is cited to prove is blocking; a separate strengthening
request may be a non-blocking follow-up. The owner and reviewer make that call.

A resolved thread with no reply or destination can be an incomplete mutation, not
a legitimate shortcut. Verify the explanation and resolution separately. A batched
GraphQL reply can fail while its resolution succeeds; a successful overall call
does not establish that both happened.

## Independence and identity

An APPROVE under Sami's login may be an automated controller approval, not his
personal review. Read the body before attributing it. A controller's own earlier
“Verified ... at head” approval cannot become its independent verdict on retry.
Use current identity routing; historical use of a personal credential grants nothing.

For the pair, inspect the actual reports and recorded seats. Rubric names do not
prove the model, depth, tool access or cross-model independence that the review
requires. A quota error or policy refusal is not a review finding and not a pass.
Use the approved route, disclose substitutions and retain the failed attempt as
such. Do not invent fleet-wide replacement rules from one failed call.
