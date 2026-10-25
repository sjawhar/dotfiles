# History, conflicts and push rounds

Read before you resolve a conflict, integrate a base, change a stack, or push after a review. `using-jj` has the commands and shared-store safety.

## When to integrate the base

- Integrate for a real conflict, a required dependency, or a decision by the person who owns the work (or the stack's owner) to integrate; never only to be up to date with the base.
- On a published or reviewed head, merge forward. A rewrite deletes the commit its verdict names. Never rebase, amend, describe over or squash into a commit that the remote or a reviewer holds.
- Unpublished work follows `using-jj`, with its ownership and descendant checks. This never permits rewriting another session's stack or bypassing immutability.
- When another session rewrites the parent PR, ask it for one parent SHA it will not change before your next push, and build on that SHA only. Do not rebuild on each of its pushes.
- After a parent squash-merges, its old commits are not ancestors of the squash commit, though their content is there. Diagnose duplicate content before you pick a fix. Never replay or discard another session's commits on an ancestry guess.

## Forward fix of a published head

1. Read the remote head, base and ownership. Fetch through jj. Check which commit `@` is.
2. `jj new <published-head> <base>`. Use `jj new @ <base>` only when `@` is exactly that head.
3. Resolve each conflict by what both sides meant to change, not by picking one side's text. If text states a count ("12 checks"), count again in the merged tree. For a positional list, check its consumer: length, maximum index, deliberately unread slots. Two clean appends can break it with no conflict marker.
4. Find the intended merge base from the graph, never by a blind pick in a criss-cross. Read `jj diff --git --from <verified-merge-base> --to <head>` with concrete revisions, merge-resolution content included. A patch-id list misses that content.
5. Record what each side gave, what you kept, and the delta. Run the affected behavior at the new head, including earlier fixes a diff selector no longer picks.
6. Check that the old head is still an ancestor: `jj log -r '<published-head> & ::<candidate-head>'`. Empty means lost. Read `jj git push --dry-run --bookmark <name>` in its own call. Stop on sideways, backwards or deletion.
7. Push only that bookmark. Check that `jj log -r '<name>@origin'` names the intended commit. Run `jj new` in the same `&&` chain as the push.
8. Request a verdict at the new SHA over the real delta and all open findings. Ancestry permits a delta review. It does not certify behavior.

Chain dependent mutations with `&&`. Run the check that decides whether you may push in its own tool call, and read its output before the push. Never gate a push on a paged pipeline's status.

## Verdict carry and comparisons

- A verdict names a commit, not a bookmark. Check ancestry with `jj log -r '<verdict-sha> & ::<new-head>'`, and carry it only as [review and readiness](review-and-readiness.md), "Review gates tied to a head", says. "I only appended" is intent, not evidence.
- Name the base and tip at both ends of a comparison, and make both fetchable by other readers. List the commits, not just a count. Mark as unverified any old-to-new commit mapping that rests on local state other readers cannot fetch.
- A two-commit diff and a merge-base diff answer different questions. To see what changed between two commits, read the files at both. For the PR's contribution, diff from the intended merge base, merge-resolution content included.
- Where a new base can change behavior, run one real matrix on old base, old head, new base and new head. Use one environment, and rerun old captures in the same window. Compare the patch effects. State what the matrix cannot see. A base change can explain an absolute difference, never a changed patch effect.

## One wave per round

- Before you dispatch a head-bound review, read the state of the PR ahead of this one in the merge queue. If a base integration will soon be required, include it in the next push. Do not wait on unrelated predecessors or a moving main.
- While a reviewer judges a held head, a plain jj read can snapshot the working copy and so rewrite a held commit you edited. Stage on an unpushed child or scratch tree, and combine it with a blocking fix on purpose. Never move or change the checkout while a long run reads it. Runtime changes void acceptance rows from the prior head.
- A blocking defect after READY: run `gh pr view <N> --json state` right before you post HOLD (your request to stop the merge) on the PR. If it merged, fix it as a new change.

## Unrequested changes

- Disclose an accidental rewrite or unrequested base merge. Never hide it with a force push. Use `using-jj`'s forward recovery; in a shared store never run `jj undo`/`jj op restore`, or a rewrite over a revset that matches commits you did not make.
