# Named publication and divergence

Read from the matching step of the using-jj skill. Version-control changes use jj.

- **Confirm the named remote head, not the absence of an error string.** Capture the intended
  commit ID before pushing, then check `jj log -r '<bookmark>@<remote>' --no-graph -T commit_id`.
  `Nothing changed` proves nothing unless that ref already equals the intended commit.
  The ref is a local cache; fetch or read the live remote when freshness matters.
  Follow a successful push with an explicit `jj new` in the same dependent command chain.
  Push itself does not create that child. Check the captured ID, not whichever commit `@-`
  happens to name afterward.

  A relative revision can name a different commit after a rewrite. `jj rebase -r @-` moves that
  parent alone and reparents `@` onto the old parent, so `@-` then names the old base and setting
  the bookmark from it is a no-op. Use named endpoints and a positive comparison with the remote
  head. Fetching a newer base does not itself require a rebase; follow the repository's
  branch-currency policy and preserve reviewed ancestry.

- **Proving a rebase of published work.** An appended commit's push says `move forward`; a
  rebase legitimately says `move sideways`, so its proof is payload equality, not the direction
  word: `git range-diff` plus identical per-commit patch-ids. A rebase that conflicted cannot have
  that equality; its proof is an empty diff outside the resolved files, plus one line per resolved
  file saying what each side contributed and what was kept. State both endpoints of both ranges
  (old base and old tip, new base and new tip; the old tip is the one people get wrong) and
  publish the commit list or a taxonomy (n rebased, m new, k from the base branch), never a bare
  count. Push the old tip to a throwaway ref first so others can reach it, or say which mappings
  rest on your own run. Read the direction word from `jj git push --dry-run`, before the real
  push. On a published branch `jj new` comes before any edit, and `jj restore --from <sha>`
  restores content, not identity: it builds a sibling, and the next push goes sideways again.

- **Edit a private change in place.** For your own unpublished, unshared change, use `jj edit <change>` rather than temporary children to squash back. A published or reviewed head gets a new child before editing.

- **Non-TTY — `-m`/`-u` is mandatory:** NEVER invoke `jj split` or `jj squash` bare in an
  agent/piped shell; paths make only the fileset noninteractive, not the commit description.
  Always use `jj split -m "child description" <paths...>` and either `jj squash -m "resulting
  description"` or `jj squash -u`. `jj-editor` rejects editor launches without a TTY as a
  last-resort guard; it does not make omitted flags acceptable. Use `jj diff --git` in agent/piped
  contexts. Backticks inside a double-quoted `-m "..."` are shell command substitution: the message
  stores with the substituted word MISSING, and the shell's command-not-found reads as noise on
  stderr (bash: `bash: line 1: filename: command not found`; this harness's tool shell:
  `error: command not found: filename`). Single-quote the message, escape the backticks, or use a
  file/heredoc, and read it back with `jj log -r @ --no-graph -T description` before pushing.

- **CRITICAL — publishing a new bookmark, and the two traps jj sets for you:** the one-shot form
  is `jj git push --named <name>=@`, which CREATES and pushes. `--allow-new` does not exist (it
  was a flag in older jj releases, which is why it keeps coming to mind). When you try it, jj
  replies *"tip: a similar argument exists: '--all'"*: **do not take that suggestion.** `--all`
  pushes every local bookmark in the repository, most of them other agents' work. Ignore it and
  use `--named`.
  The failed push publishes nothing, so the first visible symptom is usually the next command
  failing somewhere else: `gh pr create` answers **`No commits between <base> and <branch>`**,
  which reads as a branch or base problem and is really the push that never happened. Read the
  error's first line (`error: unexpected argument '--allow-new'`), not jj's `--help` tail. When a
  publish-then-open sequence fails at the open step, re-read the push's first line before touching
  the branch.
  The second trap is that `--named` and `jj bookmark create` are ALTERNATIVES, not a sequence, and
  there are two distinct ways a "push" publishes nothing (jj 0.45.1-sami):
  - After a `bookmark create`, `--named` fails `Error: Bookmark already exists: <name>` and
    publishes nothing. It is LOUD: exit 1, and its Hint names the fix, `jj git push -b <name>`.
    Nothing goes to stdout, so `cmd | tail -1` shows an empty line and `2>&1 | tail -1` shows the
    Hint; either way the exit code is the pipe's, not jj's.
  - **Bare `jj git push` after a `bookmark create` is the genuinely silent one:** the bookmark is
    untracked, so jj prints `Warning: Refusing to create new remote bookmark <name>@origin`, ends
    on `Nothing changed.`, publishes nothing, and **exits 0**. Through any tail that is
    indistinguishable from the real no-op you get when a bookmark is already pushed.
  **So read back what you pushed, every time: `jj log -r '<name>@origin'`.** `Revision ... doesn't
  exist` is the non-push; a commit ID is the real one. Exit codes and last lines both lie here.

- **CRITICAL — deleting a remote bookmark is per-name; `--deleted` is always repository-wide:**
  the form is `jj bookmark delete <name>` then `jj git push --remote origin --bookmark <name>`; a
  named push of a locally deleted bookmark deletes it on the remote. `jj bookmark delete` is not
  the only way a bookmark becomes locally deleted: `jj abandon` of the commit it points at deletes
  it too (`Deleted bookmarks: <name>`; `<name>@origin` stays), so a `-b <name>` push after such an
  abandon is `[delete from <sha>]`. `--deleted` means "push ALL deleted bookmarks and tags"
  (`jj git push --help`, 0.45) and has no per-name variant: bare or combined with `--bookmark`, it
  carries every pending deletion in the shared repository, every bookmark any session has deleted
  since its last push, and GitHub closes each pull request whose branch disappears. The output
  merely lists the refs, which nobody reads on a cleanup push. It is the same failure shape as
  `--all`: a repository-wide flag in a repository shared by many sessions.

- **Divergence is bookkeeping, not damage:** resolve it deliberately; do not panic or delete
  remote history. Single-revision commands refuse a divergent change ID: `jj rebase -r
  <change-id>` exits 1 with `Error: Change ID ... is divergent` plus offset hints, and moves
  NOTHING. That is loud, but a `cmd1; cmd2` chain runs on past it, so the branch you then push is
  still on the old base. In any chained command that mutates a repository, gate dependent steps
  with `&&`, never `;`: a `;` runs the push after the failed edit. Address the commit by its
  **commit ID**, which is never ambiguous (`jj rebase -r <commit-id> -d <dest>`). Two follow-ups
  the success message does not say: the rebase rewrites the commit, so the ID you just used now
  names the *hidden pre-rebase* snapshot (re-find the survivor via `change_id(...)` or the
  bookmark); and the divergence itself persists until you abandon the stale side. That abandon is
  safe only when the side has **no descendants** (`jj log -r 'descendants(<id>) ~ <id>'` empty),
  **no bookmark** (`jj log -r <id> --no-graph -T 'bookmarks'` prints 0 bytes; without
  `--no-graph` the graph glyphs print even on an empty template), and is **your own change ID**;
  missing any of the three, the same command takes another lane's work. When the bookmark is on
  both sides it shows `(conflicted)`, the name stops resolving (`Error: Name ... is conflicted`)
  and push refuses; `jj bookmark set <name> -r <surviving commit-id>` closes both. After ANY
  rebase, assert the new parent before the next command:
  `jj log -r <bookmark> --no-graph -T 'commit_id.short() ++ " parent=" ++ parents.map(|c| c.commit_id().short()).join(",")'`.
