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

**The shared store's default checkout is not a place to work.** `/home/ubuntu/agent-c` (and every other canonical checkout other sessions share) is the store's default workspace: a `jj new`, edit, or commit there lands in a working copy every co-tenant resolves through. Make a named workspace and work only there: `jj workspace add /home/ubuntu/.worktrees/<repo>/<name> --name <name>`. Four stray `jj new`s in shared checkouts on 2026-09-17/18 (the platform PO's at 14:37Z, a dispatched subagent's in `/home/ubuntu/legion` ~04:40Z, two on #19336's own lane) each cost another session a repair. Inferred from those incidents (platform PO via the PR queue, 2026-09-18).

This user uses **colocated repositories** (jj + git coexist). A `.git` folder is present and tools like `gh` work fine. However, **always use `jj` commands instead of `git`** — git operations can desync the jj state.

In non-default workspaces:
- If the workspace is stale, `jj workspace update-stale` **loses nothing**: it snapshots the on-disk edits into the OLD working-copy commit first, then checks out the fresh one (`cli/src/cli_util.rs`, `recover_stale_working_copy_impl`: "Snapshot the current working copy on top of the last known working-copy operation, then merge the divergent operations"; if the operation itself is gone it writes a recovery commit). What it does not do is carry those edits onto the new `@` — they sit on a sibling. So: `OLD=$(jj log --ignore-working-copy -r @ --no-graph -T 'change_id.short()')` first, then `jj workspace update-stale`, then `jj log -r "change_id($OLD)"` — a nonempty sibling holds your edits; `jj restore --from <it> <paths>` puts them on the new `@`; an empty one is nothing to keep. Sami, 2026-09-18 13:37Z, verbatim: "update-stale doesn't cause losses, do your research" — the 'lossy' claim that circulated that night was wrong; the only way to lose the edits is to abandon the sibling without looking.
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
- If your workspace is stale, follow the update-stale recipe above (record `@`, update, restore from the sibling) before doing anything else — it is safe; nothing is lost by running it
- Rebase onto main with `jj git fetch && jj rebase -o main`, and only when there is a conflict to resolve (Sami, #2092: "Please don't do unecessary rebases (i.e. unless there are merge conflicts)")
- Rebase **your own change**, named: `-r @` / `-s <your change>`. `-s`/`-b` rewrite every DESCENDANT of the named root, and in a shared store those descendants are other sessions' commits: `jj rebase -s <root> -d main@origin` on 2026-09-18 08:55Z rewrote 1,081 commits across every lane (op `ca97c0f7af50`; left in place because undoing it would be a second repo-wide rewrite). Before any `-s`/`-b`, `jj log -r "descendants(<root>) ~ (<your change ids>)"` must be empty. After a store-wide rewrite your local branch head is likely a rebased TWIN of your pushed PR head — same content, new commit id — so before citing or pushing a head compare content, not ids: `git diff <merge-base> <head> | git patch-id --stable` against the pushed head; pushing the twin moves the PR head and re-runs CI, and verdicts then carry on patch-id identity, stated in the packet. After `jj commit <paths>` in a shared checkout, never `jj rebase -r` that new commit away from under `@`: `-r` moves only the named commit and re-parents `@` back onto the old base, so your files on disk silently revert to the pre-fix versions while the push succeeds — the live gh shim ran un-hardened for 44 minutes this way (2026-09-17 20:41-21:25Z). Use `-s <commit>` (or `-b @`), which carries `@` along. A revset like `visible_heads() & ~immutable()` sweeps every other session's unpushed PR bookmark in the store onto the new base — three open-PR bookmarks were moved that way in `~/.dotfiles` on 2026-09-17 (no conflicts, identical patches, so pure churn, and each PR's local bookmark then diverged from its published head)
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
