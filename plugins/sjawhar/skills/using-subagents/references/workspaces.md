# Workspaces, clones and path traps

## Paths

- Rule 1 in `SKILL.md` has how relative paths and edit headers resolve. Do not dispatch from a cwd you do not own, since your subagents' relative paths land there; if your own checkout holds work in progress, brief absolute paths (rule 1) or dispatch from a scratch directory.
- An edit that fails with a snapshot mismatch that looks like a stale read has resolved to a different copy in the cwd. Do not retry by the relative path: switch to the absolute one.
- Every output path leaks the same way: a `curl -o`, a `>` redirect or a screenshot path is absolute or under the lane's own `mktemp -d`.
- After a tool call that errored strangely or returned mangled output, `pwd` and `ls -la` the file you believe you wrote.

## After subagents return

1. Run `git -C <dir> --no-optional-locks status --porcelain` on the checkout your session started in. A stray file there is a subagent's.
2. Run `git -C <dir> status --porcelain --ignored=matching` as the control. It must print at least one line; if it prints nothing, step 1 did not read the tree you think. Report both line counts.
3. Do not run `jj status` in a shared jj checkout: it snapshots another session's working copy. To audit another session's checkout without touching it, run `git -C <repo> status --porcelain`, which creates no commit.
4. If a worker's clone holds an empty commit after it reported edits, its writes went to the default checkout. Look there before you redo them.

Put three questions in every lane's output contract: any write outside its own workspace, even one it reverted; how it did the work; what it did not run or check. When a lane reports such a write, check the named checkout yourself. When your own report turns out wrong, say how you did the work.

## Writing a brief or long prose through a shell

- Write a pull request body, a comment or a brief to a file with the write tool or a quoted heredoc (`<<'EOF'`), then pass `--body-file`. An unquoted `<<EOF` expands `$…`, `$(…)` and backticks and still writes a plausible file.
- Build a brief that carries code by concatenation or a quoted heredoc, never an f-string or a template literal.
- `wc -c` the brief file and read its first and last lines before you dispatch against it: a brief that failed to write dispatches a subagent with no instructions, which looks like a bad agent.

## `isolated: true`

An isolated task's patch is applied to the head your checkout sits on. Commit or stash your own changes first, so your working copy (jj: `@`) is empty, and read `git status --short | wc -l` before the next command. Read the result's `Applied patches:` line.

## Sizing a lane's copy of the tree

- A read-only lane gets no clone: `gh api` contents at a ref, or `git show <rev>:<path>`.
- A lane that mutates or runs tests gets one scratch clone of the paths under test, not of the whole repository. The coordinator removes it by literal path when the lane ends. For a change that runs no tests, update an idle clone (`git fetch && git merge origin/main`) instead.
- A lane that runs `uv sync` puts its venv on the uv cache's filesystem (`env UV_PROJECT_ENVIRONMENT=<path on that filesystem> uv sync`): hard links cannot cross filesystems, so elsewhere every wheel is copied.
- A re-review reuses the existing copy (fetch, check out the new head) or removes it before taking a new one.
