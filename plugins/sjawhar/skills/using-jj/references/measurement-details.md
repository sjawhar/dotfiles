# Diffs, ancestry and absence

## Numbers from diffs

- `--stat`'s per-file figure is insertions plus deletions (`f | 4 +++-` is 3 added, 1 removed). `jj diff` has no `--numstat`. For a per-file split use `git diff --numstat` on the exported refs.
- For "what did my work change", diff the commit against its own parent: `jj diff -r <rev> --stat`.
- `git diff trunk..feat` across a moved trunk counts undoing trunk's work as yours. `X...feat` uses the merge base of the two endpoints you name. It is right only when that merge base is the fork point you mean. Two of your own commits across a base move can share a merge base deep in the stack.
- jj's diff algorithm matches none of git's, and per-file rows can differ while totals agree. For figures compared with GitHub's `additions`/`deletions`, use `gh api repos/<o>/<r>/pulls/<n>/files` or `git diff --numstat` at git's default algorithm (a configured `diff.algorithm` changes counts). Never use `jj diff` for them. Check each row.

## Ancestry is a query

- Ask the graph: `jj log -r '<fix> & ::<pin>'` (empty means absent) or `git merge-base --is-ancestor <sha> <ref>`.
- These do not answer it: a PR title, push times, a length-limited log window, GitHub commit search, an issue's status. An image's `org.opencontainers.image.revision` label answers in one call. For issue keys use `jj log -r '::main@origin & description(substring:"<KEY>")'`.

## Stale refs

- If `jj git fetch` exits 0 but the remote ref did not move, compare `git ls-remote origin main` with `jj log -r main@origin`.
- If `jj workspace update-stale` selects a fresh empty commit while your edits looked unsnapshotted, find the sibling with `jj log -r 'change_id(<wc-change>)'`, inspect it, then `jj edit` it.

## Claims about main

- A long-lived working copy's files are not main's. Test main in its own directory:

  ```bash
  git archive origin/main | tar -x -C "$(mktemp -d)"
  ```

- Extract into a subdirectory, never `/tmp` itself: tools that search upward for a project root (pytest's `rootdir`) then pick `/tmp` for every session on the box. If the tooling needs a VCS marker, run `git init -q` inside first.

## Evidence of absence

- `jj file show -r <rev> <path>` returns zero bytes three ways: a literal path that matches nothing (paths are cwd-relative. It errors, but `$(...)`, `| grep` or `2>/dev/null` hide it), a glob that matches nothing (exit 0, silent), and an empty file.
- Count bytes (`wc -c`) before you query the output. Anchor paths with `root:` outside the repository root.
- Before you report that a search found nothing, run it on one item you know exists and that the search can see: `gh search issues` excludes pull requests, so its control must be an issue. A control that shares the test's blind spot proves nothing. Confirm the item with an independent read such as `git show origin/main:<path>`.
