---
name: using-jj
description: "Use for version control in a checkout that has a `.jj` directory, such as a Legion runtime workspace or the shared `~/.dotfiles` checkout. Covers jj commands, bookmarks, jj workspaces, jj errors and conflict resolution there. Skip it in a plain git clone."
---

# Using jj (Jujutsu)

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

In a jj repository use jj, not git, unless the user says otherwise: git writes can desync jj. The git commands in the references are read-only diagnostics or named exceptions. There is no staging: every jj command first snapshots the working copy into `@`, so your saved files are already in it. Change IDs survive rewrites; commit IDs do not. Commands: [commands](references/commands.md).

All workspaces share one commit store and one operation log, so an undo or a rewrite you run changes what the others see.

## 1. Know where `@` is

1. Before you edit, and after an interruption, rebase or workspace update, run `jj log -r '@ | main@origin' --no-graph` and `jj status` (use the real trunk name). A clean `jj status` does not show that `@` is on current main, or that its content is published.
2. Work you do not recognize is usually another session's. `jj log -r <change>`, `jj op log` and `jj workspace list` name its owner; ask them before you move or revert it.
3. Work in your own workspace. Subagents on disjoint files of one feature share it, so their edits land in one change.
4. Read another session's store with `--ignore-working-copy`, which leaves its working copy alone. Never check your own edits with it: it reads the old snapshot.
5. A workspace directory or a copied file is not a saved change. Run `jj describe -m '<what you did>'` (which snapshots `@`) before you switch away, or the work goes with the directory.
6. Before you create, recover or delete a workspace, read [workspaces](references/workspaces.md). Prefer a colocated add (the default) and list the new directory afterwards: a non-colocated add can abort halfway and leave it empty. Find a workspace by name, never by path: most `jj workspace list` rows show none. Delete only your own superseded workspace, by name (`jj workspace forget`, or `knives finish` in a fork). Never run `git worktree prune`, which deletes other workspaces' registrations, and never delete your current directory.

## 2. Edit the intended commit

- Run `jj new <head>` before you edit a published or reviewed head; otherwise the next snapshot amends it. Squashing into it does too.
- After a rebase, check which commit `@` is: rebasing another branch does not move it, so your next edit lands in the old commit.
- Edit your own unpublished, unshared change in place with `jj edit`: a throwaway child squashed back is one more rewrite.
- `jj describe` replaces `@`'s message; it does not commit. Read that message first.
- Never take paths out of a merge commit with `split` or `squash --from`: its paths are the merge resolution, so they vanish from the merged tree. Keep the work there or rebuild the delta on a fresh child.
- For traps after a push, a rebase or with colocated `HEAD`, read [working copy](references/working-copy-details.md).

## 3. Protect the shared store

- Never run `jj undo`, `jj op restore` or `jj op revert` in a shared repository, even on your own operation: they rewind every workspace. Repair forward: read `jj log` and `jj diff` for that revision, then make one new commit or one `jj restore --from <rev> <your paths>`.
- Every rewrite moves the commits stacked on it: `describe`, amend, `squash`, `abandon`, `absorb`, every `rebase` form. Before one, run `jj log -r '<rev>::'` and message the owner of every commit and bookmark it lists. Being the author does not make the rewrite safe.
- Never mutate a broad set (`mine()`, `empty()`, `divergent()`, `all:`, a range with no upper end such as `main@origin..`): each matches other sessions' commits. Name your own IDs.
- Never run a bare `jj restore`, which takes other sessions' uncommitted work. Name the source and the paths: `jj restore --from <rev> <paths>`, in your own workspace.
- After a failure, read the whole error, then `jj status` and `jj log -r '@ | @-'`, then make one correction. A second correction without a fresh read compounds the damage.
- An immutable error is not permission to pass `--ignore-immutable`. Find what pins the commit: `jj log -r 'immutable_heads() & descendants(<rev>)'`. In a knives-managed fork, read `fork-work` and `using-knives` first.
- Before you touch descendants, immutable revisions, worktree registrations or another owner's conflicted bookmark, read [shared store](references/shared-store-details.md).

## 4. Publish only the named change

1. Fetch the intended remote. Read the remote head and the real base from `jj log`, not from a PR title or a commit date.
2. Push one named bookmark: either `jj git push --named <name>=<rev>`, or `jj bookmark set <name>` then `jj git push --bookmark <name>`. `--allow-new` does not exist; `--all` pushes other sessions' bookmarks and `--deleted` every pending deletion, closing their PRs. A bare push can exit 0 and publish nothing.
3. Dry-run the same selection with `--dry-run`. Stop unless it prints an add for a new bookmark, or a forward move from the commit ID you recorded. Rebase published work only if the repository's docs or owner allow it, and show with `git range-diff` (old vs new commit range) that the payload is unchanged; a forward move is not that proof.
4. Record the intended commit ID. After the push, check `<name>@<remote>` equals it: `Nothing changed` is not proof.
5. Chain `jj new` after a successful push, so later edits cannot amend the published head. Gate dependent commands with `&&`, never `;`, which runs on after a failure.
6. Give `split` and `squash` `-m` (or `squash -u`): without it they try to open an editor, which fails with no terminal. Quote backticks in messages, or the shell runs them.
7. For push failures, bookmark deletion, rebase proof and divergence, read [publishing](references/publishing-details.md).

## 5. Rebase, stacks, conflicts

- Do not rebase only because main moved: the rewrite moves descendants and voids the review.
- If your private, unpublished chain conflicts at every commit, squash it into reviewable commits and resolve once. Never rewrite another owner's chain or a published chain as cleanup.
- Before you restructure a stack, read [stacks and conflicts](references/stack-and-conflict-details.md) and [rebase](references/rebase.md). They cover: copy only your own commits out of a shared stack (never one a fork release pins: it tracks change IDs); restack after a squash merge from the real fork point; pick the child range by revset, not by eye; divergence.

## 6. Check ancestry, diffs and absence

Before you compare a change, prove ancestry (`jj log -r '<fix> & ::<pin>'`, not a PR title or a log window), diagnose a ref that did not move, test a claim about main, or call content absent, read [measurement](references/measurement-details.md).
