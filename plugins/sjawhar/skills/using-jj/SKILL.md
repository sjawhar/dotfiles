---
name: using-jj
description: "Use when performing ANY version control operation, starting a work session, checking repo state, or orienting to a codebase. This user uses jj instead of git — NEVER use git commands. Triggers on: commit, push, pull, branch, checkout, rebase, merge, diff, log, status, stash, reset, cherry-pick, bookmark, workspace, conflict resolution, 'what's the repo state', 'are other agents working here', 'what branches exist', 'starting work', 'orient me'."
---

# Using jj (Jujutsu)

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj) instead of git. **Never use git commands** unless explicitly told to. If you're thinking `git commit`, `git push`, `git checkout`, `git rebase`, etc. — STOP and use the jj equivalent from this skill.


## Repo Orientation: `jj-agent-status`

`jj-agent-status` is a Sami-local helper, not a stock `jj` command. It gives you a complete repo orientation in one command — where you are, what needs attention, who else is working here, and what branches exist. Useful when starting a session, checking for other agents, or triaging repo state. On machines without it, use `jj status`, `jj log`, and `jj workspace list`. Not needed for routine operations like push, describe, or rebase.

```bash
jj-agent-status                    # Quick orientation (auto-deep for <15 bookmarks)
jj-agent-status --deep             # Add trunk distance per branch (+N)
jj-agent-status --deep --branches  # Full detail with trunk distance
jj-agent-status --json             # Machine-readable JSONL
jj-agent-status --help             # See all options
```

Example output:
```
@ uzpy on nywr [sami] — 9 files
  default@
  files: session.ts, bus/index.ts, serve.ts...

🤖 AGENTS:
  reskin@ → workable-route-merge: reskin ralph v2-3 (2h8m) ⚠️ editing @ would rebase them

⚡ NEEDS ATTENTION:
  6 undescribed changes (31 files)
  5 divergent
  1 need push: feat/memory-telemetry

📦 5 BRANCHES (13 changes with work)
  1password-reskin-ralph +8  tsqm 2026-03-28 fix: subtle borders...
  fix/sse-backpressure ⚡ +1  xsmw 2026-03-27 fix: add SSE backpressure...

TRUNK: pyxl [dev]
```

This tells you:
- **Where you are** — current change, parent, workspace, files being edited
- **Who else is here** — active agents with session duration and rebase warnings  
- **What needs attention** — undescribed changes, divergent/conflicted, unpushed branches
- **What branches exist** — sorted by recency, with sync status (`*`), divergence (`⚡`), agents (`🤖`), and trunk distance (`+N`)

`jj-agent-status` combines `jj log`, `jj status`, `jj workspace list`, and `oc ps` into one view. Reach for it when you need the big picture, not for every jj interaction.

## Agent Log: `jj agent-log`

`jj agent-log` is a Sami-local alias for `jj log --no-graph -T agent_log`. When Sami's config is installed, it emits one JSON object per line (JSONL). Stock `jj` has no `agent-log` command; use `jj log --no-graph` with an explicit template there.

```bash
jj agent-log                    # default revset, JSONL
jj agent-log -r 'ancestors(@, 5)'  # scoped revset
jj agent-log -r 'bookmarks()'     # all bookmarked changes
jj log --no-graph -r 'ancestors(@, 5)'  # stock fallback
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
- **Divergent commits are routine bookkeeping, not damage.** Two commits sharing one change ID (the `/0`, `/4` suffixes) is simply what jj records when a change is rewritten while something still references the old commit — a bookmark, another workspace, or an octopus merge that pins it. It is not corruption and not a reason to stop working. Do not leave it lying around either; see [Resolving divergence](#resolving-divergence).

## CRITICAL: Scope Destructive Commands

Before any jj command that reverts, discards, or rewrites, ask its blast radius and scope it to a path or revision. An instruction not to touch a file does not protect it from an unscoped command that affects the whole tree.

- **`jj restore` without a path reverts the WHOLE working copy.** Always name a path: `jj restore --from <rev> <path>`. If you mean one file, name that file.
- **`jj abandon`** discards a whole change.
- **`jj undo` / `jj op restore`** are repo-wide time travel: they also undo unrelated work since that operation, including other agents' work in other workspaces.
- Newly created, untracked-but-snapshotted files are most vulnerable: they exist only in the working copy.

**Recover an accidental restore:** run `jj op log` to find the offending operation, then use a **path-scoped** `jj restore --from <commit-before-it> <path>`. Before restoring, `jj op log --limit N` plus `jj --at-op=<op> file list` lets you inspect what existed at a past operation.

## Edit in place, not via throwaway commits

**It is correct and safe for `@` to sit on the commit (or bookmark) you intend to edit.** Auto-snapshot putting your working-copy edits into that commit IS the editing mechanism — there is no staging area, no detached-HEAD danger, and nothing to "protect" the target from. Every state is in the op log, so nothing is lost.

**Git-brain antipattern to avoid:** creating a throwaway child commit on top of the thing you actually mean to edit (`jj new <target>` → edit → `jj squash` back down), or avoiding editing `@` because it "has a bookmark" or "is a merge." It is pure ceremony that produces churn and cascading rebases. Editing `@` while it points at a bookmark just updates that bookmark's commit — which is what you want.

- Edit an existing commit/bookmark: `jj edit <change>`, then edit the files. Done.
- Edit a merge/octopus commit: `jj new <parents...>` creates it; then edit `@` directly (resolve conflicts, tweak content). `@` **is** the merge — you are not sitting "on top of" it.
- A working copy showing uncommitted files on a bookmarked commit is not a hazard; letting a normal `jj` command snapshot them into that commit is the intended behavior, not something to warn about.
- `--ignore-working-copy` is only for read-only inspection when you deliberately don't want to snapshot. It is not a safety ritual for normal editing.

## CRITICAL: No Undo Loops

**If a jj command doesn't do what you expected, STOP. Do not chain `jj undo` → retry → `jj undo` → retry.**

Every jj operation (including undo) writes to a shared operation log. Undo loops create operation churn that causes divergent commits across all workspaces. One agent running 10 undo/redo cycles in 5 minutes can corrupt the history for every other workspace.

**When something goes wrong:**
1. Run `jj-agent-status` to understand your current state
2. If you understand the state, make ONE deliberate fix
3. If you don't understand the state, **ask the user** — don't guess

**Red flags — STOP and ask the user:**
- You're about to run `jj undo` for the second time
- You cannot explain where a divergence came from (investigate its provenance before rewriting anything — divergence itself is fine, *unexplained* divergence is what warrants a look)
- `jj log` shows something unexpected and you're not sure why
- You're tempted to `jj op restore` to an earlier state

## Resolving divergence

Divergence means one change ID has two or more commits. It shows up whenever you rewrite a change (`describe`, `rebase`, `abandon` of an ancestor) while another reference still points at the old commit. Finish the cleanup — but treat it as bookkeeping, not a hazard.

**A local abandon cannot harm the remote.** `jj abandon` edits your local view only: it pushes nothing, origin's refs keep pointing exactly where they did, and a later `jj git fetch` re-materializes whatever you dropped. A lockfile pinning a branch + SHA resolves against origin, so it is unaffected. The `immutable` marker is a guardrail against rewriting *your local copy* of published history — it is not protection for the remote.

Resolution, in order:

1. **Forget the reference you no longer need, then abandon the copy you don't want:** `jj bookmark forget <name>`, then `jj abandon <commit-id>`. Address commits by **commit ID, not change ID** — a divergent change ID is ambiguous; use `jj log -r 'change_id(abc)'` to list every copy.
2. **If jj refuses because the commit is immutable,** pass `--ignore-immutable`. (Tracking the bookmark and forgetting it again also clears the immutability, but the flag is the direct tool.)
3. **If an empty, bookmark-less commit reappears every time you abandon it,** it is another workspace's working copy, not cruft. Check `jj workspace list`. Retire it with `jj workspace forget <name>`; stock jj only stops tracking the workspace and leaves its directory alone. Abandoning its `@` only makes jj recreate one.
4. **Before abandoning anything non-empty, check it is not unsalvaged work.** Read its diff and confirm the content exists elsewhere (a current branch, or a branch on origin). Empty commits and empty octopus merges for superseded releases are always safe.
5. **Verify you destroyed nothing:** capture `git ls-remote --heads origin` before and after, diff the two, and confirm every open PR head commit still resolves.

Rewriting a commit that an octopus merge or a published release pins is fine. The merge is rewritten locally and its bookmark moves off the remote's commit; forget the superseded bookmark and abandon the leftover. Never "fix" local divergence by deleting branches from the remote — that destroys published history to tidy a local view.

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
| Log (agent — Sami JSONL alias) | `jj agent-log` |
| Log (stock, flat) | `jj log --no-graph` |
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
| Collapse a stack into one commit | `jj squash --from 'aaa::eee' --into zzz -m "msg"` (`-m` required) |
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
jj rebase -o 'trunk()'

# Rebase all local branches onto trunk
jj rebase -s 'roots(trunk()..@)' -o 'trunk()'

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
| `trunk()` | Default remote bookmark, or `root()` if none resolves |
| `root()` | Root commit (`zzzzzzzz`) |
| `mine()` | Changes authored by current user |
| `heads(all())` | All branch heads |
| `::x` | Ancestors of x |
| `x::` | Descendants of x |
| `x..y` | Ancestors of y that are not ancestors of x |
| `ancestors(x, depth)` | Ancestors with depth limit |
| `description(substring:x)` | Changes with x in description |
| `bookmarks()` | Changes with bookmarks |
| `remote_bookmarks()` | Changes with remote bookmarks |

**Rebase all branches onto updated trunk:**
```bash
jj rebase -s 'roots(trunk()..@)' -o 'trunk()'
```

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
| Push tracked bookmarks that are ahead of the selected remote | `jj git push` |
| Push all tracked bookmarks | `jj git push --tracked` |
| Push a specific local bookmark, including its first remote publication | `jj git push --bookmark <name>` |
| Create and publish a named remote bookmark | `jj git push --named <name>=@` |

- A plain `jj git push` refuses a local bookmark the remote has never seen. With a local `feature` bookmark and an `origin` remote, stock jj says:
  ```text
  Warning: Refusing to create new remote bookmark feature@origin
  Hint: Run `jj bookmark track feature --remote=origin` and try again.
  Nothing changed.
  ```
- Publish that existing local bookmark with `jj git push --bookmark feature`, or create a named remote bookmark with `jj git push --named feature=@`.
- **There is no `--allow-new`.** Stock jj reports `error: unexpected argument '--allow-new' found`.
- Don't re-describe commits when pushing — just push

### Identity is a push precondition

Configure identity before creating commits you may push. jj rejects any commit in the pushed ancestry with an empty author or committer:

```text
Error: Won't push commit <commit> since it has no author and/or committer set
```

```bash
# User default
jj config set --user user.name "Your Name"
jj config set --user user.email "you@example.com"

# Repository-specific override
jj config set --repo user.name "Your Name"
jj config set --repo user.email "you@example.com"

# One command / CI
JJ_USER="Your Name" JJ_EMAIL="you@example.com" jj new -m "message"
```

`--repo` and user settings affect future commits; set them before the first commit. Redirecting `XDG_CONFIG_HOME` does **not** disable the legacy `~/.jjconfig.toml`, which jj loads before the XDG config. For a hermetic invocation, use `JJ_CONFIG= jj ...`.

**Common mistake**: Labels ending with `@` in `jj log` output (e.g. `default@`, `my-workspace@`) are **workspace markers**, NOT bookmarks. Only names in the bookmark position (without trailing `@`) are actual bookmarks. **Always verify with `jj bookmark list`.**

## Bookmarks

- Bookmarks do **not** auto-advance (unlike git branches).
- `jj bookmark create <name>` creates only a new bookmark and fails if that name exists.
- `jj bookmark set <name>` creates or updates a bookmark by name; it targets `@` by default.
- `jj bookmark move <name> --to <rev>` moves existing bookmarks only; use `--allow-backwards` for a backwards or sideways move.
- When a remote branch is deleted (e.g., after PR merge), the local tracking bookmark is automatically deleted
- Untracked local bookmarks must be deleted manually if desired

### `tug` alias

This user has a custom alias: `jj tug` moves the closest bookmark to `@`. It is not a stock command; on other machines, identify the bookmark with `jj bookmark list`, then run `jj bookmark move <name> --to @`.

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

### `jj squash` needs `-m` whenever it would have to combine descriptions

`jj squash` opens an interactive editor to merge descriptions whenever more than one non-empty description is involved. **That always fails in agent/non-TTY contexts.** It hits two cases:

- `jj squash` when both `@` and `@-` are described
- `jj squash --from <range> --into <rev>` collapsing a stack of described commits into one — the everyday "one commit per PR" case, where every commit in the range has a message

Always pass a description when squashing a range:

```bash
jj squash --from 'aaa::eee' --into zzz -m "the combined message"
```

- `jj squash -m "description"` — set the final description directly
- `jj squash -u` — keep the destination's description, discard the source's

**The failure is easy to miss.** `jj squash` writes the error to stderr and exits non-zero without touching the repo, so if the command is piped (`| tail`) or its stderr discarded, it looks like it worked. Never pipe a rewriting `jj` command; check `jj log` afterwards, or `jj op log` to confirm the operation was actually recorded.

For the two-commit case, the real fix is to not get into this state — see "Modifying Existing Changes" above.

### `jj split` opens an interactive TUI by default

`jj split` with no arguments opens an interactive TUI to choose which changes go into the new commit. **This times out in agent bash sessions.**

To split non-interactively, pass file paths or a revset:
- `jj split <path> [<path>...]` — keep named files in the selected original commit and put the remaining changes in its new child
- `jj split -r <rev> <path>` — split a specific revision

Per the global `Commits` AGENTS.md rule, prefer not splitting at all — one commit per PR is the default.

### `jj diff` in non-TTY / agent contexts

Standard `jj diff` uses **word-level diffs** that concatenate old and new text without ANSI color codes in non-TTY output. This makes diffs unreadable — e.g. `my-org/aboreturn-value` is actually `[deleted:ab][added:return-value]` rendered without color.

**Always use `--git` for verification in agent/piped contexts:**

```bash
jj diff --git          # Standard unified diff format (readable without color)
jj diff --git -r <rev> # For a specific revision
jj diff --git --stat   # Summary of changed files
```
