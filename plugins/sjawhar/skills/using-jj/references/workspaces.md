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

This user uses **colocated repositories** (jj + git coexist). A `.git` folder is present and tools like `gh` work fine. However, **always use `jj` commands instead of `git`** — git operations can desync the jj state.

In non-default workspaces:
- If the workspace is stale, `update-stale` REPLACES the tree on disk; anything jj can save from an unsnapshotted edit lands in a recovery sibling of your old `@`, and only its change id finds it. So: `OLD=$(jj log --ignore-working-copy -r @ --no-graph -T 'change_id.short()')` first, then `jj workspace update-stale`, then `jj log -r "change_id($OLD)"` — a nonempty sibling is your edit (`jj edit` it, or `jj restore --from` it by path); an empty one is reconciliation debris. A clean working copy has nothing to lose, which is the case to be in: run `jj st` (a snapshot) at the end of every edit batch in a shared-store workspace, because only unsnapshotted edits are exposed.
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
- If your workspace is stale, follow the update-stale recipe above (record `@` with `--ignore-working-copy`, update, check the recovery sibling) before doing anything else
- Rebase onto main with `jj git fetch && jj rebase -o main`, and only when there is a conflict to resolve (Sami, #2092: "Please don't do unecessary rebases (i.e. unless there are merge conflicts)")
- Rebase **your own change**, named: `-r @` / `-s <your change>`. After `jj commit <paths>` in a shared checkout, never `jj rebase -r` that new commit away from under `@`: `-r` moves only the named commit and re-parents `@` back onto the old base, so your files on disk silently revert to the pre-fix versions while the push succeeds — the live gh shim ran un-hardened for 44 minutes this way (2026-09-17 20:41-21:25Z). Use `-s <commit>` (or `-b @`), which carries `@` along. A revset like `visible_heads() & ~immutable()` sweeps every other session's unpushed PR bookmark in the store onto the new base — three open-PR bookmarks were moved that way in `~/.dotfiles` on 2026-09-17 (no conflicts, identical patches, so pure churn, and each PR's local bookmark then diverged from its published head)
- Verify your workspace — confirm you're operating on the right directory

**Cloning away from the shared store:** `git clone --local /path/to/shared/checkout` keeps the shared checkout as `origin`, so the clone's first push lands stray refs back in the store every session works in. The FIRST command executed with cwd inside a new clone is `git remote set-url origin <github-url>`, verified with `git remote -v` from that same cwd — a set-url issued from the wrong directory changes nothing and fails silently. Stray-ref detection in the store: `git for-each-ref --format='%(refname)' | grep refs/heads/<branch>`; remove with `jj bookmark forget` once the content is confirmed reachable on GitHub (dispatched worker, 2026-09-17).

**Advancing a shared checkout that carries someone else's uncommitted edit** (the served `~/.dotfiles` / `~/core-ops` copy after your change landed on main): `jj new main@origin` parks the old `@` — *with the co-tenant's edit* — as an orphan change. Restore from that parked change by its id, and restore **every path it touched**, not the one you remember:

```bash
OLD=$(jj log -r @ --no-graph -T 'change_id.short()')   # BEFORE jj new
jj new main@origin
# derive the path list — NEVER type it from memory:
jj restore --from "$OLD" $(jj diff -r "$OLD" --summary | awk '{print $2}')
diff <(jj diff --stat -r "$OLD") <(jj diff --stat -r @)  # identical, or you dropped one
jj abandon "$OLD"
```

`--from @-` after `jj new` is main and carries nothing. On 2026-09-17 this session restored `mise.toml` alone from the parked change and abandoned it; the same change also held another session's three `omp/plugins/` lockfile edits, which that session had to recover from the hidden commit twenty minutes later. Inferred from that incident (librarian, 2026-09-17), not a rule Sami stated in these words.

A second instance on 2026-09-18: the same session typed four remembered paths into the restore, missed a fifth co-tenant edit (`disk-hygiene/scripts/disk_hygiene.py`), and abandoned the parked change before the diff check — recovered from the hidden commit only because abandoned commits stay reachable by commit id. The `$(jj diff --summary)` derivation above exists so the list cannot be typed from memory.

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
