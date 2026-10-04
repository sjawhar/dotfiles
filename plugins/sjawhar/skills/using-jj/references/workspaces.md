# Covers local agent helpers, jj workspaces and shared operations, conflict resolution, and `jj absorb`.

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

### `tug` alias

This user has a custom alias: `jj tug` moves the closest bookmark to `@`. It is not a stock command; on other machines, identify the bookmark with `jj bookmark list`, then run `jj bookmark move <name> --to @`.

## Workspaces

You may be in a **jj workspace** (not the default workspace). Check with `jj workspace list`.

**Work in a named workspace, not a shared canonical checkout.** The canonical store is normally
`~/src/<repo>`. Outside a box, the workspace belongs under `~/.worktrees/<repo>/<name>`;
inside a box, use its host-backed `~/boxes/<box>/<name>` area. Read the box's mount contract:
an arbitrary home-directory path can be writable yet disappear with the box.

Pass the canonical store explicitly when adding; without `-R`, the add selects whatever store
the current directory resolves to, which can be a retired one:
`jj -R "$HOME/src/<repo>" workspace add "$HOME/.worktrees/<repo>/<name>" --name <name>`
(substitute the box-backed destination inside a box). Confirm that files were populated and
inspect the new workspace's `.jj/repo` and `.git` pointers. The installed jj wrapper locks
the new worktree entry; verify that protection rather than running a repo-wide prune.
If a `gitdir:` target is missing, use the disk-hygiene recovery procedure. No `.git` means a
non-colocated workspace, not proof its jj state is lost.

Snapshot and push early: a named directory is not stored content and a box-local copy dies
with its box. Retire only your own superseded workspace through the supported
`jj workspace forget` path, never a repository-wide prune. Do not create a workspace merely
for a read-only review or use the shared checkout to park another session's changes.

This user uses **colocated repositories** (jj + git coexist). A `.git` folder is present and tools like `gh` work fine. However, **always use `jj` commands instead of `git`** — git operations can desync the jj state.

In non-default workspaces:
- Plain `jj workspace add` colocates (`git.colocate` defaults true — the new workspace gets a git worktree; jj prints `Created Git worktree for the new workspace.`). A `--no-colocate` workspace has no `.git`, and on fleet boxes the dotfiles `.jjconfig.toml` enables the LFS filter with `required = true`, so the add aborts partway on LFS-tracked files and subprocess `git` exits 128 there. The unblock (`--config git.filter.drivers.lfs.required=false --config fsmonitor.backend=none`, absolute paths) completes the add but leaves LFS files as pointer text — prefer the colocated add. See [shared-store details](shared-store-details.md).
- If the workspace is stale, run `jj workspace update-stale`. It loses nothing — see "How a stale working copy actually works" below for the mechanism and where your edits end up.
- After updating a stale workspace, check `jj log -r @` to confirm your working copy is where you expect

### Parallel Workspaces and Shared Operation Log

#### How a stale working copy actually works (from the source, jj 0.45.1)

Each workspace records its last synchronized operation. `WorkingCopyFreshness::check_stale`
compares that operation with the repository's current one:

- Same operation: fresh.
- Workspace operation ahead of the repository view: reload at the workspace operation.
- Workspace operation is an ancestor: fresh if the on-disk and recorded trees agree,
  otherwise stale.
- Divergent operations: a sibling operation, also handled by `update-stale`.

`recover_stale_working_copy_impl` snapshots on-disk edits into the old working-copy commit
before merging operations. If still stale, it checks out the working-copy commit selected
by the current repository view, then snapshots again. If the old operation is unavailable,
it creates a recovery commit holding the on-disk contents.

The checkout can therefore remove an edited file from the visible directory without
discarding its saved contents. Before updating, record the working-copy change ID with
`jj --ignore-working-copy log -r @ --no-graph -T change_id`. After
`jj workspace update-stale`, inspect `jj log -r 'change_id(<saved-id>)' -p` and the operation
history. A nonempty divergent sibling may hold the edits. Address it by commit ID and
recover the intended paths with `jj restore --from <saved-commit> <paths>` in your own
workspace. Check the complete saved path set, not just a remembered file.

`removed N files` describes a checkout, not proven data loss. Conversely, never assume an
empty sibling is disposable until ownership, bookmarks and descendants have been checked.

### The cross-session immutability guard

The session overlay in `JJ_CONFIG` adds protection for other sessions' work. A refusal can
also come from ordinary trunk, tag or remote-reference protection. Identify the actual pin
before diagnosing it; ownership alone does not decide mutability.

The recorded guard has two operands: `builtin_immutable_heads()` and a session-ownership
filter. Exemptions for your trailer, `present(@)`, empty commits and undescribed orphaned
snapshots subtract only from the second operand. They do not remove protection supplied
by the first. Inspect the current configuration rather than assuming a historical revset:
`jj config get 'revset-aliases."builtin_immutable_heads()"'`.

- An empty or undescribed commit can contain recovery work or support another workspace.
  A guard exemption is not cleanup authorization.
- Your own commit beneath another owner's commit is protected through ancestry.
- An untracked remote bookmark can freeze an otherwise mutable head or its ancestor,
  even if you did not push it. Pushed and immutable are independent properties.
- A human shell without the session overlay still has its configured builtin protections.
- Do not override a real pin or another owner's protection. Append a fix on a fresh child
  of a published or reviewed head. Managed fork movement belongs to the fork workflow.

A refused mutation exits 1 with empty stdout and its explanation on stderr. Suppressing that
error makes later checks inspect an unchanged tree, so keep stderr and verify the changed
artifact before interpreting a probe.

All workspaces share one commit store and operation log. State-changing operations,
including snapshots taken by otherwise read-like commands, affect that shared history.
Use `--ignore-working-copy` when inspecting another session's store.

**Before a rewrite:** inspect every descendant and bookmark of the exact revisions you
intend to change. This applies to describe, amend, squash, abandon, absorb and every
rebase form, not only `-s` or `-b`. `-r` can reparent descendants onto the old parents,
removing your contribution from their trees. A release merge is a descendant too:
coordinate with its owner and let the release workflow advance it.

Use only named revisions you own. Do not mutate broad selections such as
`visible_heads() & ~immutable()`. A private stack whose base was squash-merged must
select only its own unmerged commits; [rebase mechanics](rebase.md) and
[stack details](stack-and-conflict-details.md) explain the boundary. A moved base alone
is not a reason to rewrite a reviewed branch.

An operation's description does not bound its descendants: a broad rebase, or a single-revision
rebase beneath a many-parent release merge or a live wrapper's working copy, can move far more
than it names. Reconstruct a recovery with forward changes, never a repository-wide undo,
operation restore or operation revert.

**Advance a served checkout only when its working-copy change is empty.** Fetch first
and inspect its actual working-copy state. Leave another session's edits in place and
coordinate; do not park them with `jj new`. If an earlier operation already parked them,
inspect the old working-copy commit and recover its complete path set with its owner.
After `jj new`, `@-` names the new parent, not the parked edits.

Do not split human-readable diff output on spaces to recover paths: renames and spaced
filenames need structured handling. Recover the complete saved path set, including lockfiles
and generated files, not only the paths you remember editing.

Use named jj workspaces rather than cloning a shared checkout. A local clone can retain
the shared store as its remote and push stray refs into it; changing a remote from the
wrong directory is not a repair of that clone.

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
