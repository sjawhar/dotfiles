---
name: using-jj
description: "Use when doing version control in a checkout that has a `.jj` directory, such as a Legion workspace or `~/.dotfiles`: jj commands, bookmarks, workspaces, pushes, rebases, conflicts or jj errors. Skip it in a plain git clone."
---

# Using jj

In a jj repository use jj, not git: git writes can desync jj. The git commands here are read-only checks or named exceptions. Every jj command first snapshots the working copy into `@`, so there is no staging. Change IDs survive rewrites; commit IDs do not. Use the identity already configured; a missing one is not permission to invent one or change shared user config without the user's authorization.

All workspaces of a repository share one commit store and one operation log, so an undo or a rewrite you run changes what every other session sees.

Local helpers:
- `jj-agent-status` (`--deep`, `--json`): where you are, other agents' work and what needs attention. Run it at session start.
- `jj agent-log [-r <revset>]`: one JSON object per commit (an alias in `.jjconfig.toml`). `jj tug` moves the closest bookmark below `@` up to `@`.
- `JJ_CONFIG= jj ...` ignores this machine's config, for a test repository. `XDG_CONFIG_HOME` does not hide `~/.jjconfig.toml`.

## 1. Know where `@` is

1. Before you edit, and after an interruption, a rebase or a workspace update, run `jj log -r '@ | @- | main@origin' --no-graph` and `jj status` (use the repository's trunk name). A clean status does not show that `@` is on current main or that it is published.
2. Work you do not recognize is usually another session's. `jj log -r <change>`, `jj op log`, `jj workspace list` and the `session-attribution` skill name its owner. Ask them before you move or revert it.
3. Work in your own workspace. Subagents on disjoint files of one feature can share it; their edits then land in one change.
4. `--ignore-working-copy` reads a store without snapshotting another session's working copy. Do not check your own edits with it: it reads the last snapshot.
5. A file in a workspace directory is not saved until a jj command there snapshots it. Run `jj describe -m '<what you did>'` before you leave the workspace.
6. Read `references/workspaces.md` before you create, recover, forget or advance a workspace.

## 2. Edit the intended commit

- Run `jj new <head>` before you edit a published or reviewed head; otherwise the next snapshot, or a squash into it, amends it.
- `jj rebase` does not move `@`. After one, check which commit `@` is before you edit.
- Edit your own unpublished change in place with `jj edit`.
- `jj describe` replaces `@`'s message; it does not commit. Read the message first.
- Do not take paths out of a merge commit with `split` or `squash --from`: a merge's paths are its resolution, so they vanish from the merged tree. Rebuild the delta on a fresh child.
- Read `references/working-copy-details.md` when an edit landed in the wrong commit: after `jj new`, a push, a rebase or an interruption, or through git's `HEAD` in a colocated repository.

## 3. Protect the shared store

- Do not run `jj undo`, `jj op restore` or `jj op revert` in a shared repository, even on your own operation: they rewind state every workspace sees. Repair forward with one new commit, or `jj restore --from <rev> <your paths>`.
- Every rewrite moves the commits stacked on it: `describe`, amend, `squash`, `abandon`, `absorb`, every `rebase`. Before one, run `jj log -r '<rev>::'` and get the agreement of the owner of every commit and bookmark it lists. Do not pipe a rewrite, which hides its exit code and first error; check it with `jj log` afterward.
- Run `jj absorb` only on a merge of several branches at `@`; for one ancestor use `jj squash --into`, or `jj edit <change>` then `jj new`. It leaves new and conflicted files in `@` and records conflicts in destinations: read `jj diff` and `jj log -r ::@` after.
- Mutate only change IDs you name. A broad set (`mine()`, `empty()`, `divergent()`, `all:`, an open range such as `main@origin..`) matches other sessions' commits.
- Never run a bare `jj restore` or `jj abandon`: name the source and the paths, `jj restore --from <rev> <paths>`, in your own workspace.
- After a failure, read the whole error, `jj status` and `jj log -r '@ | @-'`, then make one correction.
- An immutable refusal is not a reason for `--ignore-immutable`. Find the pin: `jj log -r 'immutable_heads() & descendants(<rev>)'`. In a knives-managed fork, read `fork-work` first.
- Read `references/shared-store-details.md` before you rewrite a commit that has descendants, override an immutable refusal, or clear another owner's conflicted bookmark.

## 4. Publish only the named change

1. `jj git fetch`, then read the remote head and the real base from `jj log`, not from a PR title or a date.
2. Record the commit ID you mean to publish, and the remote bookmark's current commit from `jj log -r '<name>@origin'` (none for a new bookmark).
3. Run the push with `--dry-run` first. Push one bookmark: `jj git push --named <name>=<rev>` for a new one, `jj git push --bookmark <name>` for an existing one. Go on only if the dry-run shows `add to <your commit>`, or `move forward from <the remote's commit> to <your commit>`. `--all` and `--tracked` push other sessions' bookmarks.
4. Check that `jj log -r '<name>@origin' --no-graph -T commit_id` is the recorded ID: a push that published nothing can print `Nothing changed.` and exit 0.
5. Chain `jj new` after the push with `&&`, never `;`, so later edits cannot amend the published head.
6. Rebase published work only if the repository's docs or owner allow it. Read `references/publishing-details.md` for that proof, and when a push fails or publishes nothing, when you delete a remote bookmark, or when a push chain meets a divergent change.

## 5. Rebase, stacks, conflicts

- A `jj rebase` with no `-r`, `-s` or `-b` means `-b @`: the whole branch, with any commits other sessions stacked on it. `-r` re-parents descendants onto the old parents. `jj rebase --help` has the diagrams.
- Do not rebase only because main moved: the rewrite moves descendants and voids the review.
- If your private, unpublished chain conflicts at every commit, squash it into reviewable commits, then rebase and resolve once. Never rewrite another owner's chain or a published chain as cleanup.
- In jj's markers, `%%%%%%%` is a diff to apply to the `+++++++` snapshot. Resolving a parent's conflict resolves its descendants on the automatic rebase. Follow the `resolve-conflicts` skill for what to keep.
- Read `references/stack-and-conflict-details.md` before you restack after a squash merge, move a child stack, publish from a stack other sessions share, or fix a divergent change.

## 6. Ancestry, diffs and absence

- Ancestry is a graph query: `jj log -r '<fix> & ::<pin>'` (empty means absent) or `git merge-base --is-ancestor <sha> <ref>`. A PR title, a push time, a log window or an issue's status does not answer it. To find an issue's commit on main: `jj log -r '::main@origin & description(substring:"<KEY>")'`.
- For what your work changed, diff the commit against its parent: `jj diff -r <rev> --stat`. `git diff trunk..feat` across a moved trunk counts trunk's work as yours.
- `jj diff` has no `--numstat`, and its diff algorithm is not git's. For figures compared with GitHub's, use `gh api repos/<o>/<r>/pulls/<n>/files` or `git diff --numstat` at git's default algorithm (a configured `diff.algorithm` changes counts), and check each row.
- A long-lived working copy is not main. Test main in its own directory: `git archive origin/main | tar -x -C "$(mktemp -d)"`.
- `jj file show` prints nothing and exits 0 for a glob that matches nothing. A literal path that matches nothing errors, which `2>/dev/null` or `$(...)` hides. Count bytes (`wc -c`) before you call content absent.
- If `jj git fetch` exits 0 but the ref did not move, compare `git ls-remote origin <branch>` with `jj log -r '<branch>@origin'`.
