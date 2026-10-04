# Diffs, ancestry and absence evidence

Read from the matching step of the using-jj skill. The examples include read-only diagnostics
and isolated scratch-repository setup; they do not change the main skill's jj-only rule.

- **Numbers from diffs:** `--stat`'s per-file figure is insertions **plus** deletions (`f | 4 +++-`
  for 3 added, 1 removed), not a split. `jj diff` has **no `--numstat`** (`unexpected argument`);
  its summary line carries only the totals across all files, so a per-file insertions/deletions
  split needs `git diff --numstat` on the exported refs. For "what did my work change" use the
  commit against its own parent (`jj diff -r <rev> --stat`), which has no merge base to get wrong.
  Across a moved trunk, `git diff trunk..feat` (two-dot) counts undoing trunk's own work as yours.
  Three-dot (`X...feat`) diffs against the **merge base of the two endpoints you name**, and is
  right exactly when that merge base is the fork point your question means; the branch's topology
  is not the discriminator. On a stack trunk → A (3 files) → feat (1 file) after trunk moved:
  `A...feat` shows 1 file (merge base is A's head, right); `trunk...feat` shows 4 (merge base is
  A's fork point, so all of A is counted); `trunk..feat` shows 5. Two of your own commits across a
  base move can share a merge base deep in the stack.
  **jj's built-in diff is its own line decomposition, not git's.** Where git's four algorithms
  disagree, jj matched none of them in 3 of 8 synthetic cases (jj 5/4, myers 4/3, histogram 8/7),
  and on a real PR jj's per-file rows differed from git myers, GitHub's algorithm, while the totals
  coincided exactly, so a matching total proves nothing. Derive a figure that will be compared with
  GitHub's `additions`/`deletions` from the same source as that oracle: `gh api
  repos/<o>/<r>/pulls/<n>/files`, which returns the oracle's own numbers, or `git diff --numstat`
  at git's default algorithm (a `diff.algorithm` in config silently changes the counts). Never use
  `jj diff` for it, and cross-check per row.

- **Read a jj error whole; clap puts the diagnosis on the FIRST line.** `| tail -2` can cut the
  line that says `error: unexpected argument '--allow-new' found` and leave only `For more
  information, try --help`, which reads like a push refusal. In this fleet's jj (0.45.1)
  `--allow-new` is not a `jj git push` flag; an existing local bookmark pushes for the first time
  with `jj git push --bookmark <name>` (`-b`) without it. Never `| tail -N` an error you have not
  read in full, and never `2>/dev/null` a command whose output you are about to test for emptiness:
  the suppressed stderr can hold the diagnosis and name the flag that fixes it, as when a log read
  rejects terminal escape sequences.

- **An ancestry claim is a query, never a reading.** "Does this pin carry that fix", "does this
  image contain my merge" and "is the pushed head behind my work" are answered by asking the graph
  the exact question, `jj log -r '<fix> & ::<pin>'` (empty means absent) or
  `git merge-base --is-ancestor <sha> <ref>`. These readings do not answer it:
  - a PR title, which can imply fixes the bump does not carry;
  - push times, when an image's own `org.opencontainers.image.revision` label answers in one call;
  - a length-limited log window, which may never reach the pushed base;
  - GitHub's commit search, which can return nothing for issue keys whose commits are on main,
    where `jj log -r '::main@origin & description(substring:"<KEY>")'` finds them;
  - an issue's status, which can stay in progress after its PR merged and deployed.

- **A successful fetch can still leave the needed ref stale.** When `jj git fetch` exits 0 but an expected remote ref did not move, compare the read-only `git ls-remote origin main` result with `jj log -r main@origin`; this is a diagnostic exception to the no-Git-mutations rule. In a shared-store workspace, if `jj workspace update-stale` selects a fresh empty commit while edits appeared unsnapshotted, recover deliberately: use `jj log -r 'change_id(<wc-change>)'` to find the divergent sibling that holds the snapshot, inspect it, then `jj edit <recovered-change>`. Both checks apply only to these ambiguous states.

- **"Is main red?" is answered from a pristine extraction, never from the shared checkout.** A
  long-lived working copy's files are not main's, whatever `jj log` says about its parent: tests
  run "against main" there can pass while the same tests fail on main, and implicate the wrong
  change. Extract main into its own directory:

  ```bash
  git archive origin/main | tar -x -C "$(mktemp -d)"
  ```

  Use it for any claim about what main does. The `mktemp -d` is load-bearing: extract into a
  subdirectory, never `/tmp` itself, because a checkout-shaped `/tmp` root can make tools that
  infer a project root upward (pytest's `rootdir`, for one) pick `/tmp` for every other session
  on the box. If the project's tooling recognizes a checkout only by a VCS marker, run
  `git init -q` inside the extraction before anything that resolves project content.
