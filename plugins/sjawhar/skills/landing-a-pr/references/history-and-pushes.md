# History, conflicts and push rounds

Read before conflict repair, required base integration, stack changes or a push
that follows a review. Use `using-jj` for commands and shared-store safety.

## Decide whether the base needs integrating

`BEHIND` alone does not justify touching the head. Integrate for a real conflict,
a required dependency or an explicit coordination decision, not currency.
GitHub `update-branch` is not the babysitter's repair path. The owner controls the
integration, its proof and its publication.

Keep published/reviewed commits as ancestors. The boundary is **unrequested
currency work versus required owner-controlled integration**. A required
integration of a reviewed head is a forward merge, because a rewrite would remove
the commit its verdict names.

- Published/reviewed head: make a forward merge with jj. Do not rebase, amend,
  describe over, or squash into a commit the remote or reviewer already holds.
- Unpublished work: follow `using-jj`, including ownership/descendant checks
  before a rewrite. Do not interpret this as permission to rewrite another
  lane's stack or bypass immutability.
- Stacked PR: work the lowest unmerged member. Ask the stack owner to perform
  topology work; the babysitter does not restack the series.
- A parent being rewritten elsewhere needs one agreed freeze SHA for the next
  integration round. Do not chase every intermediate push.

After a parent squash-merges, its old commits are not ancestors of the new
squash commit merely because their content is present. Diagnose duplicate
content before selecting a repair. Do not replay or discard another lane's
commits by assuming that ancestry proves content equivalence.

## Forward repair of a published head

1. Read the current remote head, base and ownership. Fetch through jj. Confirm
   which commit `@` names and that the workspace is yours.
2. Create a merge child of the actual published head and the required base.
   The shape is `jj new <published-head> <base>`; when `@` is exactly that head,
   `jj new @ <base>` is equivalent. Do not infer `@` from your last command.
3. Resolve semantically. A conflict in a counted sentence needs a new
   enumeration of the merged tree, not either parent's number. A positional
   list needs its consumer checked too: compare length, maximum index and
   intentionally unread slots. Two independent appends can break that contract
   even when no conflict markers appear.
4. Compare the PR's own contribution against its new base, including all
   merge-resolution content. A per-commit patch-id list misses content created
   by the merge itself. Establish the intended merge base from the graph, then
   inspect `jj diff --git --from <verified-merge-base> --to <head>` with concrete
   revisions. Do not choose one common ancestor blindly in a criss-cross graph.
5. Record what each conflict side contributed, what was kept and the actual
   delta. Run the affected behavior at the new head, including earlier fixes
   whose tests a current-diff selector no longer chooses.
6. Before pushing, verify the old published head is still an ancestor:
   `jj log -r '<published-head> & ::<candidate-head>'`. Empty is not preserved.
   Read a named `jj git push --dry-run --bookmark <name>` separately. A published
   branch must move forward; stop on sideways, backwards or deletion.
7. Publish only the named bookmark after the checks. Confirm
   `jj log -r '<name>@origin'` names the intended commit, and leave that commit
   with `jj new` before any edit. Keep the successful push and that `jj new`
   in one dependent command sequence so a later tool cannot amend the head.
8. Request a verdict at the new SHA over the real delta and all open findings.
   Forward ancestry permits a delta review; it does not certify the new
   behavior or silently carry a verdict across a changed contribution.

Use `&&` between dependent mutation commands. A failed first command must stop
its push. Read the licensing measurement in its own call before constructing
the mutation. Never gate a push on a paged pipeline's status.

## Review ancestry and comparison endpoints

A verdict names a commit, not a bookmark. Before re-citing it, query
`jj log -r '<verdict-sha> & ::<new-head>'`. No result means a full review is
needed. “I only appended” is intent, not graph evidence.

Name the base and tip at both ends of any before/after comparison, and make the
objects reachable to independent readers. List the commits or their meaningful
classification, not just a count. A locally recoverable old tip that others
cannot fetch is not independently verified; name which mappings are unverified.

A direct two-commit delta and a merge-base comparison answer different questions.
A merge-base view can show an unchanged line as re-added. Read the files at the
two exact commits when asking what changed between them. For PR contribution,
compare from the intended base/merge base and include merge-resolution content.

Patch equality does not prove identical behavior against a new base. Where the
interaction matters, run the same real matrix on old base, old head, new base
and new head. Compare patch effects, not just absolute outputs. Hold the
environment constant, rerun old captures in the same window, and state what the
matrix cannot observe. A base-only change can explain an absolute difference;
it does not excuse a changed patch effect.

## Keep one publication wave per round

Repair conflicts first, then findings, then CI. Run fast checks and the suites
for affected callers and inventory changes before pushing the combined wave.
Do not change the checkout while a long run reads it.

When a reviewer is judging a held head, stage on a new unpushed child or in a
scratch tree. An ordinary jj read can snapshot the working copy; editing the
held commit then reading it would rewrite the artifact under review. If a
blocking finding arrives, combine the staged work deliberately with its fix.
Runtime changes invalidate acceptance rows observed at the prior head.

Before dispatching a head-bound review, read the queue predecessor's current
state. If a required integration is imminent, combine it with the next round
rather than buying a verdict for a head already known to need replacement.
Do not impose a general wait on unrelated predecessors or moving main.

Useful additions that do not block a held head belong in the next coherent
change. If a real blocking defect is found after sending READY, read the PR's
state immediately before sending HOLD. If already merged, repair it as a new
change; a late stop message cannot undo a merge.

## Unrequested changes and recovery

An unexpected head move stops mutation until ownership is settled. An accidental
rewrite or unrequested base merge is disclosed, not hidden with a force push.
Use `using-jj`'s forward recovery procedures; do not perform repository-global
undo or broad revision-set surgery in a shared store.

Pass `-R <owner>/<repo>` to `gh` where supported. Workspace colocation varies;
explicit repository selection avoids relying on discovery. Never export
`GIT_DIR` or `GIT_WORK_TREE`: they redirect unrelated subprocesses into the
shared repository.
