---
name: using-jj
description: "Use when performing ANY version control operation, starting a work session, checking repo state, or orienting to a codebase. This user uses jj instead of git — NEVER use git commands. Triggers on: commit, push, pull, branch, checkout, rebase, merge, diff, log, status, stash, reset, cherry-pick, bookmark, workspace, conflict resolution, 'what's the repo state', 'are other agents working here', 'what branches exist', 'starting work', 'orient me'."
---

# Using jj (Jujutsu)

Use jj, not Git commands, unless the user explicitly says otherwise. There is no
staging step: ordinary jj commands snapshot the working copy. `@` is that commit;
change IDs survive rewrites, commit IDs do not.

## 1. Establish the workspace and revision

Before editing, and after an interruption, rebase or workspace update, inspect:

```bash
jj log -r '@ | main@origin' --no-graph
jj status
```

Use the repository's actual trunk name when it is not `main`. A clean working copy
is not proof it is on current main or that its contents were published. Do not
revert unrecognized work. Establish ownership from the change, operation history
and workspace before moving anything.

Work in your own workspace. Inspect someone else's store with
`--ignore-working-copy`; do not use that flag to measure your own on-disk edits,
because it reads the prior snapshot instead. A workspace directory or copied file
is not a saved change: snapshot and describe your own work before switching away.

When creating, recovering or cleaning up a workspace, read
[workspace mechanics](references/workspaces.md) and
[shared-store details](references/shared-store-details.md). In particular:

- A normal workspace add must actually populate the files. Prefer colocated
  workspaces; disabling a required LFS filter can leave pointer text instead of data.
- If only a shared checkout is available, the register-then-populate procedure in
  [working-copy details](references/working-copy-details.md) avoids snapshotting another
  session. Its first command can fail after registration; populate that named new
  workspace, do not blindly add it again.
- `jj workspace list` identifies workspaces by name. Optional relative paths are
  not a reliable existence check. Never infer an orphan from an absent absolute path.
- Remove only your own superseded workspace by name, with the repository's supported
  forget path. Never run a repository-wide worktree prune or delete your current directory.

## 2. Edit the intended commit

**Create the child before editing a published or reviewed head.** Editing while
`@` is that head amends it at the next snapshot. After a rebase, check which commit
`@` names; rebasing another branch does not move your working copy onto it.

For your own unpublished, unshared change, edit it in place rather than making
throwaway children merely to squash them. `jj describe` changes the description of
whatever `@` is now; read the existing description before replacing it.

Never extract paths from a merge commit with `split` or `squash --from`: those paths
are merge resolutions. Keep the work there or recreate the intended delta on a
fresh child. See [working-copy and identity details](references/working-copy-details.md)
for the after-push, after-rebase and colocated-HEAD traps and their recovery paths.

## 3. Protect the whole shared store

The operation log belongs to the repository, not one workspace. **Never use
`jj undo`, `jj op restore` or `jj op revert` in a shared repository, even with your
own operation ID.** Recover with an inspected, forward, narrowly scoped edit.

Every rewrite can rebase descendants, including `describe`, amend, squash, abandon
and either form of rebase. Before one, inspect descendants and bookmarks, and
coordinate any other owner's work. Being your own change is necessary, not sufficient.
Never select mutations with broad sets such as `mine()`, `empty()`, `divergent()`,
`all:` or an unbounded trunk range in a shared store.

A path-scoped `jj restore --from <known-revision> <explicit-paths>` in your own
workspace can recover your own changes or exercise a regression. A bare restore
in a shared checkout cannot. After a failed operation, read the complete error and
current state before making one deliberate correction; do not run undo loops.

Before touching descendants, immutable revisions, worktree registrations or
someone else's conflicted bookmark, read
[shared-store details](references/shared-store-details.md). An immutable error is
not permission to bypass the guard. Fork pins have different identity requirements;
use `fork-work` and `using-knives` for a managed fork, not a raw jj workaround.

## 4. Publish only the named change

Fetch the intended remote before deciding what to publish. Confirm the current
remote head and the actual base rather than inferring them from a title or timestamp.
Use a single named bookmark, never `--all` or `--deleted`.

Two alternatives for a new bookmark:

```bash
jj git push --named <name>=@
```

Or create/set the bookmark separately, then push with `--bookmark <name>`.
Do not combine those alternatives. `--allow-new` is not a flag in this installation;
a suggestion to use `--all` is not a safe substitute. Bare push can return success
while refusing to publish an untracked bookmark.

Before a real push, dry-run the **same selection** you intend to publish:
`jj git push --named <name>=<revision> --dry-run` for a new name, or
`jj git push --bookmark <name> --dry-run` for an existing local bookmark.
The named dry-run does not create that bookmark; its real push still needs
`--named`, not `--bookmark`. Inspect the proposed change before removing `--dry-run`:

- A new bookmark is an add; an appended review fix must **move forward**.
- A deletion, backward move or unexpected sideways move stops the push.
- An intentional rebase of published work needs the repository's authorization and
  payload proof, with both old and new endpoints, reachable commits, and explicit
  conflict resolutions. A direction word alone cannot establish equivalence.

Capture your intended commit ID before pushing. Verify the named remote bookmark
now equals it; `Nothing changed` is not proof of a successful publication. Run
`jj new` after a successful push in the same dependent command chain so later edits
cannot amend the published head. Gate dependent commands with `&&`, not `;`.

For publication failures, new-bookmark behavior, explicit single-bookmark deletion,
non-TTY quoting or divergent references, read
[publishing details](references/publishing-details.md) and the
[command reference](references/commands.md). Pass `-m` to split/squash or `-u` to
squash when an editor is unavailable; protect backticks from shell substitution.

## 5. Rebase, stacks and conflicts

Do not rebase merely because main advanced when no conflict requires it. For a
private unpublished chain that repeatedly conflicts at each commit, consolidate it
into reviewable logical commits first, then resolve once. Do not rewrite another
owner's or a published chain as incidental cleanup.

Read [stack and conflict details](references/stack-and-conflict-details.md) and
[rebase mechanics](references/rebase.md) before restructuring a stack:

- In an unowned shared stack, duplicating only your line can isolate publication
  without moving siblings. **Do not duplicate fork-release members** whose pins rely
  on stable change IDs; the fork workflow owns their movement.
- Squash merging creates no ancestry to the original commits. Restack only your
  unmerged commits, based on the actual fork point, rather than replaying the merged
  parent's changes. A deleted remote branch is not a usable base ref.
- Compute the child boundary against the old parent when that parent was rewritten
  elsewhere; inspect the resulting files and conflicts. Do not choose a boundary
  by eye from abbreviated change IDs.
- After any rebase, verify the new parents. A divergent change ID may be ambiguous;
  use the known commit ID for a one-revision operation, then find the survivor.
  Clean up only your own stale copy with no descendants or bookmarks.

Use [revsets](references/revsets.md) for selection syntax and
[divergence](references/divergence.md) for the matching recovery procedure.

## 6. Make evidence answer the actual question

Read [measurement details](references/measurement-details.md) when comparing a
change, proving ancestry, diagnosing stale refs or claiming content is absent.

- Query ancestry explicitly, for example `jj log -r '<fix> & ::<pin>'`; neither a
  PR title, issue status, commit search nor a short log window answers containment.
- Inspect your own changes against their own parent. A moved trunk or wrong merge
  base counts other work as yours. `--stat` counts added plus removed lines, not a
  per-file split. For figures compared with GitHub, use GitHub's own file statistics.
- A successful fetch can still leave the ref you meant unchanged. Check the exact
  live remote when that happens rather than treating a cached ref as current.
- A claim about main's behavior uses a pristine named revision in a dedicated
  directory, never the shared working copy or `/tmp` as a checkout root.
- Show that a content read succeeds and returns bytes before using it as evidence
  of absence. A wrong literal path, unmatched glob and empty file are different
  states. Use a positive control independent of the suspected failure.

Git commands in the references are read-only diagnostics or named exceptions; they
do not change the jj-only rule above.
