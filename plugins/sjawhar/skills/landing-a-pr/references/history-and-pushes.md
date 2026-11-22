# History, conflicts and push rounds

Read before you resolve a conflict, integrate a base, change a stack, or push after a review. `using-jj` has the commands and the shared-store rules.

## When to integrate the base

- Integrate for a real conflict, a required dependency, or a decision by the person who owns the work (or the stack's owner); not only to be up to date.
- Unpublished work follows `using-jj`, with its ownership and descendant checks.
  - That never permits rewriting another session's stack or bypassing immutability.
- When another session rewrites the parent PR, ask it for one parent sha it will not change before your next push.
  - Build only on that sha.
- After a parent squash-merges, its old commits are not ancestors of the squash commit, though their content is there.
  - Diagnose duplicate content before you pick a fix.
  - Do not replay or discard another session's commits on an ancestry guess.

## Forward fix of a published head

1. **Read the state.** Read the remote head, base and ownership.
   - Fetch through jj.
   - Check which commit `@` is.
2. **Merge forward.** Run `jj new <published-head> <base>`.
   - Use `jj new @ <base>` only when `@` is exactly that head.
3. **Resolve conflicts.** Resolve each conflict by what both sides meant to change, not by picking one side's text.
   - Recount any count the text states ("12 checks") in the merged tree.
   - For a positional list, check its consumer (length, maximum index, unread slots): two clean appends can break it with no conflict marker.
4. **Find the merge base.** Find the intended merge base from the graph, not by a blind pick in a criss-cross.
   - Read `jj diff --git --from <merge-base> --to <head>` with concrete revisions, merge-resolution content included; a patch-id list misses that content.
5. **Record and rerun.** Record what each side gave, what you kept, and the delta.
   - Run the affected behavior at the new head, including earlier fixes a diff selector no longer picks.
6. **Check before the push.** Check that the old head is still an ancestor: `jj log -r '<published-head> & ::<candidate-head>'`; empty means lost.
   - Read `jj git push --dry-run --bookmark <name>` in its own call.
     - Stop on sideways, backwards or deletion.
7. **Push.** Push only that bookmark.
   - Run `jj new` in the same `&&` chain.
   - Check that `jj log -r '<name>@origin'` names the intended commit.
8. **Request a verdict.** Request a verdict at the new sha over the real delta and all open findings.
   - Ancestry permits a delta review; it does not certify behavior.

Chain dependent mutations with `&&`. Run the check that decides whether you may push in its own tool call. Read that check's output before the push. Do not gate a push on a paged pipeline's status.

## Verdicts and comparisons

- A verdict names a commit, not a bookmark.
  - Check ancestry with `jj log -r '<verdict-sha> & ::<new-head>'`.
  - Carry the verdict only as "Review gates tied to a head" in `review-and-readiness.md` says.
- Name the base and tip at both ends of a comparison.
  - Make both fetchable by other readers.
  - List the commits, not only a count.
- Mark as unverified any old-to-new commit mapping that rests on local state other readers cannot fetch.
- A two-commit diff and a merge-base diff answer different questions.
  - To see what changed between two commits, read the files at both.
  - For the PR's contribution, diff from the intended merge base, merge-resolution content included.
- Where a new base can change behavior, run one matrix on old base, old head, new base and new head, in one environment and window, and compare the patch effects.
  - State what the matrix cannot see.
  - A base change can explain an absolute difference, never a changed patch effect.

## One wave per round

- Before you request a head-bound review, read the state of the PR ahead of this one in the merge queue.
  - If that PR, once merged, will conflict with this one or carry a change this one needs to build or pass, include that base integration in the next push.
  - Do not wait on unrelated predecessors or a moving main.
- While a reviewer judges a held head, a plain jj read can snapshot the working copy and so rewrite a held commit you edited.
  - Stage on an unpushed child or a scratch tree.
  - Combine that work with a blocking fix only on purpose.
- A blocking defect after the readiness report: run `gh pr view <N> --json state` right before you post HOLD (your request to stop the merge).
  - If it merged, fix it as a new change.
- Disclose an accidental rewrite or unrequested base merge; do not hide it with a force push.
  - Recover forward with `using-jj`.
  - In a shared store do not run `jj undo`, `jj op restore`, or a rewrite over a revset that matches commits you did not make.
