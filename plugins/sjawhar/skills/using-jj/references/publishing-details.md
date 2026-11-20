# Publishing

## Pushes that publish nothing

- A bare `jj git push` skips a bookmark the remote has never seen: it warns `Refusing to create new remote bookmark`, prints `Nothing changed.` and exits 0. The usual later symptom is `gh pr create` saying `No commits between <base> and <branch>`: re-read the push's first line before you touch the branch.
- `--named <name>=<rev>` fails with `Bookmark already exists` when the bookmark exists locally. Push that one with `--bookmark <name>`.
- `--allow-new` does not exist, and clap's suggestion, `--all`, pushes every local bookmark, mostly other sessions'. Use `--named`.
- `--named ... --dry-run` creates no bookmark, so the real push still needs `--named`.
- Read an error's first line, where the diagnosis is: `| tail` or `2>/dev/null` hides it. Never add `2>/dev/null` to a command whose output you test for emptiness.

## Check what you pushed

- Compare `jj log -r '<name>@origin' --no-graph -T commit_id` with the ID you recorded, not with whatever `@-` names later: `jj rebase -r @-` reparents `@`, so `@-` changes. `Revision ... doesn't exist` means nothing was pushed.
- `<name>@origin` is the remote as of your last fetch or push. Before a decision or a report that rests on the remote's state, fetch or read `git ls-remote origin <name>`; only the read-back right after your own push may use it unfetched.
- A newer base after a fetch does not call for a rebase unless the repository's docs require branches to be current.

## Delete one remote bookmark

- `jj bookmark delete <name>`, then `jj git push --bookmark <name>`.
- `jj abandon` of a bookmarked commit deletes the bookmark too, so the next `-b <name>` push is `[delete from <sha>]`, and GitHub closes the PR. Read the dry-run's direction before every push that follows an abandon.
- Never use `jj git push --deleted`: it pushes every pending deletion in the repository, and GitHub closes each PR whose branch goes.

## Rebase of published work

- An appended commit moves forward; a rebase moves sideways. The proof that a rebase kept the payload is `git range-diff <old-base>..<old-tip> <new-base>..<new-tip>`, with the same patch-id per commit.
- Name all four endpoints; most errors are in the old tip. Publish the commit list or a breakdown (rebased, new, from the base branch), never a bare count. Before the rebase, push the old tip to a throwaway ref so a reviewer can rerun your range-diff, or say that the mapping comes only from your local run.
- A rebase with conflicts cannot be equal. Show an empty diff outside the resolved files, and for each resolved file what each side gave and what you kept.
- `jj restore --from <sha>` copies content, not identity: the result is a sibling, and the next push is sideways again.

## Messages

- Backticks inside a double-quoted `-m "..."` run as command substitution, and the word disappears. Single-quote the message or use `jj describe --stdin`, then read it back with `jj log -r @ --no-graph -T description`.

## Divergent change in a push chain

- A single-revision command on a divergent change ID exits 1 (`Change ID ... is divergent`) and moves nothing; a `;` chain then pushes the old state. Gate with `&&`.
- Address each copy by its current commit ID from `jj log -r 'change_id(<id>)'`. An ID you recorded before the rewrite names the hidden old snapshot.
- A bookmark on both copies shows `(conflicted)`, stops resolving and blocks the push: `jj bookmark set <name> -r <survivor commit ID>`.
- After any rebase, check the bookmark's parent: `jj log -r <bookmark> --no-graph -T 'commit_id.short() ++ " parent=" ++ parents.map(|c| c.commit_id().short()).join(",")'`.
