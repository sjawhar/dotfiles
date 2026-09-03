---
name: using-jj
description: "Use when performing ANY version control operation, starting a work session, checking repo state, or orienting to a codebase. This user uses jj instead of git — NEVER use git commands. Triggers on: commit, push, pull, branch, checkout, rebase, merge, diff, log, status, stash, reset, cherry-pick, bookmark, workspace, conflict resolution, 'what's the repo state', 'are other agents working here', 'what branches exist', 'starting work', 'orient me'."
---

# Using jj (Jujutsu)

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj), not git. **Never use git commands** unless explicitly told to.

## Non-negotiable traps

- **Auto-snapshot:** there is no staging; every `jj` command snapshots. `@` is the on-disk working-copy change; change IDs stay stable across rewrites, commit IDs do not.
- **CRITICAL — `jj new` comes BEFORE the work, never after:** auto-snapshot puts edits into
  whatever `@` is *now*. If the next piece of work deserves its own commit, run `jj new` first,
  then edit. Running `jj new -m "msg"` after editing creates an **empty** commit with your
  message and strands the work in the previous change — and there is no after-the-fact way to
  separate "my new edits" from "@'s prior content" short of path surgery.
- **CRITICAL — bare `jj describe` rewrites `@`'s existing message:** it does not "commit your
  work"; it renames whatever `@` already is. Before describing, check
  `jj log -r @ --no-graph -T 'description.first_line()'` — if `@` already carries a message that
  matters, you are about to destroy it. One session lost the same long-form commit message three
  times this way, each time after a `squash --into` had quietly moved `@` back onto that commit.
  Recovery: `jj op log` shows the describe op naming the old commit id;
  `jj log -r <that-commit-id> --no-graph -T 'description'` still prints the pre-rewrite text.
- **CRITICAL — never path-extract from a merge commit:** `jj split <paths>` and
  `jj squash --from <merge> <paths>` do not move "the change to those paths"; a merge commit's
  path content *is* its merge resolution, so extraction deletes those files from the merged tree
  and leaves the parent unbuildable (whole files vanish, manifests revert to a parent's version).
  Work that auto-snapshotted into a merge commit stays there or gets **recreated** on a fresh
  child — carving it out is not a recoverable operation short of `jj op restore`.
- **Colocated repos look dirty to git:** jj parks git HEAD at `@`'s parent, so `@`'s content shows in `git status` as uncommitted changes. That is expected state, not a mess to clean up: `git reset --hard`, `git checkout -- .`, `git clean`, or `git stash` there destroys `@`'s work, recoverable only up to the last jj snapshot.
- **CRITICAL scope:** unscoped `jj restore` reverts the **whole tree**. Name the path: `jj restore --from <rev> <path>`. `abandon`, `undo`, and `op restore` have whole-change/repo-wide blast radii.
- **CRITICAL no undo loops:** the operation log is shared across workspaces. After a failed command, inspect state and make one deliberate fix; stop and ask before a second `jj undo`.
- **Edit in place:** use `jj edit <change>` and edit `@`; do not make throwaway child commits just to squash them back.
- **Non-TTY — `-m`/`-u` is mandatory:** NEVER invoke `jj split` or `jj squash` bare in an
  agent/piped shell — paths make only the fileset noninteractive, not the commit description.
  Always use `jj split -m "child description" <paths...>` and either `jj squash -m "resulting
  description"` or `jj squash -u`. `jj-editor` rejects editor launches without a TTY as a
  last-resort guard; it does not make omitted flags acceptable. Use `jj diff --git` in agent/piped
  contexts.
- **CRITICAL — publishing a new bookmark, and the trap jj sets for you:** the form is
  `jj git push --named <name>=@`. `--allow-new` does not exist (it was a flag in older jj
  releases, which is why it keeps coming to mind). When you try it, jj replies *"tip: a similar
  argument exists: '--all'"* — **do not take that suggestion.** `--all` pushes every local
  bookmark in the repo; agent-c currently has 179, mostly other agents' work. The tool's own
  error message is steering you into a mass push. Ignore it and use `--named`.
- **Divergence is bookkeeping, not damage:** resolve it deliberately; do not panic or delete remote history.
- **CRITICAL — `Commit X is immutable` on a rebase in a fork is a stale pin, not a
  protection:** jj's default `immutable_heads()` includes `untracked_remote_bookmarks()`, so a
  superseded release ref a fetch re-materialized, or another fork's PR head, freezes every
  commit beneath it — your branch tips included. NEVER `--ignore-immutable` a rebase (it
  rewrites whatever the pin is; last time, the release merges), and NEVER substitute
  `jj duplicate` (new commit ids; the release can no longer match the branch by change id).
  Find the pin: `jj log -r 'immutable_heads() & descendants(<rev>)'`. In a knives-managed
  fork, `knives start` sets the repo's rule to trunk, tags, and the trunk by name on every
  knives remote (`trunk() | tags() | remote_bookmarks(exact:"<trunk>", exact:"upstream") |
  remote_bookmarks(exact:"<trunk>", exact:"origin")`, …) where the repo config states
  none (a rule someone already stated is left, and `knives status` reports it as
  `immutable-heads-rule`); the rebase then goes through. Moving many members at once is
  `knives release rebase`, never per-branch jj.

Details:
- `references/commands.md`
- `references/rebase.md`
- `references/revsets.md`
- `references/workspaces.md`
- `references/divergence.md`
