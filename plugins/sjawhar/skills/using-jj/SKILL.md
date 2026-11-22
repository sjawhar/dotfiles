---
name: using-jj
description: "Use when doing version control in a checkout that has a `.jj` directory, such as a Legion workspace or `~/.dotfiles`: jj commands, bookmarks, workspaces, pushes, rebases, conflicts or jj errors. Skip it in a plain git clone."
---

# Using jj

In a jj repository use jj, not git, unless the user says otherwise: git writes can desync jj.

The git commands here are read-only checks or named exceptions. Every jj command first snapshots the working copy into `@`, so there is no staging. Change IDs survive rewrites; commit IDs do not.

- Use the identity already configured.
- If the identity is missing, do not invent one or change shared user config without the user's authorization.
  - Read `references/commands.md` for identity setup.

All workspaces of a repository share one commit store and one operation log, so an undo or a rewrite you run changes what every other session sees. A read-like command's snapshot changes that history too. A repository is shared when `jj workspace list` prints more than one workspace, or when another session works in your checkout (`jj-agent-status` lists them).

Read `references/commands.md` for a command or revset you do not know, bookmark commands, identity setup and the local helpers (`jj-agent-status`, `jj agent-log`, `jj tug`) with their fallbacks.

## 1. Know where `@` is

1. **Run `jj-agent-status`** at session start and for triage. Without it, run `jj status`, `jj log` and `jj workspace list`.
   - Read `references/commands.md` for the local helpers.
2. **Check `@`.** Before you edit, and after an interruption, a rebase or a workspace update, run `jj log -r '@ | @- | main@origin' --no-graph` and `jj status` (use the repository's trunk name). A clean status does not show that `@` is on current main or that it is published.
3. **Ask its owner before you move or revert work you do not recognize:** it is usually another session's. `jj log -r <change>`, `jj op log`, `jj workspace list` and the `session-attribution` skill name its owner.
4. **Work in your own workspace.** Subagents on disjoint files of one feature can share it; their edits then land in one change.
5. **Do not check your own edits with `--ignore-working-copy`:** it reads the last snapshot. The flag reads a store without snapshotting another session's working copy.
6. **Run `jj describe -m '<what you did>'` before you leave the workspace:** a file in a workspace directory is not saved until a jj command there snapshots it.
7. **Read `references/workspaces.md`** before you create, recover, forget or advance a workspace, or touch a worktree registration.
8. **Never run `git worktree prune`:** it deletes other workspaces' registrations.

## 2. Edit the intended commit

- Run `jj new <head>` before you edit a published or reviewed head; otherwise the next snapshot, or a squash into it, amends it.
- After a `jj rebase`, check which commit `@` is before you edit: it does not move `@`.
- Edit your own unpublished, unshared change in place with `jj edit`.
- Read `@`'s message before you run `jj describe`: it replaces `@`'s message; it does not commit.
- Read `references/split-and-squash-details.md` before you run `jj split` or `jj squash`: their `-m` or `-u`, and a merge commit's paths.
- Read `references/working-copy-details.md` when an edit landed in the wrong commit: after `jj new`, a push, a rebase or an interruption, or through git's `HEAD` in a colocated repository.

## 3. Protect the shared store

- Do not run `jj undo`, `jj op restore` or `jj op revert` in a shared repository: not on your own operation, not to repair another session's mistake, not because a hint names one. They rewind state every workspace sees. A workspace whose session ended is not idle.
- Repair forward in a shared repository, for the same reason: read `jj log` and `jj diff` for the revision, then make one new commit or one `jj restore --from <rev> <your paths>`.
- Before a rewrite, run `jj log -r '<rev>::'` and get the agreement of the owner of every commit and bookmark it lists. Every rewrite moves the commits stacked on it: `describe`, amend, `squash`, `abandon`, `absorb`, every `rebase`. Being the author does not make it safe.
- Do not pipe a rewrite, which hides its exit code and first error.
- Check a rewrite with `jj log` afterward, for the same reason.
- Read `references/commands.md` before you run `jj absorb`.
- Mutate only change IDs you name. A broad set (`mine()`, `empty()`, `divergent()`, `visible_heads() & ~immutable()`, `all:`, an open range such as `main@origin..`) matches other sessions' commits.
- Never run a bare `jj restore` or `jj abandon` in any checkout: each takes other sessions' uncommitted work.
  - Name the source and the paths, `jj restore --from <rev> <paths>`, in your own workspace.
- Never run a bare `jj new` in a checkout other sessions use, for the same reason.
- After a failure, read the whole error, `jj status` and `jj log -r '@ | @-'`, then make one correction.
- Do not take an immutable refusal as a reason for `--ignore-immutable`.
- On an immutable refusal, find the pin: `jj log -r 'immutable_heads() & descendants(<rev>)'`.
- On an immutable refusal in a knives-managed fork (one that `knives repos` lists), read the `fork-work` and `using-knives` skills first.
- Read `references/shared-store-details.md` before you rewrite a commit that has descendants, override an immutable refusal, or clear another owner's conflicted bookmark.

## 4. Publish only the named change

1. **Run `jj git fetch`**, then read the remote head and the real base from `jj log`, not from a PR title or a date.
2. **Record the commit ID you mean to publish**, and the remote bookmark's current commit from `jj log -r '<name>@origin'` (none for a new bookmark).
3. **Run the push with `--dry-run` first.**
   - Push one bookmark: `jj git push --named <name>=<rev>` for a new one, `jj git push --bookmark <name>` for an existing one. `--all` and `--tracked` push other sessions' bookmarks.
   - Go on only if the dry-run shows `add to <your commit>`, or `move forward from <the remote's commit> to <your commit>`.
   - Read `references/commands.md` for bookmark commands.
4. **Check that `jj log -r '<name>@origin' --no-graph -T commit_id` is the recorded ID:** a push that published nothing can print `Nothing changed.` and exit 0.
5. **Chain `jj new` after the push with `&&`**, so later edits cannot amend the published head.
   - Gate every command that depends on the one before it with `&&`, never `;`, which runs on after a failure.
6. **Read `references/publishing-details.md`** before you rebase published work, and when a push fails or publishes nothing, when you delete a remote bookmark, or when a push chain meets a divergent change.

## 5. Rebase, stacks, conflicts

- Read `references/rebase-details.md` before you rebase: the rebase flags and the `jj log` read after every rebase.
- If your private, unpublished chain conflicts at every commit, squash it to one commit, or to the layers that help review. Conflict chains in `references/stack-and-conflict-details.md` says when. Then rebase and resolve once.
- Never rewrite another owner's chain as cleanup.
- Never rewrite a published chain as cleanup, other than by a rebase.
- Before you resolve a conflict, read jj's conflict markers in `references/commands.md`, then follow the `resolve-conflicts` skill for what to keep.
- Before you restructure a stack, read `references/stack-and-conflict-details.md` and `jj rebase --help`: to restack after a squash merge, move a child stack, publish from a stack other sessions share, or fix a divergent change.

## 6. Ancestry, diffs and absence

Prove ancestry with a graph query such as `jj log -r '<fix> & ::<pin>'`, never a PR title or a log window. Read `references/measurement-details.md` before you prove ancestry, count what a change changed, test a claim about main, call content absent, or diagnose a ref that a fetch did not move.
