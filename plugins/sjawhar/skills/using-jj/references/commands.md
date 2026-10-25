# jj command lookup

Ownership and descendant checks from the skill apply to every rewriting command here.

## Model

- `@` is the on-disk working-copy change, with uncommitted work. `@-` is its parent. It is not git's `HEAD`.
- Prefer change IDs (letters k-z). Commit IDs (hex) change on every rewrite.
- A rebase that succeeds can record conflicts. A refusal is not success. Read the result.
- A rewrite of an ancestor rewrites its working-copy descendants.
- Files not yet snapshotted are not stored. Inspect saved states with `jj op log --limit N` and `jj --at-op=<op> file list`, then recover with `jj restore --from <commit> <paths>`.
- Two commits on one change ID (`/0`, `/4` suffixes) is divergence: bookkeeping, not corruption. Resolve it ([stacks and conflicts](stack-and-conflict-details.md)).

## Commands

| Task | Command |
|------|---------|
| Status | `jj status` |
| Log | `jj log`. Flat: `jj log --no-graph`. JSONL: `jj agent-log` (local alias) |
| Diff | `jj diff --git [-r <rev>] [--stat]` |
| Show `@` | `jj log -r @` |
| Describe `@` | `jj describe -m "message"` |
| New change | `jj new [<rev>] [-m "message"]` |
| Insert before `@` | `jj new -B @` |
| Edit a change | `jj edit <rev>` |
| Next/previous | `jj next --edit` / `jj prev --edit` |
| Squash private `@` into private parent | `jj squash -u` |
| Collapse a stack | `jj squash --from 'aaa::eee' --into zzz -m "msg"` |
| Squash into one ancestor by path | `jj squash --into <change> -m "msg" -- <paths>` (never into a published head: a published head only gets new child commits, never amends) |
| Split by path | `jj split [-r <rev>] -m "child description" <paths>` (named paths stay. The rest move to the child) |
| Abandon | `jj abandon <rev>` (discards the whole change) |
| Rebase | see [rebase](rebase.md) |
| Bookmarks | `jj bookmark list`. `jj bookmark set <name>` |
| Push one bookmark | `jj git push --bookmark <name>` |
| Fetch | `jj git fetch` |
| Update stale workspace | `jj workspace update-stale` |

- Never pipe a rewriting command: the pipe hides its exit code and its first error line. Run `jj log` afterward to check it ran.
- Do not re-describe commits only to push them: `describe` rewrites the commit and moves every descendant.

## Non-TTY rules

- Always pass `-m` to `split` and `-m` or `-u` to `squash`. Paths alone still open an editor. The local `jj-editor` refuses to start without a TTY. Treat that as a fail-safe, not a reason to leave out `-m`.
- Use `jj diff --git`. The default word-level diff concatenates old and new text without color and is unreadable.

## Revsets

| Revset | Meaning |
|--------|---------|
| `@`, `@-`, `@+` | Working copy, parent, child |
| `trunk()` | Default remote bookmark, or `root()` |
| `root()` | Root commit (`zzzzzzzz`) |
| `mine()` | Authored by you (never a mutation target in a shared store) |
| `heads(all())` | All heads |
| `::x` / `x::` | Ancestors / descendants of x |
| `x..y` | Ancestors of y that are not ancestors of x |
| `ancestors(x, depth)` | Ancestors, depth-limited |
| `description(substring:x)` | Description contains x |
| `bookmarks()` / `remote_bookmarks()` | Commits with local / remote bookmarks |

## Bookmarks

- Bookmarks do not advance with new commits.
- `create` makes a new name and fails if it exists. `set` creates or updates and targets `@` by default. `move <name> --to <rev>` moves an existing one. A backward or sideways move needs `--allow-backwards`.
- Labels ending in `@` in `jj log` (`default@`) are workspace markers, not bookmarks. Check with `jj bookmark list` before a push.
- A deleted remote branch deletes its tracking bookmark. Delete untracked local bookmarks yourself.
- Plain `jj git push` refuses a bookmark the remote has never seen (`Refusing to create new remote bookmark`, `Nothing changed.`). Bare push and `--tracked` can select other work. Use `--bookmark` or `--named`.

## Identity

jj refuses to push a commit with no author or committer. Set identity before the first commit:

```bash
jj config set --user user.name "Your Name"   # or --repo
jj config set --user user.email "you@example.com"
JJ_USER="Your Name" JJ_EMAIL="you@example.com" jj new -m "message"   # one command
```

- Use the identity already configured. A missing identity is not permission to invent one or change shared user config without the user's authorization.
- `XDG_CONFIG_HOME` does not disable `~/.jjconfig.toml`. For a hermetic call use `JJ_CONFIG= jj ...`.

## Conflict markers

- `<<<<<<<` / `>>>>>>>`: start and end. `+++++++`: a snapshot of one side. `%%%%%%%`: a diff to apply to the snapshot.
- Delete all markers. Resolving a parent conflict resolves descendants by automatic rebase.

## Local helpers

- `jj-agent-status [--deep] [--json]`: orientation in one view, with other agents and changes that need attention. Use it at session start or for triage. Without it: `jj status`, `jj log`, `jj workspace list`.
- `jj agent-log [-r <revset>]`: `jj log --no-graph -T agent_log`, one JSON object per line (`bookmarks` marks unsynced with `*`). Stock fallback: `jj log --no-graph`. Show the user `jj log` or `-T builtin_log_compact`.
- `jj tug`: moves the closest bookmark to `@`. Elsewhere: `jj bookmark move <name> --to @`.

## `jj absorb`

- Use it only when `@` sits on a merge of several branches, to route each fix to the branch that last touched the line. To fix one ancestor, use `jj edit <change>` then `jj new`, or `jj squash --into`.
- It leaves new files, ambiguous boundary insertions, mode changes and conflicted files in `@`.
- It records conflicts in destinations instead of aborting, and rebases them and every descendant. Afterwards run `jj diff` and `jj log -r ::@`.
