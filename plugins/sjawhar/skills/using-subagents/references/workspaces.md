# Workspaces, clones and the path traps

## Why absolute paths are the mechanism, not style

A subagent's relative `edit`/`read`/`write` paths resolve against the parent's cwd — even when the subagent holds its own clone path, and even after its own `bash cd`. A subagent told to work in `../.worktrees/x` that edits `meta/foo.py` edits the coordinator's live checkout. The bash tool starts every call at the session cwd (checked on omp 18.12.2), so a `cd` never carries into the next call, and this bites a top-level session working in its own checkout as much as a subagent.

So: give bash its `cwd` on every call, pass `edit`/`write` absolute paths, and write in every brief that may edit, in these words, "absolute paths only, rooted in `<workspace>`", with the path spelled out. A coordinator does not dispatch from a cwd it does not own, because its subagents' relative paths land there. If your own checkout holds work in progress, either brief absolute paths as above or dispatch from a scratch directory. Audit another session's checkout without touching it with `git -C <repo> status --porcelain`, which mints no commit.

## The `edit`-header trap

An absolute `read` does not protect a relative `edit` header. The `[path#TAG]` header re-resolves against the session cwd, and the tag binds to content, not to the file that was read:

- when the cwd copy has identical content, the tag validates with no stale-tag warning and the wrong file is written silently;
- when the cwd copy differs, the edit fails with a snapshot-hash mismatch, which reads like a stale read.

Re-reading by the relative path re-reads the wrong file, so do not retry — switch to the absolute workspace path. After any tool call that errored strangely or returned mangled output, `pwd` and `ls -la` the file you believe you just wrote before the next call.

## Reading the checkout back after subagents return

After the subagents return, run `git -C <dir> --no-optional-locks status --porcelain` on the checkout your session started in; a stray file there is a subagent's. Also run `git -C <dir> status --porcelain --ignored=matching`, which must print at least one line, and report both line counts: if that one also prints nothing, the first command did not run on the tree you think. Do not run `jj status` in a shared jj checkout: it snapshots someone else's working copy. The mirror symptom is an empty commit in the worker's own clone after it reported edits — the writes went to the default checkout, so the work is misplaced, not lost. Look there before redoing it.

Put three questions in every lane's output contract: any write outside its own workspace, even one it reverted; how it actually did the work; and what it did not run or check. No verdict or gate shows those, because the leak is in a checkout the PR never touches, and the not-run list is the only evidence that points at ground nobody examined. Check the named checkout yourself when a lane reports one, and volunteer how you did the work when your own report turns out wrong.

The leak covers every output path, not only edits: `curl -o`, a `>` redirect or a screenshot path in a lane that inherited your cwd writes into that checkout, so a brief makes every output path absolute or puts it under the lane's own `mktemp -d`.

## Never pass long prose inline to a shell

An unquoted heredoc delimiter (`<<EOF`) silently corrupts long prose: `$100` became `00`, `$HOME` leaked an absolute path, `$(…)` errored and deleted itself from the text, backticks ran as commands — and the file is still written, non-empty and plausible. `<<'EOF'` is byte-exact. Write a PR body, issue comment or any long prose to a file (quoted delimiter, or the write tool) and pass `--body-file`. Build a brief that carries code the same way — by concatenation or a quoted heredoc, never an f-string or template literal — because braces and backticks are what such a brief is full of.

## `isolated: true`

An isolated task's patch is applied to whatever head your checkout is sitting on, so commit or stash first so your working copy (jj: `@`) has no changes, and read `git status --short | wc -l` before the next command. The result line says `Applied patches: yes/no` — read it.

## Sizing a lane's copy of the tree

A read-only lane gets no workspace and no clone: `gh api` contents at a ref, or `git show <rev>:<path>` from the existing checkout, read everything it needs.

A lane that mutates or runs tests gets one scratch clone of the paths under test rather than the whole repository, and the coordinator removes it by literal path when the lane ends. A clone of a 62k-file tree costs 4–9 GB; for a change that runs no tests, update an existing idle clone (`git fetch && git merge origin/main`) instead of taking a new one. Once such a copy runs `uv sync`, a monorepo clone in a box's `/tmp` reaches 9–13 GB, because uv's cache hard links cannot cross onto that filesystem and every wheel is copied. A lane that runs tests puts its venv on the uv cache's filesystem, per command: `env UV_PROJECT_ENVIRONMENT=<path on that filesystem> uv sync`. A re-review reuses the existing copy (fetch, check out the new head) or removes it before taking a new one.
