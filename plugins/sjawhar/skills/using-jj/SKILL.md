---
name: using-jj
description: "Use when performing ANY version control operation, starting a work session, checking repo state, or orienting to a codebase. This user uses jj instead of git — NEVER use git commands. Triggers on: commit, push, pull, branch, checkout, rebase, merge, diff, log, status, stash, reset, cherry-pick, bookmark, workspace, conflict resolution, 'what's the repo state', 'are other agents working here', 'what branches exist', 'starting work', 'orient me'."
---

# Using jj (Jujutsu)

This user uses [jj (Jujutsu)](https://github.com/jj-vcs/jj), not git. **Never use git commands** unless explicitly told to.

## Non-negotiable traps

- **Auto-snapshot:** there is no staging; every `jj` command snapshots. `@` is the on-disk working-copy change; change IDs stay stable across rewrites, commit IDs do not.
- **Moving work to another workspace does not save it; a commit does.** A file copied into a fresh `jj workspace add` directory lives only on disk until some jj command there snapshots it, so the workspace can be registered, its `@` empty, and the content nowhere in the store. One lane moved a 220-line extension out of a shared checkout this way and left it unsnapshotted for two hours (2026-09-26). Two things made that invisible. Its workspace lived under a path outside the agent-box mount list, so the directory exists ONLY inside that box: from anywhere else - including a coordinator checking on it - the path does not exist, and `jj workspace list` shows the workspace registered with an empty `@`, which reads exactly like lost work. I diagnosed it that way and was wrong; the box had the file all along. What was true is that nothing outside that box could see or recover it, and an unsnapshotted file dies with its box. An unsnapshotted file that IS in the store's op log is recoverable with `jj --ignore-working-copy --at-op <op> file show -r <commit> 'root-file:"<path>"'`, but an op-log copy is a stale snapshot, not the current file - the recovered copy here predated two fixes, so restoring from it would have silently reverted them. After moving anything out, put it in a described commit before doing anything else, and confirm with `jj log -r @` that `@` is not empty.
- **`--ignore-working-copy` makes a diff LIE about your tree, because that is precisely what it disables.** It is the right flag for reading another lane's store without snapshotting their working copy — `jj --ignore-working-copy bookmark list`, `log`, `workspace list` — and the wrong one for any question about what is on disk. One lane ran `jj --ignore-working-copy diff --stat` after a tool rewrote a file, read `0 files changed`, and spent twenty minutes diagnosing a recording that had in fact worked (2026-09-26). When the question is "did my edit land", use plain `jj diff` or `git diff HEAD` and let the snapshot happen; keep `--ignore-working-copy` for looking at someone else's repository, where snapshotting IS the harm.
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
  Repair, once it has happened and a reviewer's verdict names the pushed sha: put the bookmark
  back on that sha — `jj bookmark set <name> -r <pushed-sha> --allow-backwards` (without the
  flag jj refuses: `Refusing to move bookmark backwards or sideways`) — after which
  `jj git push -b <name> --dry-run` says `Bookmark <name>@origin already matches` and there is
  nothing to push; then carry the edit onto a fresh child, `jj new <name>` and
  `jj restore --from <amended-sha> <paths>`; then `jj abandon <amended-sha>`, which now deletes
  no bookmark because none points at it. Two repairs that look right and are not:
  `jj abandon <amended-sha>` while the bookmark still points at it **deletes the bookmark**
  (jj prints `Deleted bookmarks: <name>` and a hint about `--deleted`), and the next
  `jj git push -b <name>` is then a delete push — `bookmark: <name> [delete from <sha>]` —
  which removes the branch on origin and GitHub closes the pull request (`gh pr reopen` was
  refused; the content had to move to a new PR — platform PO, 2026-09-21); and
  `jj abandon --retain-bookmarks` moves the bookmark to the *parent*, so the push becomes
  `move backward` onto trunk. Both reproduced on jj 0.45.1-sami, 2026-09-21. Read the
  `Changes to push` line before any push after a repair: `delete from` and `move backward`
  are written in plain words, and `--dry-run` prints them without acting.
  The third half is the rebase case, and "`jj new` before editing" does NOT prevent it:
  `jj rebase` moves the commits you named and leaves `@` exactly where it was. Rebase branch B
  onto A's tip while `@` sits on A's commit, then edit a file for B — the snapshot amends **A**,
  the approved head, and because B is now A's child, B carries the hunk at once, before any
  `jj new`; a `jj new B` afterwards puts the same on-disk file on a third commit. That is the
  reproduction (throwaway repo, 2026-09-21: after the edit `jj file list` shows the new file in
  A and in B; after `jj new B`, in `@` too), and it is the evidence for the mechanism. The rule
  is therefore not "make a commit before editing" but **assert which commit `@` is on before
  editing** — `jj log -r @ --no-graph -T 'change_id.short() ++ " " ++ description.first_line()'`
  — after every rebase, edit, or workspace update. Once a rewrite is suspected, a different
  instrument answers a different question: grep the hunk's own identifier in the file at each
  sha along the branch (pre-rebase, the approved tip, the current tip) — absent, absent, present
  pins WHERE the hunk entered the history (one lane, 2026-09-21). That is a presence probe; the
  count it returns is occurrences within one file at one sha and says nothing about how many
  commits carry the hunk. And diff from the sha a gate actually cited, never from the branch
  name: "I only appended" can be true of intent and false of history.
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
- **Colocated repos look dirty to git:** jj parks git HEAD at `@`'s parent, so `@`'s content shows in `git status` as uncommitted changes. That is expected state, not a mess to clean up: `git reset --hard`, `git checkout -- .`, `git clean`, or `git stash` there destroys `@`'s work, recoverable only up to the last jj snapshot. The same fact has a measurement consequence: while `@` *is* the bookmark commit, `HEAD` is the commit *before* it — `git show HEAD:<file>` does not even contain a file the bookmark adds — so "measured at HEAD" names the previous commit, and the quieter case is worse: when the bookmark commit *edits* an existing file, `git show HEAD:<file>` succeeds and returns the pre-edit version — present, wrong version, exit 0. After `jj new` off the bookmark, `HEAD` equals it (all three shapes measured 2026-09-21). The general form: `git show HEAD:` sees your top commit only while `@` is a *descendant* of it, so appending a second commit and staying on it leaves HEAD on the first — committed and still lagging, on exactly the file you just changed. A gate reading one revision stale is *consistently* wrong, so it presents as "my fix doesn't work" — the failure imitates the thing you are debugging (three bisection runs lost to it, 2026-09-21). `git rev-parse HEAD` and `jj log -r @-` agree whenever `@` has one parent — on a merge working copy (`tl task`'s octopus) `@-` is every parent and HEAD is the *first* only — so the question is whether `@-` is the commit you meant to test. Measured: `jj new`, `jj commit`, and `jj squash` into the parent all leave the content in `@-` (HEAD sees it); `jj describe` on `@` does not — the edits stay in `@` and HEAD is unchanged. "Commit first" therefore means one of the first three, never a describe. Name the sha you measured, never `HEAD`; `jj new` after a push fixes this and the after-push amend trap at once.
- **Numbers from diffs:** `--stat`'s per-file figure is insertions **plus** deletions (`f | 4 +++-` for 3 added, 1 removed), not a split; `jj diff` has **no `--numstat`** (`unexpected argument`) — its summary line carries only the totals across all files, so a per-file insertions/deletions split needs `git diff --numstat` on the exported refs. For "what did my work change" use the commit against its own parent (`jj diff -r <rev> --stat`). Across a moved trunk, `git diff trunk..feat` (two-dot) counts undoing trunk's own work as yours. Three-dot (`X...feat`) diffs against the **merge-base of the two endpoints you name**, and is right exactly when that merge-base is the fork point your question means — the branch's topology is not the discriminator. Measured on a stack trunk → A (3 files) → feat (1 file), trunk moved: `A...feat` 1 file (merge-base is A's head — right); `trunk...feat` 4 files (merge-base is A's fork point, so all of A is counted — the 3.5x in one lane); `trunk..feat` 5 files; two of your own commits across a base move can share a merge-base deep in the stack (49 files, same lane). When the question is "what did this commit change", own-parent (`jj diff -r <rev> --stat`) has no merge-base to get wrong. And jj's built-in diff is **its own line decomposition, not git's**: where git's four algorithms disagree, jj matched none of them in 3 of 8 synthetic cases (jj 5/4, myers 4/3, histogram 8/7), and on a real PR jj's per-file rows differed from git myers — GitHub's algorithm — while the totals coincided exactly (5514/132), so a matching total proved nothing. A figure that will be compared with GitHub's `additions`/`deletions` is derived from the same source as the oracle (`gh api repos/<o>/<r>/pulls/<n>/files` — unconditionally the oracle's own numbers — or `git diff --numstat` at git's default algorithm; a `diff.algorithm` in config silently changes the counts), never from `jj diff`, and cross-checked per row (2026-09-21).
- **CRITICAL scope, ranked worst first.** (1) `jj undo`, `jj op restore`, and `jj op revert` are **repo-global**: the operation log is per repository, not per workspace, so an undo from ANY workspace rewinds every other workspace's working-copy commit too. one repo has 93+ workspaces and 21 were dirty when this was measured (2026-09-13); a workspace whose session ended is not idle. Never run them, from anywhere, including your own workspace; the earlier recovery hints in this file that name `jj op restore` describe what the operation *could* undo, not a command to run in a shared repo. (2) A bare `jj restore`, `jj abandon`, or `jj new` in a SHARED checkout takes co-tenants' uncommitted work; unscoped `jj restore` reverts the whole tree. The same goes for a REVSET that is not your own change ids: `divergent()`, `mine()`, `empty()`, `main@origin..`, `all:` each match every session's commits in the store, so `jj abandon`/`jj rebase`/`jj restore` take only the change ids you created, named one by one. Your own change id is necessary, not sufficient: another workspace's `@` may sit on top of it — abandoning your own merged divergent commits rebased the shared default workspace's child into conflicts (~331k files churned, overlays lane, 2026-09-18) — so after a squash-merge delete the bookmark, forget your worktree, and leave the merged commits alone; abandon only when `jj log -r "descendants(<id>) ~ <id>"` is empty. A repo-wide `jj undo`/`op restore`/`op revert` run to REPAIR a cross-session mistake is the same forbidden class as the mistake (two lanes did it tonight; each rolled back every other session's work since that operation). Fourth shared-store incident of 2026-09-17/18 (08:4xZ): a cross-session `jj abandon` made another lane's workspace stale (the edits were not lost — `update-stale` snapshots them onto the old working-copy commit first; the recipe for finding them is in `references/workspaces.md`). (3) `jj restore --from @- <explicit paths>` in your OWN workspace is safe and is the sanctioned fail-before technique when a test must be shown failing without the fix; if you need more than that, copy the bytes aside first. (4) A probe that creates commits in a shared store — a `jj squash`/`commit`/`new` run to measure something — is cleaned up by reading **commits**, not the working copy: `jj restore` + `rm` + `jj abandon @` left a clean `jj status` while two commits the probe's `jj squash` had created still carried the probe file (one lane, 2026-09-21). Prefer a throwaway repo under `mktemp -d`; when the probe must run in the real store, list the change ids it created and abandon those by id.
- **`Error: Commit <id> is immutable` on an unpushed commit is the cross-session guard, not a bug.** Agent sessions' jj config makes other sessions' non-empty unpushed commits immutable (2026-09-18). Do not rewrite another lane's work; `--ignore-immutable` is legitimate only for commits your own lane owns. **That guard is not the only reason a commit refuses, and the exemptions people reason about do not reach the other one.** `immutable_heads()` is `builtin_immutable_heads() | (… ~ present(@) ~ empty() ~ description(…))`, and every one of those subtractions applies to the SECOND operand only — **nothing subtracts from `builtin_immutable_heads()`**, which includes `untracked_remote_bookmarks()`. In one repo a PR-tracking ref (`19842@pr`) lands on a head AFTER the PR exists, so a commit you were mutating an hour ago refuses today without having been touched — and without you having pushed it: **pushed and immutable are independent**, in both directions. Remedy: **`jj new` first, then mutate the fresh child**; never reach for `--ignore-immutable`, which rewrites whatever the real ref is pinning. And the refusal is **silent under suppressed stderr**: `jj restore` exits 1 with empty stdout and the whole error on stderr, so a script running it under `2>/dev/null` carries on and reports on an UNMUTATED tree — every result a false negative. Prove the mutation applied — grep the file for a string only the mutated version has — before trusting any result (all measured on jj 0.45.1-sami, 2026-09-23). Full semantics: `references/workspaces.md` § "The cross-session immutability guard".
- **`jj rebase -b @` moves every chain stacked on your branch, not just yours.** In a shared store, run `jj log -r 'descendants(roots(main@origin..@)) ~ ::@'` first: anything it prints is someone else's chain that `-b` will carry along. Use `-s <root of your own chain>` instead.
- **Before treating a commit as a sibling lane's, find out whose it is.** When a moved chain's bookmark carries no session trailer, `jj op log | grep <bookmark>` names the workspace that pushed it. One lane was about to coordinate around a "sibling's" commit that its OWN forgotten workspace had pushed nine hours earlier, and it took another lane's reply to notice. For a STRAY WORKSPACE whose candidates all say "not mine", compare its working-copy commit's TIMESTAMP against each candidate's `jj op log` workspace-add time — two lanes answered "not mine" from exactly that read, which is cheaper and more conclusive than asking two oracles to reason about it.
- **Confirm a push by the POSITIVE result, not by the absence of a failure string.** One lane reported "moved forward" because it had checked that the output lacked `move sideways`, while the push itself said `Nothing changed` and had pushed nothing. The cause was `jj rebase -r @-`, which moves only that commit and re-parents its child `@` onto the old parent — so `@-` became `main` and `bookmark set main -r @-` was a no-op. Assert that `main@origin` equals your own commit id after the push, and in a store other sessions are writing to, address the commit by CHANGE ID rather than by `@-`. Two sessions pushing within seconds is normal there; jj records a `reconcile divergent operations` and both positions survive, so the read-back is what tells you which one is yours. Address your commit by its CHANGE ID rather than `@-`, too: the push creates a new empty `@`, so a check written against `@-` reads a different commit after the push than the one it meant before it, and in a store other sessions write to, a concurrent push can occupy that name. Capture your own commit id BEFORE pushing, then assert `jj log -r 'main@origin' --no-graph -T commit_id` equals it. Two lanes hit this on 2026-09-25 within the same hour, one pushing nothing and one asserting against a commit that was no longer at the name it used. `main@origin` is itself a local cache: before pushing to an open PR, compare `gh api repos/<o>/<r>/branches/main --jq .commit.sha` against `jj log -r 'main@origin' --no-graph -T commit_id` and fetch-and-rebase if they differ. A third lane the same evening landed two of its four pushes on a base GitHub had already moved past, and each one cost a full lane run it then discarded.
- **After any interruption, check WHAT `@` is before trusting a grep of the working copy.** A stale working copy is a summary wearing evidence's clothes: its grep output looks exactly like a grep of main, carries file and line numbers, and can be entirely wrong. One lane resumed in a new box, grepped without checking, and concluded from real grep evidence that main had refactored a whole package AND lost its own already-merged PR — then began re-porting a fix onto a layout that did not exist. `@` had never been main; it was another lane's in-flight refactor. What caught it was an edit failing with `file not found`, not scepticism, at a cost of about five calls and one wrong dispatch. The control is one line: `jj log -r '@ | main@origin'`, and check the two are related. `jj status` does NOT tell you this — it reports a perfectly clean working copy that is twenty commits and one refactor away from main.
- **`git status` cannot certify a jj workspace clean.** In a colocated workspace jj syncs its working-copy commit into git's HEAD, so `git status --porcelain` reads empty while that HEAD sits on no branch and is pushed nowhere; the content is at risk and git says nothing. Use `jj st` and `jj log -r @` in that workspace.
- **A `--no-colocate` workspace has no `.git`, and THIS FLEET's jj config assumes one.** Plain `jj workspace add` is safe here: `git.colocate` defaults true, so the new workspace gets a git worktree and LFS files materialize normally. The trap needs two conditions together, both real on fleet boxes: `~/.dotfiles/.jjconfig.toml` enables `git.filter` with `git.filter.drivers.lfs.required = true` (stock jj: `git.filter.enabled = false`, `fsmonitor.backend = "none"` — verify with `jj config list --include-defaults -T 'source ++ "|" ++ name'`, whose `source` field distinguishes `default` from `user`; the bare list MERGES them and cannot), and the add is `--no-colocate` (or runs where colocation is off). Then the add ABORTS partway (`Failed to call the lfs filter to convert ...` — `git lfs filter-process` needs a git repo), leaving a partial working copy, and subprocess `git` calls (`check-ignore`, `ls-files`) exit 128 (colocated: 0 or git's ordinary codes — `check-ignore` exits 1 on a non-ignored path). The unblock — every jj command there with `--config git.filter.drivers.lfs.required=false --config fsmonitor.backend=none`, absolute paths (an overlay pattern) — exits 0 but **leaves LFS files as pointer text** (`Warning: Failed to use filter to convert some files`; a 127-byte pointer where the snapshot baseline should be), so tooling that reads those files operates on pointers silently. Prefer the colocated add; reach for the flags only when you must, knowing the tradeoff (reviewer's reproduction, 2026-09-22; first documented in that lane's archive; surfaced after a Release 1 implementer lost setup time — hiring lane).
- **A workspace inherits colocation from the workspace you run `jj workspace add` in, and inside an agent box its git worktree entry dies to another box's prune unless you lock it.** jj colocates the new workspace only if the current one has a valid `.git` at that moment; `git.colocate` does not override that (scratch, fleet jj 0.45.1-sami, 2026-09-25: from a parent with no `.git` the child had none either; the same parent re-registered by hand gave a normal child). A colocated add writes an UNLOCKED `<store>/.git/worktrees/<dir basename>`, and a bare `git worktree prune` from any other box, which cannot see your box's paths, deletes it: jj stays healthy while git fails with `fatal: not a git repository: <store>/.git/worktrees/<name>` (traced to bare prunes on 2026-09-21 and 09-23; the 09-25 losses fit one). Lock it right after the add: `git -C <store> worktree lock <path> --reason '<why>'`. Remove your own with `jj workspace forget` (it prints `Removed Git worktree for "..."`) on a fixed build, never `git worktree prune`: stock jj 0.45.1's forget runs a repo-wide prune, fixed from `0.45.1-sami.20260909-184010`, so check `jj --version` before forgetting on a shared store. To diagnose, read the workspace's own `.git`: no file means not colocated (git does not work there; run git and `gh` from a colocated checkout); a `gitdir:` line naming a missing directory means the entry was deleted (rebuild it by `disk-hygiene`'s recipe, then lock it). Never match `jj workspace list` against `ls <store>/.git/worktrees/`: git names the entry after the directory basename plus a collision number, and 32 of 67 names differed on 2026-09-25 (platform PO's incident).
- **Read a jj error whole; clap puts the diagnosis on the FIRST line.** A lane read `For more information, try --help` as a push refusal and worked around a problem it did not have, because `| tail -2` had cut the line that said `error: unexpected argument '--allow-new' found`. The instance is worth knowing on its own: in this fleet's jj (0.45.1) `--allow-new` is not a `jj git push` flag, and a NEW bookmark pushes with `jj git push --bookmark <name>` (`-b`) without it. The general rule is the one that costs rounds — never `| tail -N` an error you have not read in full, and never `2>/dev/null` a command whose output you are about to test for emptiness; one lane read an empty log as "no log" when the suppressed stderr said the response contained terminal escape sequences and named the flag that would have printed it.
- **An ancestry claim is a query, never a reading.** "Does this pin carry that fix", "does this image contain my merge", "is the pushed head behind my work" are all answered by asking the graph the exact question — `jj log -r '<fix> & ::<pin>'` (empty means absent), or `git merge-base --is-ancestor <sha> <ref>` — and never by reading a PR title, a push time, or a length-limited log window. Three lanes got it wrong on 2026-09-25 the three available ways: a bump PR whose title implied fixes it did not carry (one image constant served two services), a deployment checked by guessing from push times when the image's own `org.opencontainers.image.revision` label answers it in one call, and a branch topology inferred from a 10-ancestor log window that never reached the pushed base — concluding the pushed head was stale when it was the base the work stacked on.
- **A successful fetch can still leave the needed ref stale.** When `jj git fetch` exits 0 but an expected remote ref did not move, compare the read-only `git ls-remote origin main` result with `jj log -r main@origin`; this is a diagnostic exception to the no-Git-mutations rule. In a shared-store workspace, if `jj workspace update-stale` selects a fresh empty commit while edits appeared unsnapshotted, recover deliberately: use `jj log -r 'change_id(<wc-change>)'` to find the divergent sibling that holds the snapshot, inspect it, then `jj edit <recovered-change>`. Both checks apply only to these ambiguous states and are inferred from the hosted-lane incident (platform PO, 2026-09-17).
- **"Is main red?" is answered from a pristine extraction, never from the shared checkout.** A long-lived working copy's files are not main's, whatever `jj log` says about its parent; four tests run "against main" in the shared checkout passed 4/4 while the same four failed 4/4 in `git archive origin/main | tar -x -C "$(mktemp -d)"` — the pristine tree found the breaker (#19101) after the checkout had implicated the wrong PR (#19104). One command; use it for any claim about what main does — and the `mktemp -d` is load-bearing: extract into a SUBDIRECTORY, never `/tmp` itself — and in that repo, run `git init -q` in the extraction before anything that resolves task content: `is_checkout_product_manifest_root` requires a `.git`/`.jj` marker beside the checkout shape (measured 2026-09-22: bare extraction False, after `git init -q` True; #19763 strengthens the miss to a named refusal). A checkout-shaped `/tmp` root (44 repo top-level entries, no `.git`) made pytest infer `rootdir=/tmp` for every pytester child session box-wide and turned two suites red on pristine main for three lanes (2026-09-22; quarantined, filed for the `task_content_root()` defect it exposed). Inferred from the e2e lane's 2026-09-17 incident (#19129).
- **No undo loops:** after a failed command, inspect state and make one deliberate, path-scoped fix; a second corrective command without a fresh read is how the damage compounds.
- **Edit in place:** use `jj edit <change>` and edit `@`; do not make throwaway child commits just to squash them back.
- **Non-TTY — `-m`/`-u` is mandatory:** NEVER invoke `jj split` or `jj squash` bare in an
  agent/piped shell — paths make only the fileset noninteractive, not the commit description.
  Always use `jj split -m "child description" <paths...>` and either `jj squash -m "resulting
  description"` or `jj squash -u`. `jj-editor` rejects editor launches without a TTY as a
  last-resort guard; it does not make omitted flags acceptable. Use `jj diff --git` in agent/piped
  contexts. Backticks inside a double-quoted `-m "..."` are shell command substitution: the message
  stores with the substituted word MISSING, and the shell's command-not-found reads as noise on
  stderr — bash: `bash: line 1: filename: command not found`; this harness's tool shell:
  `error: command not found: filename` (both measured 2026-09-22; one such message is merged). Single-quote the
  message, escape the backticks, or use a file/heredoc — and read it back with
  `jj log -r @ --no-graph -T description` before pushing.
- **CRITICAL — publishing a new bookmark, and the two traps jj sets for you:** the one-shot form
  is `jj git push --named <name>=@`, which CREATES and pushes. `--allow-new` does not exist (it
  was a flag in older jj releases, which is why it keeps coming to mind). When you try it, jj
  replies *"tip: a similar argument exists: '--all'"* — **do not take that suggestion.** `--all`
  pushes every local bookmark in the repo; one repo currently has 179, mostly other agents' work.
  The tool's own error message is steering you into a mass push. Ignore it and use `--named`.
  The second trap is that `--named` and `jj bookmark create` are ALTERNATIVES, not a sequence, and
  there are two distinct ways a "push" publishes nothing (both reproduced on jj 0.45.1-sami,
  2026-09-21):
  - After a `bookmark create`, `--named` fails `Error: Bookmark already exists: <name>` and
    publishes nothing. It is LOUD: exit 1, and its Hint names the fix, `jj git push -b <name>`.
    Nothing goes to stdout, so `cmd | tail -1` shows an empty line and `2>&1 | tail -1` shows the
    Hint — either way the exit code is the pipe's, not jj's.
  - **Bare `jj git push` after a `bookmark create` is the genuinely silent one:** the bookmark is
    untracked, so jj prints `Warning: Refusing to create new remote bookmark <name>@origin`, ends
    on `Nothing changed.`, publishes nothing, and **exits 0**. Through any tail that is
    indistinguishable from the real no-op you get when a bookmark is already pushed.
  **So read back what you pushed, every time: `jj log -r '<name>@origin'`** — `Revision ... doesn't
  exist` is the non-push; a commit id is the real one. Exit codes and last lines both lie here.
- **CRITICAL — deleting a remote bookmark is per-name; `--deleted` is always repo-wide:** the
  form is `jj bookmark delete <name>` then `jj git push --remote origin --bookmark <name>` —
  a named push of a locally deleted bookmark deletes it on the remote — and `jj bookmark delete`
  is not the only way a bookmark becomes locally deleted: `jj abandon` of the commit it points
  at deletes it too (`Deleted bookmarks: <name>`; `<name>@origin` stays), so a `-b <name>` push
  after such an abandon is `[delete from <sha>]` (reproduced 2026-09-21). `--deleted` means
  "push ALL deleted bookmarks and tags" (`jj git push --help`, 0.45) and has no per-name
  variant: bare or combined with `--bookmark`, it carries every pending deletion in the shared
  repo — every bookmark any session has `jj bookmark delete`d since its last push — and the
  output merely lists the refs, which nobody reads on a cleanup push. One worker's
  single-branch cleanup this way also deleted a human's closed-PR branch (2026-09-15; content
  stayed reachable, restored with one command). Same failure shape as `--all` above: a
  repo-wide flag in a repo shared by a hundred sessions.
- **Divergence is bookkeeping, not damage:** resolve it deliberately; do not panic or delete remote history. Single-revision commands refuse a divergent change id — `jj rebase -r <change-id>` exits 1 with `Error: Change ID ... is divergent` plus offset hints, and moves NOTHING — loudly, but a `cmd1; cmd2` chain runs on past it, so the branch you then push is still on the old base (near-miss, 2026-09-22; the same night, an edit-script assert failed inside a `;` chain and an empty described commit reached main before its content). The habit that catches the whole class: in any chained command that mutates a repository, gate dependent steps with `&&`, never `;` — a `;` runs the push after the failed edit. Address the commit by its **commit id**, which is never ambiguous (`jj rebase -r <commit-id> -d <dest>` worked first time on the same commit). Two follow-ups the success message does not say: the rebase rewrites the commit, so the id you just used now names the *hidden pre-rebase* snapshot — re-find the survivor via `change_id(...)` or the bookmark — and the divergence itself persists (the other side still exists) until you abandon the stale side — which is safe only when that side has **no descendants** (`jj log -r 'descendants(<id>) ~ <id>'` empty), **no bookmark** (`jj log -r <id> --no-graph -T 'bookmarks'` — 0 bytes; without `--no-graph` the graph glyphs print even on an empty template), and is **your own change id**; missing any of the three, the same command takes another lane's work. When the bookmark rides both sides it shows `(conflicted)`, the name stops resolving (`Error: Name ... is conflicted`) and push refuses; `jj bookmark set <name> -r <surviving commit-id>` closes both. After ANY rebase, assert the new parent before the next command: `jj log -r <bookmark> --no-graph -T 'commit_id.short() ++ " parent=" ++ parents.map(|c| c.commit_id().short()).join(",")'`.
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

**After a squash merge, the merged commits have no ancestry a rebase can recognise.** GitHub's
squash lands the PR as one new commit whose parents do not include any of the branch's commits, so
a branch stacked on that PR still carries them and `jj rebase -b`/`-s` onto `main@origin` re-applies
them: as **empty** commits when main has not touched those files since (clutter; `--skip-emptied`
drops them), as **conflicts** when main has changed one of them since (a 2-sided conflict on the
file, propagated into your own commits — `--skip-emptied` does nothing for those, a conflicted
commit is not empty). The fork is decided per **file**, so a real case is a mix — several empty
commits and one conflicted one — which reads as a broken rebase rather than a duplicate replay; the
natural next move, `--skip-emptied`, drops the empties and leaves the conflict, and the natural
misdiagnosis is that the tool is confused (one lane, 2026-09-21: five duplicates, one
`.gitignore` collision; `git merge-tree` of the un-rebased head against main already exited 1 on that
file before any rebase). jj does no patch-id matching. The probe is positive, not a reading of commit
subjects: `jj log -r '<their-commit> & ::main@origin'` **empty** proves the squash left no ancestry.
The formulation that drops the duplicates is `jj rebase -r <your own commit ids> -d main@origin`;
the merged commits stay on the old base and are nothing to keep. Two things break in the same
event: the merged PR's head branch is auto-deleted, so `<base-branch>@origin` stops resolving
(`Error: Revision \`A@origin\` doesn't exist`, after a fetch that printed `A@origin [deleted]`) — the
loud failure, not a wrong answer — and a stacked branch's base is the commit it actually forked
from, not the base branch's final head, so "rebase the stack" is a per-branch list, never one
instruction (two lanes derived their own lists from origin, 2026-09-21; all three reproduced on
jj 0.45.1-sami the same night).

**A read used as evidence of absence must first be shown to return something.** `jj file show -r
<rev> <path>` has three ways to hand you zero bytes, and a `grep` over the result reads all three
as "the clause is not there": a **literal path that matches nothing** — jj paths are cwd-relative,
so `docs/x.md` from a subdirectory looks for `<subdir>/docs/x.md` — fails loud (`Error: No such
path`, exit 1) but a stdout-only capture (`$(...)`, `| grep`, `2>/dev/null`) throws the error and
the status away; a **glob that matches nothing** (`glob:docs/nothing*.md`) is genuinely silent —
exit 0, no output, no warning; and a genuinely **empty file** is exit 0 and empty too. Only the
byte count separates them: `wc -c` the output before asking it anything, and anchor paths at the
root (`root:docs/x.md`) when the cwd is not the repo root. A control has to be non-empty for a reason
**independent** of what you are testing: a "known-present" item that shares the query's blind spot
returns the same empty and proves nothing (a control against `gh search issues` whose known item was
a pull request — which that command excludes — agreed its way to a false rule about a working tool,
2026-09-21); pick something the tool must return by a different mechanism. A lane preparing a merged-PR check on
2026-09-21 read 0 bytes for a file that is 14,971 bytes on main, and was one grep from reporting a
landed clause as missing; the size check caught it, `git show origin/main:<path>` confirmed. All
three forms reproduced on jj 0.45.1-sami.

