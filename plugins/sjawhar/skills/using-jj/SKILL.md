---
name: using-jj
description: "Use when performing ANY version control operation, starting a work session, checking repo state, or orienting to a codebase. This user uses jj instead of git — NEVER use git commands. Triggers on: commit, push, pull, branch, checkout, rebase, merge, diff, log, status, stash, reset, cherry-pick, bookmark, workspace, conflict resolution, 'what's the repo state', 'are other agents working here', 'what branches exist', 'starting work', 'orient me'."
---

# Using jj (Jujutsu)

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj), not git. **Never use git commands** unless explicitly told to.

## Non-negotiable traps

- **Auto-snapshot:** there is no staging; every `jj` command snapshots. `@` is the on-disk working-copy change; change IDs stay stable across rewrites, commit IDs do not.
- **CRITICAL scope:** unscoped `jj restore` reverts the **whole tree**. Name the path: `jj restore --from <rev> <path>`. `abandon`, `undo`, and `op restore` have whole-change/repo-wide blast radii.
- **CRITICAL no undo loops:** the operation log is shared across workspaces. After a failed command, inspect state and make one deliberate fix; stop and ask before a second `jj undo`.
- **Edit in place:** use `jj edit <change>` and edit `@`; do not make throwaway child commits just to squash them back.
- **Non-TTY:** if both changes are described, `jj squash` opens an editor—use `-m` or `-u`. `jj split` opens a TUI—pass paths. Use `jj diff --git` in agent/piped contexts.
- **Publishing:** new bookmarks need `jj git push --named <name>=@`; there is no `--allow-new`.
- **Divergence is bookkeeping, not damage:** resolve it deliberately; do not panic or delete remote history.

Details:
- `references/commands.md`
- `references/rebase.md`
- `references/revsets.md`
- `references/workspaces.md`
- `references/divergence.md`
