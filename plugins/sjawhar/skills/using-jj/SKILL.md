---
name: using-jj
description: MUST USE for ANY version control operations. This user uses jj (Jujutsu) instead of git. Triggers on commit, push, pull, branch, rebase, merge, diff, log, status, conflict, bookmark, or workspace operations.
---

# Using jj (Jujutsu)

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj) instead of git. **Never use git commands** unless explicitly told to.

## Core Mental Model

- **No staging area.** Every `jj` command auto-snapshots the working copy. There is no `git add`.
- **Changes vs Commits.** Change IDs (letters k-z, e.g. `qzmzpxyl`) are *stable* across rewrites. Commit IDs (hex) change when the commit is modified. Prefer change IDs to refer to things.
- **`@` = working copy change.** Not like git HEAD — it represents what's on disk right now, including uncommitted work. `@-` is its parent.
- **Rebases always succeed.** Conflicts are recorded in the commit, not blocking. Descendants auto-rebase when parents change.
- **Commands operate on the repo, not the working copy** — rebase doesn't touch your files or move `@` unless asked.

## Squash Workflow (How This User Works)

All changes accumulate in the working copy change (`@`). Don't create new commits for fixes — just make changes and push again.

1. Work directly in `@` — all file changes are auto-captured
2. When done, push with `jj git push` (see Pushing Changes)
3. For fixes after pushing: just edit files and push again — don't create new commits or re-describe

## Commands (use these instead of git)

| Task | Command |
|------|---------|
| Status | `jj status` |
| Log | `jj log` |
| Diff of current change | `jj diff` |
| Diff of specific change | `jj diff -r <rev>` |
| Show current change | `jj log -r @` |
| Describe current change | `jj describe -m "message"` |
| Create new empty change | `jj new` |
| New change on specific parent | `jj new <rev>` |
| New change with message | `jj new -m "message"` |
| Insert change before current | `jj new -B @` |
| Edit an existing change | `jj edit <rev>` |
| Move to next/prev change | `jj next --edit` / `jj prev --edit` |
| Squash `@` into parent | `jj squash` |
| Squash interactively (TUI) | `jj squash -i` |
| Redistribute edits to ancestors | `jj absorb` |
| Abandon a change | `jj abandon <rev>` |
| Undo last operation | `jj undo` |
| Redo undone operation | `jj redo` |
| Rebase single revision | `jj rebase -r <rev> -d <dest>` |
| Rebase revision + descendants | `jj rebase -s <rev> -d <dest>` |
| Rebase branch | `jj rebase -b <rev> -d <dest>` |
| List bookmarks | `jj bookmark list` |
| Create/move bookmark to `@` | `jj bookmark set <name>` |
| Push | `jj git push` |
| Fetch | `jj git fetch` |
| Update stale workspace | `jj workspace update-stale` |

## Revsets

Revsets are a functional query language for selecting commits. Most commands accept `-r <revset>`.

| Revset | Meaning |
|--------|---------|
| `@` | Working copy change |
| `@-` | Parent of `@` |
| `@+` | Child of `@` |
| `trunk()` | Main/master/trunk on remote |
| `root()` | Root commit (`zzzzzzzz`) |
| `mine()` | Changes authored by current user |
| `heads(all())` | All branch heads |
| `::x` | Ancestors of x |
| `x::` | Descendants of x |
| `x..y` | Range between x and y |
| `ancestors(x, depth)` | Ancestors with depth limit |
| `description(substring:x)` | Changes with x in description |
| `bookmarks()` | Changes with bookmarks |
| `remote_bookmarks()` | Changes with remote bookmarks |

**Rebase all branches onto updated trunk:**
```bash
jj rebase -s 'all:roots(trunk()..@)' -d trunk()
```

The `all:` prefix is required when a revset resolves to multiple revisions (confirms you intended multiple results).

## Conflicts

jj conflict markers differ from git:
- `<<<<<<<` / `>>>>>>>` — start/end of conflict
- `+++++++` — start of a **snapshot** (full content of one side)
- `%%%%%%%` — start of a **diff** (changes to apply to the snapshot)

To resolve: edit the file to remove all markers, keeping the correct content. Resolving a parent conflict auto-resolves descendants via automatic rebasing.

## Pushing Changes

**Before pushing, ALWAYS run `jj bookmark list` to see what bookmarks actually exist.**

| Action | Command |
|--------|---------|
| Push all tracked bookmarks | `jj git push` |
| Push specific bookmark | `jj git push --bookmark <name>` |
| **Create** new remote branch | `jj git push --named <name>=@` |

- `--named` does not require `--allow-new`
- Don't re-describe commits when pushing — just push

**Common mistake**: Labels ending with `@` in `jj log` output (e.g. `default@`, `my-workspace@`) are **workspace markers**, NOT bookmarks. Only names in the bookmark position (without trailing `@`) are actual bookmarks. **Always verify with `jj bookmark list`.**

## Bookmarks

- Bookmarks do **not** auto-advance (unlike git branches) — use `jj bookmark set <name>` to move them
- When a remote branch is deleted (e.g., after PR merge), the local tracking bookmark is automatically deleted
- Untracked local bookmarks must be deleted manually if desired

### `tug` alias

This user has a custom alias: `jj tug` moves the closest bookmark to `@`. Use it to update a bookmark to point at the current change before pushing.

## Workspaces

You may be in a **jj workspace** (not the default workspace). Check with `jj workspace list`.

This user uses **colocated repositories** (jj + git coexist). A `.git` folder is present and tools like `gh` work fine. However, **always use `jj` commands instead of `git`** — git operations can desync the jj state.

In non-default workspaces:
- If the workspace is stale, run `jj workspace update-stale`
- After updating a stale workspace, check `jj log -r @` to confirm your working copy is where you expect

### Parallel Workspaces

Multiple jj workspaces may be active simultaneously (potentially with other Claude sessions).

- **Main branch moves frequently** — rebase often with `jj git fetch && jj rebase -d main`
- **Expect merge conflicts** — resolve without losing others' work
- **Verify your workspace** — confirm you're operating on the right directory

## Merge Conflict Resolution

When resolving conflicts after rebase:
1. **Check divergent commits first** — run `jj log` to see what diverged
2. **Never lose functionality** — review what changed in the commits being merged
3. **Don't delete local changes** without explicit permission
4. **Verify after rebase** — compare the current diff (against main) with the pre-rebase diff to confirm no functionality was lost or accidentally reverted
