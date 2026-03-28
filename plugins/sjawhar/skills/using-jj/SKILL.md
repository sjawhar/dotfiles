---
name: using-jj
description: Use when performing ANY version control operation — including when tempted to use git commands (commit, push, pull, branch, checkout, rebase, merge, diff, log, status, stash, reset, cherry-pick). This user uses jj instead of git. Also triggers on bookmark, workspace, or conflict resolution.
---

# Using jj (Jujutsu)

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj) instead of git. **Never use git commands** unless explicitly told to. If you're thinking `git commit`, `git push`, `git checkout`, `git rebase`, etc. — STOP and use the jj equivalent from this skill.

## Agent Log: `jj agent-log`

**Always use `jj agent-log` instead of `jj log`** when you need to inspect revision history. It outputs one JSON object per line (JSONL) with no graph, which is far easier to parse than the default human-readable graph.

```bash
jj agent-log                    # default revset, JSONL
jj agent-log -r 'ancestors(@, 5)'  # scoped revset
jj agent-log -r 'bookmarks()'     # all bookmarked changes
```

Each line is a valid JSON object:
```json
{"change":"nywr","commit":"28e998","parents":["xnrv","xqou"],"bookmarks":["sami"],"empty":false,"conflict":false,"divergent":false,"immutable":true,"desc":"sami: octopus merge"}
```

Fields: `change` (stable ID for commands), `commit` (hex, changes on rewrite), `parents` (topology), `bookmarks` (local only, `*` suffix = unsynced), `workspace` (present only if a working copy is here), `empty`/`conflict`/`divergent`/`immutable` (boolean flags), `desc` (first line or null).

Use `jj log` (without `agent-`) only when you need to show the user the human-readable graph, or with `-T builtin_log_compact` for a one-off human-readable view from within an agent environment.


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
| Log (human-readable) | `jj log` |
| Log (agent — JSONL, no graph) | `jj agent-log` |
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
| Rebase (default: branch) | `jj rebase -o <dest>` (defaults to `-b @`) |
| Rebase revisions only | `jj rebase -r <rev> -o <dest>` |
| Rebase revision + descendants | `jj rebase -s <rev> -o <dest>` |
| Rebase whole branch | `jj rebase -b <rev> -o <dest>` |
| Insert revision after target | `jj rebase -r <rev> -A <target>` |
| Insert revision before target | `jj rebase -r <rev> -B <target>` |
| Create merge commit | `jj rebase -s <rev> -o <parent1> -o <parent2>` |
| List bookmarks | `jj bookmark list` |
| Create/move bookmark to `@` | `jj bookmark set <name>` |
| Push | `jj git push` |
| Fetch | `jj git fetch` |
| Update stale workspace | `jj workspace update-stale` |

## Rebase

`jj rebase` moves revisions to different parents while preserving their diffs. The behavior varies significantly depending on which source flag you use.

### Source flags: -r vs -s vs -b

**`-r` (revisions only) -- extracts and re-parents children**

Rebases ONLY the specified revisions. Descendants are re-parented onto the revision's OLD parents, filling the "hole". The revision is "extracted" from the graph.

```
jj rebase -r K -o M

BEFORE        AFTER
M             K'
|             |
| L           M
| |    =>     |
| K           | L'    <-- L was re-parented from K to J (K's old parent)
|/            |/
J             J
```

Use `-r` when you want to move a commit without bringing its descendants. Common for rewriting octopus merge parents.

**`-s` (source + descendants) -- moves subtree intact**

Rebases the specified revision AND all its descendants. The whole subtree moves together.

```
jj rebase -s M -o O

BEFORE        AFTER
O             N'
|             |
| N           M'
| |           |
| M    =>     O
| |           |
| | L         | L
| |/          | |
| K           | K
|/            |/
J             J
```

Use `-s` when you want to transplant a whole feature branch. Multiple `-s` arguments make each a direct child of dest (flattening).

**`-b` (branch) -- moves everything not already on dest**

Rebases the whole "branch" relative to the destination: the set `(dest..rev)::` -- meaning revisions that aren't ancestors of dest, plus ALL their descendants.

Equivalent to: `jj rebase -s 'roots(dest..rev)' -o dest`

```
jj rebase -b M -o O    (same result if you said -b L or -b K)

BEFORE        AFTER
O             N'
|             |
| N           M'
| |           |
| M           | L'
| |    =>     |/
| | L         K'
| |/          |
| K           O
|/            |
J             J
```

Use `-b` when rebasing after a fetch -- it moves your whole branch onto the updated trunk. **This is the default** when no flag is specified (`jj rebase -o dest` implies `-b @`).

### Destination flags: -o vs -A vs -B

| Flag | Behavior |
|------|----------|
| `-o/--onto` (alias `-d`) | Place onto targets. Existing descendants of targets unaffected. |
| `-A/--insert-after` | Like `-o`, but also rebases targets' existing descendants onto the rebased revisions. |
| `-B/--insert-before` | Rebases onto targets' parents, then rebases targets and their descendants onto the rebased revisions. |

`-A` and `-B` can be combined to splice revisions into a specific location in the graph.

### Creating merge commits

Repeat `-o` to create a merge:

```bash
jj rebase -s L -o K -o M     # L now has parents K and M
jj rebase -r @ -o A -o B -o C   # Reset @'s parents to A, B, C (octopus merge)
```

### Common patterns

```bash
# Rebase current branch onto updated trunk (most common)
jj rebase -o trunk()

# Rebase all local branches onto trunk
jj rebase -s 'all:roots(trunk()..@)' -o trunk()

# Reset a merge commit's parents (e.g., drop branches from octopus)
jj rebase -r <merge> -o <parent1> -o <parent2> -o <parent3>

# Extract a commit from middle of chain (descendants stay)
jj rebase -r <middle> -o <new-parent>

# Move whole feature branch onto new base
jj rebase -b <branch-tip> -o <new-base>
```

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
jj rebase -s 'all:roots(trunk()..@)' -o trunk()
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
- Rebase onto main with `jj git fetch && jj rebase -o main`
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

### `jj absorb` — merge workflow only

**Absorb is for merge workflows** — when `@` sits on top of a merge of multiple branches and you want to distribute fixes back to whichever branch owns each line. It uses blame to route each changed line to the ancestor that last modified it.

**Do NOT use absorb to rewrite historical commits.** To fix a specific ancestor commit, use:
- `jj edit <change_id>` → make changes → `jj new` (preferred)
- `jj squash --into <change_id> -- <paths>` (for routing specific files)

**How it works:**
```
jj absorb [--from=@] [--into=mutable()]

1. Diff @'s tree against @'s parent tree (what you changed)
2. Annotate each line of the PARENT tree via blame → find which ancestor last touched it
3. Assign each diff hunk to the ancestor that owns those lines
4. Rewrite destination commits (3-way merge the hunks in)
5. Rebase @ (remove absorbed hunks) and all descendants
```

**What absorb CANNOT route (stays in @):**
- **New files** — no blame history, silently skipped
- **Ambiguous insertions** — pure insertions at the boundary between two annotation ranges
- **File mode changes** — only content changes are absorbed
- **Conflicted files in source** — skipped entirely

**What can go wrong:**
- Absorb can **create conflicts** in destination commits if hunks don't apply cleanly. It does NOT abort — it records the conflict and continues.
- After absorb, destination commits and all descendants (including @) are rebased. This can cascade through the graph.

**Always verify after absorb:**
```bash
jj diff          # Check what's left in @
jj log -r ::@    # Check for (conflict) markers on ancestors
```

### `jj squash` opens editor when both changes have descriptions

When both `@` and `@-` have non-empty descriptions, `jj squash` opens an interactive editor to combine them. **This always fails in agent/non-TTY contexts.**

If you already described `@` and need to squash:
- `jj squash -m "description"` — set the final description directly
- `jj squash -u` — keep the destination's description, discard source's

But the real fix is to not get into this state — see "Modifying Existing Changes" above.

### `jj diff` in non-TTY / agent contexts

Standard `jj diff` uses **word-level diffs** that concatenate old and new text without ANSI color codes in non-TTY output. This makes diffs unreadable — e.g. `my-org/aboreturn-value` is actually `[deleted:ab][added:return-value]` rendered without color.

**Always use `--git` for verification in agent/piped contexts:**

```bash
jj diff --git          # Standard unified diff format (readable without color)
jj diff --git -r <rev> # For a specific revision
jj diff --git --stat   # Summary of changed files
```
