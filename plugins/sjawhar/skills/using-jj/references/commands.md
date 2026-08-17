# Covers jj mental model, destructive-command safety, editing, command lookup, push, bookmarks, and non-TTY gotchas.

## Foundation

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj) instead of git. **Never use git commands** unless explicitly told to. If you're thinking `git commit`, `git push`, `git checkout`, `git rebase`, etc. — STOP and use the jj equivalent from this skill.

## Core Mental Model

- **No staging area.** Every `jj` command auto-snapshots the working copy. There is no `git add`.
- **Changes vs Commits.** Change IDs (letters k-z, e.g. `qzmzpxyl`) are *stable* across rewrites. Commit IDs (hex) change when the commit is modified. Prefer change IDs to refer to things.
- **`@` = working copy change.** Not like git HEAD — it represents what's on disk right now, including uncommitted work. `@-` is its parent.
- **Rebases always succeed.** Conflicts are recorded in the commit, not blocking. Descendants auto-rebase when parents change.
- **Commands operate on the repo, not the working copy** — rebase doesn't touch your files or move `@` unless asked.
- **Nothing is ever lost.** Every operation is logged in `jj op log`. You can inspect any previous state with `--at-op` and restore with `jj op restore`. Run `jj st > /dev/null` frequently to create snapshot recovery points.
- **Divergent commits are routine bookkeeping, not damage.** Two commits sharing one change ID (the `/0`, `/4` suffixes) is simply what jj records when a change is rewritten while something still references the old commit — a bookmark, another workspace, or an octopus merge that pins it. It is not corruption and not a reason to stop working. Do not leave it lying around either; see [Resolving divergence](divergence.md).

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

### `jj split` and `jj squash`: `-m`/`-u` is mandatory in non-TTY runs

**Never invoke either command bare in an agent/piped shell.** `jj split` needs a fileset
AND a description policy; paths alone do not prevent an editor launch. `jj squash` likewise
needs an explicit resulting-description policy.

```bash
# Keep named paths in the current/original change; move every other changed path to its child.
jj split -m "child change description" <path1> <path2>

# Split a specific revision by path.
jj split -r <rev> -m "child change description" <path1>

# Squash with an explicit resulting description, or deliberately retain the destination's.
jj squash -m "resulting description"
jj squash -u
jj squash --from 'aaa::eee' --into zzz -m "combined description"
```

`jj-editor` is configured to reject editor launches without a TTY and prints these forms. That
is a fail-safe only: **always pass `-m` or `-u` yourself.** Never pipe a rewriting `jj` command;
check `jj log` afterward to confirm the operation actually occurred.

Prefer not splitting at all — one commit per PR is the default — but when independent changes
must separate, the forms above are the only agent-safe split commands.

### `jj diff` in non-TTY / agent contexts

Standard `jj diff` uses **word-level diffs** that concatenate old and new text without ANSI color codes in non-TTY output. This makes diffs unreadable — e.g. `my-org/aboreturn-value` is actually `[deleted:ab][added:return-value]` rendered without color.

**Always use `--git` for verification in agent/piped contexts:**

```bash
jj diff --git          # Standard unified diff format (readable without color)
jj diff --git -r <rev> # For a specific revision
jj diff --git --stat   # Summary of changed files
```
