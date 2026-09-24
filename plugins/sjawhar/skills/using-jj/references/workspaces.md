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

**Canonical checkouts live at `~/src/<repo>` and are not a place to work.** `/home/ubuntu/src/agent-c` (and every other canonical checkout other sessions share) is the store's default workspace: a `jj new`, edit, or commit there lands in a working copy every co-tenant resolves through. Make a named workspace and work only there: `jj -R /home/ubuntu/src/<repo> workspace add /home/ubuntu/.worktrees/<repo>/<name> --name <name>`. That path is the only place a new workspace goes — never `/tmp`, never a clone — and a new one is made only for a genuinely disjoint parallel lane, not because a session "wants its own". Four stray `jj new`s in shared checkouts on 2026-09-17/18 (the platform PO's at 14:37Z, a dispatched subagent's in `/home/ubuntu/legion` ~04:40Z, two on #19336's own lane) each cost another session a repair. Inferred from those incidents (platform PO via the PR queue, 2026-09-18).

**`/home/ubuntu/agent-c` is the retired store.** Its operation log had grown to 189 GB and every command there costs ~4 s. Do not add workspaces to it. **The trap:** `jj workspace add` without `-R` uses the repo of your current directory, so run from inside any old workspace it silently creates the new one on the retired store. Always pass `-R /home/ubuntu/src/agent-c`, and check with `cat <new-dir>/.jj/repo` (it must resolve under `src/agent-c`). A session still working in one of the old store's workspaces moves its lane with `~/.local/state/disk-hygiene/run-2026-09-18/migrate-lane.sh <old-workspace-dir> <new-name>` (pushes your described commit into the new store by sha, adds a workspace at `~/.worktrees/agent-c/<new-name>`), then `cd`s there. Nothing else is carried: bookmarks nobody pushed, old working copies and the old operation log are deleted with the store once its last session ends.

This user uses **colocated repositories** (jj + git coexist). A `.git` folder is present and tools like `gh` work fine. However, **always use `jj` commands instead of `git`** — git operations can desync the jj state.

In non-default workspaces:
- Plain `jj workspace add` colocates (`git.colocate` defaults true — the new workspace gets a git worktree; jj prints `Created Git worktree for the new workspace.`). A `--no-colocate` workspace has no `.git`, and on fleet boxes the dotfiles `.jjconfig.toml` enables the LFS filter with `required = true`, so the add aborts partway on LFS-tracked files and subprocess `git` exits 128 there. The AGENTC-144 unblock (`--config git.filter.drivers.lfs.required=false --config fsmonitor.backend=none`, absolute paths) completes the add but leaves LFS files as pointer text — prefer the colocated add.
- If the workspace is stale, run `jj workspace update-stale`. It loses nothing — see "How a stale working copy actually works" below for the mechanism and where your edits end up.
- After updating a stale workspace, check `jj log -r @` to confirm your working copy is where you expect

### Parallel Workspaces and Shared Operation Log

#### How a stale working copy actually works (from the source, jj 0.45.1)

Every workspace records the operation id it last synchronised at. On each command jj compares that to the repository's current operation (`lib/src/working_copy.rs`, `WorkingCopyFreshness::check_stale`):

- same operation → **fresh**;
- the workspace's operation is *ahead* of the repo's (this workspace moved and the repo view is older) → **updated**: jj reloads the repo at the workspace's operation, silently;
- the workspace's operation is an *ancestor* of the repo's — some other workspace's operation landed since, typically one that rewrote, described, rebased or abandoned this workspace's working-copy commit — then if the on-disk tree already equals the working-copy commit's tree → **fresh** (nothing to do; this is the "it recovered by itself" case), else → **stale**;
- neither is an ancestor of the other (divergent operations) → **sibling operation**, also handled by `update-stale`.

`jj workspace update-stale` (`cli/src/cli_util.rs`, `recover_stale_working_copy_impl`) then does, in order: (1) **snapshot the on-disk working copy on top of the last-known working-copy operation** — every unsnapshotted edit is committed into the *old* working-copy commit, in the old view, before anything else happens ("Snapshot the current working copy on top of the last known working-copy operation, then merge the divergent operations"); (2) merge the operations; (3) if still stale, reset the colocated git HEAD and check out the working-copy commit the current view names — this prints `Updated working copy to fresh commit <id>` and replaces the files on disk with that commit's tree; (4) snapshot again ("there should be no data loss at least"). If the old operation cannot be loaded at all (abandoned, or lost by the storage backend), it writes a **recovery commit** holding the on-disk contents, parented to the current working-copy commit.

So after an update the edited file is **gone from disk** — that is step 3 replacing the tree, and it is the moment the update reads as "it discarded my edit" (platform PO, 2026-09-21); it did not. Do not re-apply from a copy you kept; the edits you made before the update are in the old working-copy commit — often shown as a *divergent* sibling carrying the same change id (`rsxtlxuq/0`, `rsxtlxuq/1`) — not on the new `@`, and not gone. The recipe: `OLD=$(jj log --ignore-working-copy -r @ --no-graph -T 'change_id.short()')` before updating (`--ignore-working-copy` reads the repo without touching the stale tree), `jj workspace update-stale`, then `jj log -r "change_id($OLD)" -p`: the change id is now divergent, so address the siblings by COMMIT id — the nonempty one holds your edits, and `jj restore --from <its commit id> <paths>` puts them on the new `@` (`--from <change id>` fails with "Change ID is divergent"; reproduced 2026-09-18); an empty one is nothing to keep and safe to abandon (check `descendants(<id>) ~ <id>` is empty first). The only way to lose the edits is to abandon that sibling without looking. Update-stale does not cause losses: the "lossy"/"overwrote" claims are misreadings of cross-session rewrites, and the `removed N files` / `modified N files` lines in update-stale's output describe the checkout, not a loss.

### The cross-session immutability guard

Since 2026-09-18 (AGENTC-318, approved by Sami), every omp agent session's jj config — the harness-injected `~/.cache/omp/jj/omp-attribution-<session-id>.toml` overlay in `JJ_CONFIG` — redefines `immutable_heads()` so that **other sessions' non-empty unpushed commits are immutable to you**. `jj abandon`, `rebase`, `squash`, or `describe` touching another lane's commit fails with `Error: Commit <id> is immutable` — the same guard that protects main.

What that error means and what to do:
- **It is not a bug.** You tried to rewrite a commit that carries another session's `Omp-Session:` trailer (or a human's trailer-less commit). Leave it alone; coordinate with the owning session over hub/envoy instead.
- **Your own commits stay mutable** — commits carrying YOUR session's trailer. Exception: your commit under another session's commit is immutable as their ancestor (rewriting a parent rewrites the descendant — correct).
- **Empty commits are exempt** everywhere: ended sessions' working-copy leftovers stay abandonable by anyone.
- **Your own working copy is exempt** (`present(@)`): the trailer only lands at describe time, so an edited-but-undescribed `@` carries no trailer yet — without this exemption a session could not describe its own fresh snapshot (hit live 2026-09-18 14:1xZ). `@` resolves per invocation, so each session exempts only its own working copy; other workspaces' LIVE `@`s stay guarded against you, described or not.
- **Stray snapshots are exempt** (`description(exact:"") ~ working_copies()`): an undescribed commit that is nobody's working copy — the orphan left when a session `jj new`s away from an edited-but-undescribed `@` — is cleanable by anyone, or clutter would accrete forever (second live edge, 14:16Z). CAUTION: a recovery sibling from `update-stale` has exactly this shape and may hold someone's edits — `jj show` a stray before abandoning it; the guard no longer stops you.
- **`--ignore-immutable` overrides deliberately.** Legitimate only for commits your lane owns (e.g. a successor session amending its predecessor's PR commits). NEVER for another live lane's work — the override existing is what turns an accident into a choice.
- A human shell without the overlay (Sami's terminal) keeps stock jj behaviour: only main-ancestry is immutable.

Mechanics: the overlay's revset is `builtin_immutable_heads() | (ancestors(visible_heads(), 4) ~ ::trunk() ~ description(glob:"*Omp-Session: <your-id>*") ~ empty() ~ present(@) ~ (description(exact:"") ~ working_copies()))` — it guards the 4-generation frontier below every visible head, and ancestry closure (`::immutable_heads()`) protects all deeper history automatically (measured on the ~67k-commit agent-c store: zero foreign non-empty commits escape; warm per-command cost is noise-level). Generated by `~/.dotfiles/omp/extensions/session-env.ts`.

**Every exemption in that revset subtracts from the SECOND operand only.** `~ ::trunk()`,
`~ description(glob:"*Omp-Session: …*")`, `~ empty()`, `~ present(@)` and
`~ (description(exact:"") ~ working_copies())` all sit inside the parentheses and narrow
`ancestors(visible_heads(), 4)`. **Nothing subtracts from `builtin_immutable_heads()`**, which on
jj 0.45.1-sami is `trunk() | tags() | untracked_remote_bookmarks() | untracked_remote_tags()`
(`jj config get 'revset-aliases."builtin_immutable_heads()"'`). So "it is my own commit", "it is
empty", "it is my working copy" all answer the wrong operand whenever the pin is a builtin one.

**`untracked_remote_bookmarks()` is how a commit becomes immutable without you having pushed it
and without anyone touching it.** In agent-c those refs are PR-tracking ones on a remote named
`pr` — `19842@pr`, `19842b@pr` — and they land on a head AFTER the PR exists, so a commit you were
mutating an hour ago refuses today. **Pushed and immutable are independent in both directions:**
pushing does not by itself freeze a commit, and a commit carrying no ref of its own freezes anyway
once one lands on a descendant (ancestry closure). Measured in agent-c 2026-09-23: `45280428` has
no bookmark and is not in `::trunk()`, yet is immutable, because `5e56c648` (`19842@pr`,
`19842c@pr`) and `ea5ce39e` (`19842b@pr`) sit above it — the same commit a lane had restored from
cleanly earlier, frozen since by a ref that arrived over it. Reproduced from scratch (throwaway
repo, jj 0.45.1-sami, 2026-09-23): a commit carrying this session's own `Omp-Session:` trailer —
exempt under operand 2, verified mutable — became immutable the instant a pushed-then-untracked
bookmark pointed at it, with the commit itself never rewritten.

Remedy: **`jj new`, then mutate the fresh child.** Never `--ignore-immutable` for this one: what you
would be overriding is not a guess about ownership, it is a real ref, and the override rewrites
whatever that ref is pinning.

**A refusal inside a script with stderr suppressed is silent, and the run then reports on an
unmutated tree.** `jj restore --into <pinned commit> …` exits **1** with **empty stdout** and the
whole `Error: Commit <id> is immutable` block on **stderr**; under `2>/dev/null`, or any caller that
ignores `$?`, the script continues and every downstream result is a false negative — a
mutation-testing battery scores the unmutated code, a fix loop measures the unfixed tree. Prove the
mutation landed before trusting any result: `jj file show -r <rev> <path>` piped to a `grep` for a
marker only the mutated version carries, never the command having returned.


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
- Rebase onto main with `jj git fetch && jj rebase -o main`, and only when there is a conflict to resolve
- Rebase **your own change**, named: `-r @` / `-s <your change>`. `-s`/`-b` rewrite every DESCENDANT of the named root, and in a shared store those descendants are other sessions' commits: `jj rebase -s <root> -d main@origin` on 2026-09-18 08:55Z rewrote 1,081 commits across every lane (op `ca97c0f7af50`; left in place because undoing it would be a second repo-wide rewrite). Before any `-s`/`-b`, `jj log -r "descendants(<root>) ~ (<your change ids>)"` must be empty. The one routine case that needs `-s` is a stacked branch whose base PR was squash-merged: `jj rebase -s <first own commit> -d main@origin` replays only your commits, where `-b` replays the base's too and every one of them conflicts (reproduced 2026-09-18; agent-c `docs/solutions/2026-09-18-rebasing-a-stacked-pr-after-its-base-was-squash-merged.md`) — the descendants check still comes first. After a store-wide rewrite your local branch head is likely a rebased TWIN of your pushed PR head — same content, new commit id — so before citing or pushing a head compare content, not ids: `git diff <merge-base> <head> | git patch-id --stable` against the pushed head; pushing the twin moves the PR head and re-runs CI, and verdicts then carry on patch-id identity, stated in the packet. After `jj commit <paths>` in a shared checkout, never `jj rebase -r` that new commit away from under `@`: `-r` moves only the named commit and re-parents `@` back onto the old base, so your files on disk silently revert to the pre-fix versions while the push succeeds — the live gh shim ran un-hardened for 44 minutes this way (2026-09-17 20:41-21:25Z). Use `-s <commit>` (or `-b @`), which carries `@` along. A revset like `visible_heads() & ~immutable()` sweeps every other session's unpushed PR bookmark in the store onto the new base — three open-PR bookmarks were moved that way in `~/.dotfiles` on 2026-09-17 (no conflicts, identical patches, so pure churn, and each PR's local bookmark then diverged from its published head)
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
