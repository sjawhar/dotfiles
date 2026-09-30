# Publishing from a shared stack

When several sessions' commits sit in one local stack (a shared checkout, or an unpushed
chain nobody owns whole), publish each line of work with `jj duplicate <commit> -d main@origin`
and open the PR from the duplicate. The duplicate has its own commit ID and no descendants
in the stack, so a later `jj split` / `jj describe` / `jj rebase` on the original stack rewrites
the originals and never moves a published PR head. Duplicating is therefore the right move for
publication from a stack you cannot restructure yet; it is the wrong move where a release pin
must follow the change ID (the using-jj fork-release rule).

## Conflict chains: squash first, resolve once

When a rebase drags a many-commit branch across a moved trunk, each commit re-conflicts on the
same hunks. Do not grind through the chain: squash the branch to one commit, or the few the
reviewer genuinely needs under the user's commit-structure rule, then rebase and resolve one
commit's worth of conflicts. Do not rebase at all when there is no conflict. The same shape holds
in an octopus merge: its members share one fork point, never several. The exceptions are a shared
stack you cannot restructure (duplicate instead) and a fork branch whose release pin must follow
the change ID.

**After a squash merge, the merged commits have no ancestry a rebase can recognise.** GitHub's
squash lands the PR as one new commit whose parents do not include any of the branch's commits,
so a branch stacked on that PR still carries them and `jj rebase -b`/`-s` onto `main@origin`
re-applies them: as **empty** commits when main has not touched those files since (clutter;
`--skip-emptied` drops them), and as **conflicts** when main has changed one of them since (a
2-sided conflict on the file, propagated into your own commits; `--skip-emptied` does nothing for
those, because a conflicted commit is not empty). The outcome is decided per **file**, so a real
case is a mix of empty commits and a conflicted one. That reads as a broken rebase rather than a
duplicate replay: `--skip-emptied` drops the empties and leaves the conflict, and the tool looks
confused. jj does no patch-ID matching. Before any rebase, `git merge-tree` of the un-rebased head
against main already names the conflicting file.

The probe is positive, not a reading of commit subjects: `jj log -r '<their-commit> & ::main@origin'`
**empty** proves the squash left no ancestry. The formulation that drops the duplicates is
`jj rebase -r <your own commit ids> -d main@origin`; the merged commits stay on the old base and
are nothing to keep. Two more things break in the same event (jj 0.45.1-sami):

- The merged PR's head branch is auto-deleted, so `<base-branch>@origin` stops resolving
  (`Error: Revision \`A@origin\` doesn't exist`, after a fetch that printed `A@origin [deleted]`).
  That is the loud failure, not a wrong answer.
- A stacked branch's base is the commit it actually forked from, not the base branch's final
  head, so "rebase the stack" is a per-branch list derived from the remote, never one instruction.

**A read used as evidence of absence must first be shown to return something.** `jj file show -r
<rev> <path>` has three ways to hand you zero bytes, and a `grep` over the result reads all three
as "the clause is not there" (jj 0.45.1-sami):

- A **literal path that matches nothing**: jj paths are cwd-relative, so `docs/x.md` from a
  subdirectory looks for `<subdir>/docs/x.md`. It fails loud (`Error: No such path`, exit 1), but a
  stdout-only capture (`$(...)`, `| grep`, `2>/dev/null`) throws the error and the status away.
- A **glob that matches nothing** (`glob:docs/nothing*.md`) is genuinely silent: exit 0, no
  output, no warning.
- A genuinely **empty file** is exit 0 and empty too.

Only the byte count separates them: `wc -c` the output before asking it anything, and anchor paths
at the root (`root:docs/x.md`) when the cwd is not the repository root. A control has to be
non-empty for a reason **independent** of what you are testing. A "known-present" item that shares
the query's blind spot returns the same empty and proves nothing; for example, a control for
`gh search issues` must not be a pull request, which that command excludes. Pick something the
tool must return by a different mechanism, and confirm with an independent read such as
`git show origin/main:<path>`.
