# Publishing

## Check what you pushed

- Record the intended commit ID, then check `jj log -r '<name>@<remote>' --no-graph -T commit_id`. `Revision ... doesn't exist` means nothing was pushed. The ref is a local cache. Fetch or read the live remote when freshness matters.
- Compare with the recorded ID, not with whatever `@-` names later. `jj rebase -r @-` moves only the parent and reparents `@` onto the old parent, so a bookmark set from `@-` then does nothing. Use named endpoints.
- `jj git push --named <name>=<rev> --dry-run` does not create the bookmark. The real push still needs `--named`, not `--bookmark`.
- A push does not create the child. Run `jj new` after it in the same `&&` chain.
- A newer base after a fetch does not require a rebase. Rebase onto it only when the repository's AGENTS.md or contributing docs require branches to be current with it; otherwise keep the reviewed ancestry.

## Pushes that publish nothing

- `--allow-new` does not exist. jj suggests `--all`, which pushes every local bookmark, mostly other sessions' work. Use `--named`.
- Usual symptom: `gh pr create` says `No commits between <base> and <branch>`. Re-read the push's first line before you touch the branch.
- `--named` after `jj bookmark create` fails (`Bookmark already exists`, exit 1. Its hint is `jj git push -b <name>`).
- Bare `jj git push` after `jj bookmark create` warns `Refusing to create new remote bookmark`, prints `Nothing changed.` and exits 0. Behind a `tail` it looks like a real no-op. Exit codes and last lines both lie here. Read back the remote ref every time.
- Read an error whole: clap puts the diagnosis on the first line, and `| tail -N` or `2>/dev/null` can hide it. Never `2>/dev/null` a command whose output you test for emptiness.

## Deleting one remote bookmark

- `jj bookmark delete <name>`, then `jj git push --remote origin --bookmark <name>`.
- `jj abandon` of the commit a bookmark points at also deletes the bookmark locally, so the next `-b <name>` push is `[delete from <sha>]`.
- Never use `--deleted`. It pushes every pending deletion in the shared repository, with or without `--bookmark`, and GitHub closes each PR whose branch goes.

## Proving a rebase of published work

- An appended commit moves forward. A rebase moves sideways. The proof is payload equality: `git range-diff` plus the same per-commit patch-ids.
- A rebase with conflicts cannot be equal. Prove an empty diff outside the resolved files, plus one line per resolved file: what each side gave and what you kept.
- State both endpoints of both ranges: old base and old tip, new base and new tip. Most errors are in the old tip. Publish the commit list or a breakdown (rebased, new, from the base branch), never a bare count.
- Before the rebase, push the old tip to a throwaway ref so a reviewer can rerun your `git range-diff`. If you did not, say in the report that the old-to-new commit mapping comes only from your local run.
- `jj restore --from <sha>` restores content, not identity: it builds a sibling, and the next push is sideways again.

## Messages

- Backticks inside a double-quoted `-m "..."` run as command substitution. The word goes missing and the error looks like noise. Single-quote, escape, or use a file. Read it back with `jj log -r @ --no-graph -T description` before you push.

## Divergent changes in a push chain

- A single-revision command on a divergent change ID exits 1 (`Change ID ... is divergent`) and moves nothing, but a `;` chain then pushes the old state. Gate with `&&`.
- Address each copy of a divergent change by its commit ID. A commit ID you recorded before the rewrite names the hidden old snapshot, so first find the survivor's current ID with `change_id(...)` or the bookmark. The divergence stays until you abandon the stale side, under the rules in [stacks and conflicts](stack-and-conflict-details.md).
- A bookmark on both sides shows `(conflicted)`, stops resolving, and blocks push. `jj bookmark set <name> -r <survivor commit-id>` fixes it.
- After any rebase, check the parent: `jj log -r <bookmark> --no-graph -T 'commit_id.short() ++ " parent=" ++ parents.map(|c| c.commit_id().short()).join(",")'`.
