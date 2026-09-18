---
name: using-jj
description: "Use when performing ANY version control operation, starting a work session, checking repo state, or orienting to a codebase. This user uses jj instead of git — NEVER use git commands. Triggers on: commit, push, pull, branch, checkout, rebase, merge, diff, log, status, stash, reset, cherry-pick, bookmark, workspace, conflict resolution, 'what's the repo state', 'are other agents working here', 'what branches exist', 'starting work', 'orient me'."
---

# Using jj (Jujutsu)

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj), not git. **Never use git commands** unless explicitly told to.

## Non-negotiable traps

- **Auto-snapshot:** there is no staging; every `jj` command snapshots. `@` is the on-disk working-copy change; change IDs stay stable across rewrites, commit IDs do not.
- **CRITICAL — `jj new` comes BEFORE the work, never after:** auto-snapshot puts edits into
  whatever `@` is *now*. If the next piece of work deserves its own commit, run `jj new` first,
  then edit. Running `jj new -m "msg"` after editing creates an **empty** commit with your
  message and strands the work in the previous change — and there is no after-the-fact way to
  separate "my new edits" from "@'s prior content" short of path surgery. The switch-away form
  is the same trap wearing git's clothes: `jj new main@origin` with uncommitted edits does NOT
  carry them along the way `git checkout` would — they are already snapshotted into the commit
  you left, and the new `@` is empty. They look lost; they are not. Recovery:
  `jj log -r 'heads(@-::) | @-'` (or `jj op log`) to find the commit you left, then
  `jj restore --from <that-commit> <paths>` naming the paths, then compare the file list
  (`jj diff -r <that-commit> --stat` vs `jj diff --stat`) before committing — a partial restore
  looks exactly like a complete one until the diff says otherwise. (Extension session,
  2026-09-12, nearly lost real work this way.)
  The same trap has an after-push half: once `jj git push` succeeds, the working copy IS the
  published commit — keep editing and the next snapshot silently amends it, the branch moves
  non-fast-forward with nobody deciding to rewrite history, and a sibling based on the pushed
  commit is orphaned. `jj new` belongs in the same command as the push, never a later step.
  Diagnostic once suspected: `git merge-base --is-ancestor <pushed-sha> <new-head>` non-zero
  proves the amend (dispatched worker, 2026-09-17, twice in one day).
- **CRITICAL — bare `jj describe` rewrites `@`'s existing message:** it does not "commit your
  work"; it renames whatever `@` already is. Before describing, check
  `jj log -r @ --no-graph -T 'description.first_line()'` — if `@` already carries a message that
  matters, you are about to destroy it. One session lost the same long-form commit message three
  times this way, each time after a `squash --into` had quietly moved `@` back onto that commit.
  Recovery: `jj op log` shows the describe op naming the old commit id;
  `jj log -r <that-commit-id> --no-graph -T 'description'` still prints the pre-rewrite text.
- **CRITICAL — never path-extract from a merge commit:** `jj split <paths>` and
  `jj squash --from <merge> <paths>` do not move "the change to those paths"; a merge commit's
  path content *is* its merge resolution, so extraction deletes those files from the merged tree
  and leaves the parent unbuildable (whole files vanish, manifests revert to a parent's version).
  Work that auto-snapshotted into a merge commit stays there or gets **recreated** on a fresh
  child — carving it out is not a recoverable operation short of `jj op restore`.
- **Colocated repos look dirty to git:** jj parks git HEAD at `@`'s parent, so `@`'s content shows in `git status` as uncommitted changes. That is expected state, not a mess to clean up: `git reset --hard`, `git checkout -- .`, `git clean`, or `git stash` there destroys `@`'s work, recoverable only up to the last jj snapshot.
- **CRITICAL scope, ranked worst first.** (1) `jj undo`, `jj op restore`, and `jj op revert` are **repo-global**: the operation log is per repository, not per workspace, so an undo from ANY workspace rewinds every other workspace's working-copy commit too. agent-c has 93+ workspaces and 21 were dirty when this was measured (2026-09-13); a workspace whose session ended is not idle. Never run them, from anywhere, including your own workspace; the earlier recovery hints in this file that name `jj op restore` describe what the operation *could* undo, not a command to run in a shared repo. (2) A bare `jj restore`, `jj abandon`, or `jj new` in a SHARED checkout takes co-tenants' uncommitted work; unscoped `jj restore` reverts the whole tree. The same goes for a REVSET that is not your own change ids: `divergent()`, `mine()`, `empty()`, `main@origin..`, `all:` each match every session's commits in the store, so `jj abandon`/`jj rebase`/`jj restore` take only the change ids you created, named one by one. Your own change id is necessary, not sufficient: another workspace's `@` may sit on top of it — abandoning your own merged divergent commits rebased the shared default workspace's child into conflicts (~331k files churned, overlays lane, 2026-09-18) — so after a squash-merge delete the bookmark, forget your worktree, and leave the merged commits alone; abandon only when `jj log -r "descendants(<id>) ~ <id>"` is empty. A repo-wide `jj undo`/`op restore`/`op revert` run to REPAIR a cross-session mistake is the same forbidden class as the mistake (two lanes did it tonight; each rolled back every other session's work since that operation). Fourth shared-store incident of 2026-09-17/18 (08:4xZ): a cross-session `jj abandon` made another lane's workspace stale, and its `update-stale` then replaced two unsnapshotted edits on disk — the recipe for that state is in `references/workspaces.md`. (3) `jj restore --from @- <explicit paths>` in your OWN workspace is safe and is the sanctioned fail-before technique when a test must be shown failing without the fix; if you need more than that, copy the bytes aside first.
- **`git status` cannot certify a jj workspace clean.** In a colocated workspace jj syncs its working-copy commit into git's HEAD, so `git status --porcelain` reads empty while that HEAD sits on no branch and is pushed nowhere; the content is at risk and git says nothing. Use `jj st` and `jj log -r @` in that workspace.
- **A successful fetch can still leave the needed ref stale.** When `jj git fetch` exits 0 but an expected remote ref did not move, compare the read-only `git ls-remote origin main` result with `jj log -r main@origin`; this is a diagnostic exception to the no-Git-mutations rule. In a shared-store workspace, if `jj workspace update-stale` selects a fresh empty commit while edits appeared unsnapshotted, recover deliberately: use `jj log -r 'change_id(<wc-change>)'` to find the divergent sibling that holds the snapshot, inspect it, then `jj edit <recovered-change>`. Both checks apply only to these ambiguous states and are inferred from the hosted-lane incident (platform PO, 2026-09-17).
- **"Is main red?" is answered from a pristine extraction, never from the shared checkout.** A long-lived working copy's files are not main's, whatever `jj log` says about its parent; four tests run "against main" in the shared checkout passed 4/4 while the same four failed 4/4 in `git archive origin/main | tar -x -C "$(mktemp -d)"` — the pristine tree found the breaker (#19101) after the checkout had implicated the wrong PR (#19104). One command; use it for any claim about what main does. Inferred from the e2e lane's 2026-09-17 incident (#19129).
- **No undo loops:** after a failed command, inspect state and make one deliberate, path-scoped fix; a second corrective command without a fresh read is how the damage compounds.
- **Edit in place:** use `jj edit <change>` and edit `@`; do not make throwaway child commits just to squash them back.
- **Non-TTY — `-m`/`-u` is mandatory:** NEVER invoke `jj split` or `jj squash` bare in an
  agent/piped shell — paths make only the fileset noninteractive, not the commit description.
  Always use `jj split -m "child description" <paths...>` and either `jj squash -m "resulting
  description"` or `jj squash -u`. `jj-editor` rejects editor launches without a TTY as a
  last-resort guard; it does not make omitted flags acceptable. Use `jj diff --git` in agent/piped
  contexts.
- **CRITICAL — publishing a new bookmark, and the trap jj sets for you:** the form is
  `jj git push --named <name>=@`. `--allow-new` does not exist (it was a flag in older jj
  releases, which is why it keeps coming to mind). When you try it, jj replies *"tip: a similar
  argument exists: '--all'"* — **do not take that suggestion.** `--all` pushes every local
  bookmark in the repo; agent-c currently has 179, mostly other agents' work. The tool's own
  error message is steering you into a mass push. Ignore it and use `--named`.
- **CRITICAL — deleting a remote bookmark is per-name; `--deleted` is always repo-wide:** the
  form is `jj bookmark delete <name>` then `jj git push --remote origin --bookmark <name>` —
  a named push of a locally deleted bookmark deletes it on the remote. `--deleted` means
  "push ALL deleted bookmarks and tags" (`jj git push --help`, 0.45) and has no per-name
  variant: bare or combined with `--bookmark`, it carries every pending deletion in the shared
  repo — every bookmark any session has `jj bookmark delete`d since its last push — and the
  output merely lists the refs, which nobody reads on a cleanup push. One worker's
  single-branch cleanup this way also deleted a human's closed-PR branch (2026-09-15; content
  stayed reachable, restored with one command). Same failure shape as `--all` above: a
  repo-wide flag in a repo shared by a hundred sessions.
- **Divergence is bookkeeping, not damage:** resolve it deliberately; do not panic or delete remote history.
- **CRITICAL — `Commit X is immutable` on a rebase in a fork is a stale pin, not a
  protection:** jj's default `immutable_heads()` includes `untracked_remote_bookmarks()`, so a
  superseded release ref a fetch re-materialized, or another fork's PR head, freezes every
  commit beneath it — your branch tips included. NEVER `--ignore-immutable` a rebase or a squash — `jj squash` refusing "would rewrite 188 immutable commits" is the load-bearing guard in a shared store, not an obstacle (astrolabe lane, 2026-09-18) — (it
  rewrites whatever the pin is; last time, the release merges), and NEVER substitute
  `jj duplicate` (new commit ids; the release can no longer match the branch by change id).
  Find the pin: `jj log -r 'immutable_heads() & descendants(<rev>)'`. In a knives-managed
  fork, `knives start` sets the repo's rule to trunk, tags, and the trunk by name on every
  knives remote (`trunk() | tags() | remote_bookmarks(exact:"<trunk>", exact:"upstream") |
  remote_bookmarks(exact:"<trunk>", exact:"origin")`, …) where the repo config states
  none (a rule someone already stated is left, and `knives status` reports it as
  `immutable-heads-rule`); the rebase then goes through. Moving many members at once is
  `knives release rebase`, never per-branch jj.

Details:
- `references/commands.md`
- `references/rebase.md`
- `references/revsets.md`
- `references/workspaces.md`
- `references/divergence.md`

## Publishing from a shared stack

When several sessions' commits sit in one local stack (a shared checkout, or an unpushed
chain nobody owns whole), publish each line of work with `jj duplicate <commit> -d main@origin`
and open the PR from the duplicate. The duplicate has its own commit id and no descendants
in the stack, so later `jj split` / `jj describe` / `jj rebase` on the original stack rewrites
the originals and never moves a published PR head — measured 2026-09-12: one local commit was
rewritten three times during splits while its PR (core-ops #94) stayed put. Duplicating is
therefore the right move for publication from a stack you cannot restructure yet; it is the
wrong move where a release pin must follow the change id (see the fork-release rule above).

## Conflict chains: squash first, resolve once

When a rebase drags a many-commit branch across a moved trunk, each commit re-conflicts on the
same hunks. Do not grind through the chain: squash the branch to one commit (or the few the
reviewer genuinely needs, see the commit-structure rule in CLAUDE.md), then rebase and resolve one
commit's worth of conflicts (Sami, #781, 2026-09-08: "Mindlessly grinding through a bunch of rebase
conflicts is not worth it. Try just squashing it all down to one commit, and that way you only have
to deal with one commit's worth of conflicts. Just work smarter"; #1755, 2026-09-10: "squash commits
down into the minimal number of commits — because then they don't have to deal with annoying
conflict chains when they rebase"). Do not rebase at all when there is no conflict (#2092,
2026-09-11: "Please don't do unecessary rebases (i.e. unless there are merge conflicts)"). The same
shape in an octopus merge: its members share one fork point, never four (#1368, 2026-09-09: "There
should definitely not be four distinct fork points in one. An octopus should have one shared fork
point"). The exceptions are the ones already stated above: a shared stack you cannot restructure
(duplicate instead), and a fork branch whose release pin must follow the change id.

