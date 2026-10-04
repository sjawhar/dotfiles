# Covers jj mental model, destructive-command safety, editing, command lookup, push, bookmarks, and non-TTY gotchas.

## Foundation

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj) instead of git. **Never use git commands** unless explicitly told to. If you're thinking `git commit`, `git push`, `git checkout`, `git rebase`, etc. — STOP and use the jj equivalent from this skill.

## Core Mental Model

- **No staging area.** Every `jj` command auto-snapshots the working copy. There is no `git add`.
- **Changes vs Commits.** Change IDs (letters k-z, e.g. `qzmzpxyl`) are *stable* across rewrites. Commit IDs (hex) change when the commit is modified. Prefer change IDs to refer to things.
- **`@` = working copy change.** Not like git HEAD — it represents what's on disk right now, including uncommitted work. `@-` is its parent.
- **A successful rebase can record conflicts.** A refusal is not success; read the result. Descendants can rebase automatically when a parent changes.
- **Repo operations can move working copies indirectly.** Rewriting an ancestor can rewrite its working-copy descendants. Rebasing a different branch does not select it for your next edit; inspect `@` afterward.
- **Snapshots are recoverable; unsnapshotted files are not yet stored.** Inspect saved operations with `jj op log` and `--at-op`, then recover the identified paths with a forward `jj restore --from <commit> <paths>` in your own workspace. Never use `jj op restore`, `jj op revert` or `jj undo` in a shared repository.
- **Divergent commits are routine bookkeeping, not damage.** Two commits sharing one change ID (the `/0`, `/4` suffixes) is simply what jj records when a change is rewritten while something still references the old commit — a bookmark, another workspace, or an octopus merge that pins it. It is not corruption and not a reason to stop working. Do not leave it lying around either; see [Resolving divergence](divergence.md).

## CRITICAL: Scope Destructive Commands

Before any jj command that reverts, discards, or rewrites, ask its blast radius and scope it to a path or revision. An instruction not to touch a file does not protect it from an unscoped command that affects the whole tree.

- **`jj restore` without a path reverts the WHOLE working copy.** Always name a path: `jj restore --from <rev> <path>`. If you mean one file, name that file.
- **`jj abandon`** discards a whole change.
- **`jj undo` / `jj op restore`** are repo-wide time travel: they also undo unrelated work since that operation, including other agents' work in other workspaces.
- New files are not recoverable until snapshotted. Once saved, inspect their commit rather than assuming they exist only in the current directory.

**Recover an accidental restore:** run `jj op log` to find the offending operation, then use a **path-scoped** `jj restore --from <commit-before-it> <path>`. Before restoring, `jj op log --limit N` plus `jj --at-op=<op> file list` lets you inspect what existed at a past operation.

## Edit an unpublished, unshared change in place

For your own unpublished change with no other owner's descendants, `jj edit <change>`
and ordinary file edits are the intended mechanism. A bookmark alone does not change
that. Do not create temporary children just to squash them back into such a private change.

A published or reviewed head is different: run `jj new <head>` before the first edit,
then publish the appended change. Editing the old head amends it and automatically
rebases any descendants. A bookmark does not make a published or shared commit safe
to amend.

For a merge, resolve it in its own working-copy commit, but do not path-extract its
resolution with split or squash. `--ignore-working-copy` is for inspection that must
not snapshot somebody else's workspace, never for proving your own on-disk changes.

## Recover with one inspected forward change

Read the complete failure and inspect `@`, bookmarks, ownership and descendants before
repairing anything. Never use repository-global undo, restore-operation or revert-operation
commands in a shared store, even once and even with your own operation ID.

Use `jj-agent-status` when available, or ordinary `jj status`, `jj log` and
`jj workspace list`. Divergence is bookkeeping, not proof of damage. Investigate an
unexpected state from the saved commits rather than guessing or looping on undo.

## Before and after publication

1. Accumulate related edits in your own unpublished, unshared change.
2. Keep its description meaningful. Describe with `-m` when needed; `describe` is a
   rewrite, not a staging operation.
3. Publish only its named bookmark, after inspecting the dry run and recording the
   intended commit ID. Verify that exact remote bookmark afterward.
4. Run `jj new` after the successful push. Any later review fix starts on that child,
   never by editing and re-pushing the published commit itself.

When combining private changes, name the revisions and paths, check descendants first,
and give split/squash an explicit non-interactive description policy (`-m` or squash `-u`).
Do not use a squash into a published head as a workaround for the append-only rule.

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
| Squash a private `@` into its private parent | `jj squash -u` (check ownership and descendants first) |
| Collapse a stack into one commit | `jj squash --from 'aaa::eee' --into zzz -m "msg"` (`-m` required) |
| Squash interactively (TUI) | `jj squash -i` |
| Redistribute edits to ancestors | `jj absorb` (see Gotchas) |
| Abandon a change | `jj abandon <rev>` |
| Rebase (default: branch) | `jj rebase -o <dest>` (defaults to `-b @`) |
| Rebase revisions only | `jj rebase -r <rev> -o <dest>` |
| Rebase revision + descendants | `jj rebase -s <rev> -o <dest>` |
| Rebase whole branch | `jj rebase -b <rev> -o <dest>` |
| Insert revision after target | `jj rebase -r <rev> -A <target>` |
| Insert revision before target | `jj rebase -r <rev> -B <target>` |
| Create merge commit | `jj rebase -s <rev> -o <parent1> -o <parent2>` |
| List bookmarks | `jj bookmark list` |
| Create/move bookmark to `@` | `jj bookmark set <name>` |
| Push a named bookmark | `jj git push --bookmark <name>` |
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
| Check the intended named push without publishing | `jj git push --bookmark <name> --dry-run` |
| Push a specific local bookmark, including its first remote publication | `jj git push --bookmark <name>` |
| Create and publish a named remote bookmark | `jj git push --named <name>=@` |

Use only a named push in a shared store. Bare push and `--tracked` can select other
work, and `--all` / `--deleted` are repository-wide, not substitutes for a failed flag.

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

Changing shared user-level identity requires the user's authorization. Prefer the
already configured identity; a missing identity is not permission to invent one or
silently change other sessions' configuration.

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

Choose logical commits for reviewability. Published review fixes are appended commits;
consolidation of private work must not rewrite a published head or another owner's work.

### `jj diff` in non-TTY / agent contexts

Standard `jj diff` uses **word-level diffs** that concatenate old and new text without ANSI color codes in non-TTY output. This makes diffs unreadable — e.g. `my-org/aboreturn-value` is actually `[deleted:ab][added:return-value]` rendered without color.

**Always use `--git` for verification in agent/piped contexts:**

```bash
jj diff --git          # Standard unified diff format (readable without color)
jj diff --git -r <rev> # For a specific revision
jj diff --git --stat   # Summary of changed files
```
