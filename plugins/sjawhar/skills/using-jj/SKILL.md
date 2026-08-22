---
name: using-jj
description: "Use when performing ANY version control operation, starting a work session, checking repo state, or orienting to a codebase. This user uses jj instead of git — NEVER use git commands. Triggers on: commit, push, pull, branch, checkout, rebase, merge, diff, log, status, stash, reset, cherry-pick, bookmark, workspace, conflict resolution, 'what's the repo state', 'are other agents working here', 'what branches exist', 'starting work', 'orient me'."
---

# Using jj (Jujutsu)

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj), not git. **Never use git commands** unless explicitly told to.

## Non-negotiable traps

- **Auto-snapshot:** there is no staging; every `jj` command snapshots. `@` is the on-disk working-copy change; change IDs stay stable across rewrites, commit IDs do not.
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
- **Publishing:** new bookmarks need `jj git push --named <name>=@`; there is no `--allow-new`.
- **Divergence is bookkeeping, not damage:** resolve it deliberately; do not panic or delete remote history.

Details:
- `references/commands.md`
- `references/rebase.md`
- `references/revsets.md`
- `references/workspaces.md`
- `references/divergence.md`
