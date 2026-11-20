# jj command lookup

The ownership and descendant checks in SKILL.md, section 3, apply to every rewriting command here.

## Commands

| Task | Command |
|------|---------|
| Status | `jj status` |
| Log | `jj log`. Flat: `jj log --no-graph`. One JSON object per commit: `jj agent-log` |
| Diff | `jj diff --git [-r <rev>] [--stat]` |
| Show `@` | `jj log -r @` |
| Describe `@` | `jj describe -m "message"` |
| New change | `jj new [<rev>] [-m "message"]` |
| Insert before `@` | `jj new -B @` |
| Edit a change | `jj edit <rev>` |
| Next/previous | `jj next --edit` / `jj prev --edit` |
| Squash private `@` into private parent | `jj squash -u` |
| Collapse a stack | `jj squash --from 'aaa::eee' --into zzz -m "msg"` |
| Squash into one ancestor by path | `jj squash --into <change> -m "msg" -- <paths>` (never into a published head: it only gets new child commits) |
| Split by path | `jj split [-r <rev>] -m "<message for the named paths>" <paths>` (the named paths stay; the rest move to a new child) |
| Abandon | `jj abandon <rev>` (discards the whole change) |
| Rebase | `jj rebase --help` has each flag's effect and the diagrams |
| Bookmarks | `jj bookmark list`, `jj bookmark set <name>` |
| Push one bookmark | `jj git push --bookmark <name>` |
| Fetch | `jj git fetch` |
| Update a stale workspace | `jj workspace update-stale` |

- Do not re-describe commits only to push them: `describe` rewrites the commit and moves every descendant.

## Without a terminal

- Always pass `-m` to `split`, and `-m` or `-u` to `squash`. Paths alone still open an editor. The local `jj-editor` refuses to start without a TTY: that is a fail-safe, not a reason to leave out `-m`.
- Use `jj diff --git`. The default word-level diff runs old and new text together without color and is unreadable.

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
- Labels ending in `@` in `jj log` (`default@`) are workspace markers, not bookmarks. Check `jj bookmark list` before a push.
- A deleted remote branch deletes its tracking bookmark. Delete untracked local bookmarks yourself.

## Identity

jj refuses to push a commit with no author or committer. If none is configured, get the user's authorization for the identity, then set it before the first commit:

```bash
jj config set --user user.name "Your Name"   # or --repo
jj config set --user user.email "you@example.com"
JJ_USER="Your Name" JJ_EMAIL="you@example.com" jj new -m "message"   # one command
```

## Conflict markers

- `<<<<<<<` and `>>>>>>>` start and end a conflict. `+++++++` is a snapshot of one side. `%%%%%%%` is a diff to apply to that snapshot.
- Resolving a parent's conflict resolves its descendants on the automatic rebase.

## `jj absorb`

- Run it only when `@` sits on a merge of several branches, to route each fix to the branch that last touched the line. To fix one ancestor, use `jj squash --into`, or `jj edit <change>` then `jj new`.
- It leaves new files, ambiguous boundary insertions, mode changes and conflicted files in `@`.
- It records conflicts in destinations instead of aborting, and rebases them and every descendant. Afterwards read `jj diff` and `jj log -r ::@`.

## Local helpers

- `jj-agent-status [--deep] [--json]`: where you are, other agents' work and the changes that need attention, in one view. Run it at session start and for triage. Without it: `jj status`, `jj log`, `jj workspace list`.
- `jj agent-log [-r <revset>]`: `jj log --no-graph -T agent_log` (an alias in `.jjconfig.toml`), one JSON object per commit; `bookmarks` gives each local bookmark with `synced`. Without it: `jj log --no-graph`. Show the user `jj log` or `jj log -T builtin_log_compact`, not this JSON.
- `jj tug` moves the closest bookmark below `@` up to `@`. Without it: `jj bookmark move <name> --to @`.
- `JJ_CONFIG= jj ...` ignores this machine's config, for a test repository. `XDG_CONFIG_HOME` does not hide `~/.jjconfig.toml`.
