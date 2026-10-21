---
name: using-jj
description: "Use for version control in a checkout that has a `.jj` directory, such as a Legion runtime workspace or the shared `~/.dotfiles` checkout. Covers jj commands, bookmarks, jj workspaces, jj errors and conflict resolution there. Skip it in a plain git clone, and never run `jj git init` in one."
---

# Using jj (Jujutsu)

- Never run `jj git init` in a git clone. A box's clone fetches file contents on demand, and jj fails partway on the ones it lacks.
- In a jj repository, use jj, not git, unless the user says otherwise. Git commands in the references are read-only diagnostics or named exceptions.
- No staging: jj commands snapshot the working copy into `@`. Automatic snapshots are not a hazard or a race condition.
- Change IDs survive rewrites. Commit IDs do not.
- Command lookup: [commands](references/commands.md).

## 1. Establish the workspace and revision

1. Before you edit, and after an interruption, rebase or workspace update, run `jj log -r '@ | main@origin' --no-graph` and `jj status`. Use the real trunk name. A clean working copy does not prove that `@` is on current main or that its content is published.
2. Do not revert unrecognized work. Find its owner from the change, the operation log and the workspace before you move it.
3. Work in your own workspace. Subagents on disjoint files of the same feature share it. Do not make separate workspaces for them.
4. Use `--ignore-working-copy` to read another session's store. Never use it to check your own edits, because it reads the old snapshot.
5. A workspace directory or a copied file is not a saved change. Snapshot and describe your work before you switch away.
6. Before you create, recover or delete a workspace, read [workspaces](references/workspaces.md). It must populate the files (prefer a colocated add). Find workspaces by name, never by an absent path. Delete only your own superseded workspace, by name, with the repository's supported forget path. Never prune worktrees across the repository or delete your current directory.

## 2. Edit the intended commit

- Run `jj new <head>` before you edit a published or reviewed head. Otherwise the next snapshot amends it. Do not squash into a published head as a workaround for the append-only rule.
- After a rebase, check which commit `@` is. A rebase of another branch does not move `@`.
- Edit your own unpublished, unshared change in place with `jj edit`. Do not make throwaway children to squash back.
- `jj describe` replaces the message of the current `@`. Read that message first.
- Never extract paths from a merge commit with `split` or `squash --from`. Those paths are its merge resolution. Keep the work there or rebuild the delta on a fresh child.
- For the traps after a push, after a rebase and with colocated `HEAD`, read [working copy](references/working-copy-details.md).

## 3. Protect the whole shared store

- Never run `jj undo`, `jj op restore` or `jj op revert` in a shared repository, even with your own operation ID. The operation log belongs to every workspace. Recover with one inspected, narrow, forward edit.
- Every rewrite can rebase descendants: `describe`, amend, `squash`, `abandon`, `absorb` and each `rebase` form. Before one, inspect the descendants and bookmarks of the exact revisions, and coordinate with other owners. Your own change is necessary, not sufficient.
- Never mutate a broad set: `mine()`, `empty()`, `divergent()`, `all:` or an open trunk range. Name your own IDs.
- Never run a blanket `jj restore`. Name the source commit and the paths: `jj restore --from <rev> <paths>`, in your own workspace.
- After a failed command, read the full error and the state, then make one correction. No undo loops.
- An immutable error is not permission to bypass the guard. Find the pin. For a managed fork, use `fork-work` and `using-knives`.
- Before you touch descendants, immutable revisions, worktree registrations or another owner's conflicted bookmark, read [shared store](references/shared-store-details.md).

## 4. Publish only the named change

1. Fetch the intended remote. Check the remote head and the real base from the graph, not from a title or a timestamp.
2. Push one named bookmark. Never use `--all` or `--deleted`. For a new bookmark, use one of these, never both:
   - `jj git push --named <name>=<rev>`
   - `jj bookmark set <name>`, then `jj git push --bookmark <name>`
3. `--allow-new` does not exist, and `--all` is not a substitute. A bare push can exit 0 and publish nothing.
4. Dry-run the same selection with `--dry-run` and read the direction. A new bookmark is an add, and a review fix must move forward. A delete, a backward move or an unexpected sideways move stops the push. A rebase of published work needs the repository's authorization and payload proof, not a direction word.
5. Record the intended commit ID. After the push, check that `<name>@<remote>` equals it. `Nothing changed` is not proof.
6. Run `jj new` after a successful push in the same dependent command chain, so later edits cannot amend the published head. Gate dependent commands with `&&`, never `;`.
7. Give `split` and `squash` `-m`, or `squash -u`. Protect backticks in messages from the shell.
8. For push failures, bookmark deletion, rebase proof and divergence, read [publishing](references/publishing-details.md).

## 5. Rebase, stacks and conflicts

- Do not rebase only because main moved.
- If your private, unpublished chain conflicts at each commit, squash it into reviewable logical commits, then resolve once. Never rewrite another owner's chain or a published chain as cleanup.
- Before you restructure a stack, read [stacks and conflicts](references/stack-and-conflict-details.md) and [rebase](references/rebase.md):
  - In a shared stack you do not own, duplicate only your line to publish it. Never duplicate fork-release members, because their pins need stable change IDs.
  - After a squash merge, restack only your unmerged commits from the real fork point. A deleted remote branch is not a base.
  - Compute the child boundary against the old parent. Never choose it by eye from short change IDs.
  - After a rebase, check the new parents. Address a divergent change by commit ID, then find the survivor. Abandon only your own stale copy, with no descendants and no bookmarks.

## 6. Make evidence answer the question

Read [measurement](references/measurement-details.md) before you compare a change, prove ancestry, diagnose a stale ref or claim that content is absent.

- Query ancestry: `jj log -r '<fix> & ::<pin>'`. A PR title, an issue status, a commit search or a short log window does not answer it.
- Diff your change against its own parent. For figures compared with GitHub, use GitHub's file statistics.
- If a fetch succeeds but the ref did not move, check the live remote.
- Test claims about main on a pristine revision in its own directory. Never use the shared working copy or `/tmp` as the root.
- Before a read proves absence, show that it returns bytes. Use a positive control that does not share the suspected failure.
