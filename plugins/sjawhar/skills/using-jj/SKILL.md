---
name: using-jj
description: Use when performing ANY version control operation — including when tempted to use git commands (commit, push, pull, branch, checkout, rebase, merge, diff, log, status, stash, reset, cherry-pick). This user uses jj instead of git. Also triggers on bookmark, workspace, or conflict resolution.
---

# Using jj (Jujutsu)

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj) instead of git. **Never use git commands** unless explicitly told to. If you're thinking `git commit`, `git push`, `git checkout`, `git rebase`, etc. — STOP and use the jj equivalent from this skill.

## Core Mental Model

- **No staging area.** Every `jj` command auto-snapshots the working copy. There is no `git add`.
- **Changes vs Commits.** Change IDs (letters k-z, e.g. `qzmzpxyl`) are *stable* across rewrites. Commit IDs (hex) change when the commit is modified. Prefer change IDs to refer to things.
- **`@` = working copy change.** Not like git HEAD — it represents what's on disk right now, including uncommitted work. `@-` is its parent.
- **Rebases always succeed.** Conflicts are recorded in the commit, not blocking. Descendants auto-rebase when parents change.
- **Commands operate on the repo, not the working copy** — rebase doesn't touch your files or move `@` unless asked.
- **Nothing is ever lost.** Every operation is logged in `jj op log`. You can inspect any previous state with `--at-op` and restore with `jj op restore`. Run `jj st > /dev/null` frequently to create snapshot recovery points.
- **Divergent commits are normal.** When multiple workspaces are active, concurrent operations can create divergent commits (IDs with `/0`, `/4` suffixes). This is usually fine — resolve by squashing the copies together.

## CRITICAL: No Undo Loops

**If a jj command doesn't do what you expected, STOP. Do not chain `jj undo` → retry → `jj undo` → retry.**

Every jj operation (including undo) writes to a shared operation log. Undo loops create operation churn that causes divergent commits across all workspaces. One agent running 10 undo/redo cycles in 5 minutes can corrupt the history for every other workspace.

**When something goes wrong:**
1. Run `jj log -r @` to understand your current state
2. If you understand the state, make ONE deliberate fix
3. If you don't understand the state, **ask the user** — don't guess

**Red flags — STOP and ask the user:**
- You're about to run `jj undo` for the second time
- You see `/0`, `/4` suffixes on change IDs (divergent commits)
- `jj log` shows something unexpected and you're not sure why
- You're tempted to `jj op restore` to an earlier state

## Squash Workflow (How This User Works)

All changes accumulate in the working copy change (`@`). Don't create new commits for fixes — just make changes and push again.

1. Work directly in `@` — all file changes are auto-captured
2. When done, push with `jj git push` (see Pushing Changes)
3. For fixes after pushing: just edit files and push again — don't create new commits or re-describe

### Modifying Existing Changes

To modify a change that already has a description, **do NOT make changes in `@`, describe `@`, then squash.** This opens an interactive editor that fails in agent contexts.

**Option 1: Edit the target directly** (preferred)
```bash
jj edit <change_id>    # Move @ to the change you want to modify
# Make your changes directly
jj new                 # Create new empty change when done
```

**Option 2: Squash without describing**
```bash
# Make changes in @ — do NOT run jj describe
jj squash              # Content moves to @-, parent keeps its description
```

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
| Redistribute edits to ancestors | `jj absorb` (see Gotchas) |
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

### Parallel Workspaces and Shared Operation Log

Multiple jj workspaces share **one operation log and one commit store**. Every jj command you run — including `jj st`, `jj undo`, `jj rebase` — writes to that shared log. Other Claude sessions in other workspaces see your operations and vice versa.

**Consequences:**
- Concurrent operations from two sessions create **divergent operations** that jj must reconcile
- Each reconciliation can create divergent commit IDs (the `/0`, `/4` suffixes)
- A rebase that rewrites another workspace's `@` (or its ancestors) makes that workspace stale — this only matters when workspaces share lineage, not when they're on independent branches
- **This is why undo loops are so destructive** — each undo is another shared operation that may trigger reconciliation

**Rules for parallel workspaces:**
- Keep operations minimal and deliberate — don't experiment
- Never chain undos (see "No Undo Loops" above)
- If your workspace is stale, run `jj workspace update-stale` before doing anything else
- Rebase onto main with `jj git fetch && jj rebase -d main`
- Verify your workspace — confirm you're operating on the right directory

## Merge Conflict Resolution

When resolving conflicts after rebase:
1. **Check divergent commits first** — run `jj log` to see what diverged
2. **Never lose functionality** — review what changed in the commits being merged
3. **Don't delete local changes** without explicit permission
4. **Verify after rebase** — compare the current diff (against main) with the pre-rebase diff to confirm no functionality was lost or accidentally reverted
5. **REPLACE, don't DUPLICATE** — when one side is the "old version" and the other is the "new version" of the same logic, keep ONLY the new version. A common agent mistake is keeping both sides, producing duplicate code blocks. After resolving, scan for repeated logic.
6. **Verify before squashing** — run tests, lint, and format checks BEFORE squashing commits together. Failures discovered after squash require another fix-and-squash cycle, triggering cascading rebases.

## Gotchas

### `jj absorb` — how it actually works

`jj absorb` distributes changes from one commit into its ancestors using **blame**. For each changed line, it finds which ancestor last modified that line and moves the edit there.

```
jj absorb [--from=@] [--into=mutable()]

1. Diff @'s tree against @'s parent tree (what you changed)
2. Annotate each line of the PARENT tree via blame → find which ancestor last touched it
3. Assign each diff hunk to the ancestor that owns those lines
4. Rewrite destination commits (3-way merge the hunks in)
5. Rebase @ (remove absorbed hunks) and all descendants
```

**Designed for the megamerge workflow.** When `@` is on top of a merge of multiple branches, absorb distributes edits back to whichever branch commit last touched each line. This is exactly the `tl task` octopus merge pattern.

**Key flags:**
- `--from` (default `@`): which commit to absorb from
- `--into` (default `mutable()`): which ancestors are eligible destinations. Only ancestors of `--from` within this set are considered. **Immutable commits (like main@origin) are never touched.**

**What absorb CANNOT route (stays in @):**
- **New files** — no blame history, silently skipped
- **Ambiguous insertions** — pure insertions at the boundary between two annotation ranges
- **File mode changes** — only content changes are absorbed
- **Symlinks and submodules** — skipped with warning
- **Conflicted files in source** — skipped entirely

**What can go wrong:**
- Absorb can **create conflicts** in destination commits if hunks don't apply cleanly. It does NOT abort — it records the conflict and continues.
- After absorb, destination commits and all descendants (including @) are rebased. This can cascade through the graph.

**Always verify after absorb:**
```bash
jj diff          # Check what's left in @
jj log -r ::@    # Check for (conflict) markers on ancestors
```

**Two-phase save pattern** (when absorb isn't enough):
1. `jj absorb` — routes edits to existing lines via blame
2. `jj squash --into <change_id> -- <paths>` — routes new files by path
3. `jj diff` — verify `@` is empty (nothing left unrouted)

### `jj squash` opens editor when both changes have descriptions

When both `@` and `@-` have non-empty descriptions, `jj squash` opens an interactive editor to combine them. **This always fails in agent/non-TTY contexts.**

If you already described `@` and need to squash:
- `jj squash -m "description"` — set the final description directly
- `jj squash -u` — keep the destination's description, discard source's

But the real fix is to not get into this state — see "Modifying Existing Changes" above.

### `jj diff` in non-TTY / agent contexts

Standard `jj diff` uses **word-level diffs** that concatenate old and new text without ANSI color codes in non-TTY output. This makes diffs unreadable — e.g. `trajectory-labs-pbc/tasksagent-c` is actually `[deleted:tasks][added:agent-c]` rendered without color.

**Always use `--git` for verification in agent/piped contexts:**

```bash
jj diff --git          # Standard unified diff format (readable without color)
jj diff --git -r <rev> # For a specific revision
jj diff --git --stat   # Summary of changed files
```
