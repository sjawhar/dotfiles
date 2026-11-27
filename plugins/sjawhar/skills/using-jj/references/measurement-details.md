# Ancestry, diffs and absence

## Ancestry is a graph query

- Ask the graph: `jj log -r '<fix> & ::<pin>'` (empty means absent) or `git merge-base --is-ancestor <sha> <ref>`.
- These do not answer it: a PR title, a push time, a log window, GitHub commit search, an issue's status. An image's `org.opencontainers.image.revision` label answers it in one call. To find an issue's commit on main: `jj log -r '::main@origin & description(substring:"<KEY>")'`.

## Numbers from diffs

- For what your work changed, diff the commit against its parent: `jj diff -r <rev> --stat`. Its per-file figure is insertions plus deletions (`f | 4 +++-` is 3 added, 1 removed).
- `git diff trunk..feat` across a moved trunk counts trunk's work as yours. `git diff X...feat` diffs from the merge base of the two endpoints you name, which is right only when that merge base is the fork point you mean: two of your own commits across a base move can share a merge base deep in the stack.
- `jj diff` has no `--numstat`, and its diff algorithm is not git's: per-file rows can differ while totals agree. For a per-file split, or figures compared with GitHub's, use `gh api repos/<o>/<r>/pulls/<n>/files` or `git diff --numstat` at git's default algorithm (a configured `diff.algorithm` changes counts), and check each row.

## Claims about main

- A long-lived working copy is not main. Test main in its own directory: `git archive origin/main | tar -x -C "$(mktemp -d)"`.
- Extract into a new subdirectory, as `mktemp -d` makes, never into `/tmp` itself: a tool that searches upward for a project root (pytest's `rootdir`) then picks `/tmp` for every session on the machine. If the tooling needs a VCS marker, run `git init -q` inside first.
- If `jj git fetch` exits 0 but the ref did not move, compare `git ls-remote origin <branch>` with `jj log -r '<branch>@origin'`.

## Evidence of absence

- `jj file show -r <rev> <path>` prints zero bytes three ways: a glob that matches nothing (exit 0, silent); a literal path that matches nothing (paths are relative to the current directory; it errors, but `2>/dev/null`, `| grep` or `$(...)` hides the error); and an empty file.
- Count bytes (`wc -c`) before you call content absent. Outside the repository root, anchor paths with `root:`.
- Before you report that a search found nothing, run it on one item you know exists and the search can see: `gh search issues` excludes pull requests, so its control must be an issue. Confirm that item with an independent read, such as `git show origin/main:<path>`.
