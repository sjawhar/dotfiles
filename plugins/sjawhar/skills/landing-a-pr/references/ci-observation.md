# Observe CI before acting

Read for red, missing or misleading checks, incomplete API responses, choosing the tests to run before a push, or reproducing CI locally. Find the checks from the `pr-snapshot` output; decide what ran from each job's own log and step list.

## A complete current-head population

- Name the head and the check under judgment, and read the latest attempt per check name. Zero results before the expected checks exist is not success.
- GitHub lists are capped. Paginate, reconcile with the reported total, and say "unknown" when they differ:
  - `gh pr view --json files` returns at most 100 files: when the PR's `changed_files` is over 100, use the paginated PR files endpoint (`gh api --paginate repos/<owner>/<repo>/pulls/<N>/files`), reconciled with `changed_files`.
  - The compare endpoint caps files at 300, even paginated: do not use it for scope.
  - PR files stop at 3,000 and PR commits at 250: at a ceiling, diff locally.
  - REST lists default to 30 rows: reconcile check-runs with `total_count`.
  - `gh api --paginate --jq` runs the filter per page: use `--slurp` and aggregate once with `jq`.
  - `runs/<id>/jobs?filter=all` mixes attempts: use `runs/<id>/attempts/<n>/jobs?per_page=100`, or group by `run_attempt`. Say whether you counted jobs, legs or checks.
- A commit's file list: `jj diff --name-only --from <parent-sha> --to <sha>`. To show which files differ between two lists, compare the sets (`comm -3 <(sort a) <(sort b)`), not their counts: equal counts can hide different members.
- A failed API call can print an error object to stdout. Check the status and content before you count, keep stderr, and run a known-content control through the same path.

## Read where the outcome lives

1. Read the job's conclusion and step list first. A setup failure can leave a collector's table green.
2. For a red job, compare each affected step with the base's run. A known red can hide a new failure.
3. Under `continue-on-error`, the REST step `conclusion` is post-processed: read the outcome table and annotations. Skips after a failure are not completed checks.
4. A workflow that tolerates a nonzero exit by design: read its positive verdict artifact, and the emitter's code when unclear.
5. Check that the assertion-bearing cells ran. A green excluded lane is no evidence; zero covered is a failure.

For tooling you own, build a run in the state in question (a skipped step, a setup failure) and check how the tool counts it: a green history cannot show how skips and setup failures count. Report judged-good, judged-bad and never-judged apart.

## Diagnose

```sh
gh run view <run-id> -R <owner>/<repo> --log-failed
gh api repos/<owner>/<repo>/check-runs/<check-id>/annotations
gh api repos/<owner>/<repo>/actions/jobs/<job-id>/logs --allow-escape-sequences
gh run rerun <run-id> -R <owner>/<repo> --failed
```

- Take the run ID from the snapshot's failing-check URL. Annotations and a finished job's log are readable while the run is still pending.
- A job's log, `gh api repos/<owner>/<repo>/actions/jobs/<id>/logs`, prints nothing unless you pass `--allow-escape-sequences`.
- Classify by mechanism, not step name, error string or color. Before "all failures are X", read every not-X row.
- Before the next run, write the predicted failure signature and what refutes it. Keep setup, timeout and assertion failures apart; a crash is not a passing negative control.

## Cached merges and missing runs

- Checks can run a cached test-merge. When the PR's result depends on what the base holds (a migration chain, or a file both the PR and the base change), read the checkout line and the test-merge parents. A test-merge on an older base is normal, so do not hold every PR until its test-merge uses the latest base; act only when the base commits it lacks touch those files.
- A rerun can replay the cached merge with its old base and workflow. Compare the tested head and base with the intended ones before another retry. An identical failure is a reason to inspect that input, not proof of a stale base or a flake. For failures in untouched files, reproduce at the exact head and a pristine base.
- A conflicted PR can get no `pull_request` run: read mergeability before you blame webhooks. A mergeable head with zero runs: check trigger and path filters and commit-message skip directives. To get a run, add commits on top; do not rewrite published commits.
- Count the CI app's suites, not another integration's queued empty suite. Re-snapshot after UNKNOWN mergeability computes. Read the gate's code before you claim how it treats UNKNOWN.

## Reproduce the workflow literally

- Before the push, run the fast checks and the affected suites. The fast checks are the lint, format and typecheck commands the CI workflow runs. The affected suites are the tests of each changed file, callers' tests, and tests that list or count members (registries, inventories) touched by an addition, deletion, move or shared-factory argument; search for a sibling entry and the old module name to find them. When every render or import reads the changed config or helper, run the whole package once.
- Use the workflow's own invocation, cwd, interpreter, arguments, heap and environment. For moved modules, collect every affected package and run each import-boundary and import-graph gate by name.
- Compare a failing file at head, pristine base and main before you regenerate a fixture. Dirty working-copy results are not main.
- Typecheck proof is its summary (`0 errors, 0 warnings` or the equivalent JSON), not the exit status. Read the first diagnostics before you rerun an implausible count. Unresolved imports can mean the wrong interpreter. A typecheck that prints nothing at its heap limit is often on the wrong cwd or config: check both before you wait longer, because silence is not progress. Do not weaken the check.
- Do not edit a tree a long run is reading. After the push, run a full local lane only for a named question, such as a merge-with-main interaction.

## Shell and gh traps

- Do not read a producer's exit status through a pager or filter pipeline. Run a check whose result permits a write (push, merge, delete) without a pipe, or keep its exit code (`${PIPESTATUS[0]}`), and read its output before the write.
- Scope shell options to a subshell. Set `GIT_DIR`, `VIRTUAL_ENV`, `KUBECONFIG` and similar per command, never `export`ed. Treat inherited venv and config pointers as untrusted, and identify what a run loaded.
- Single-quote jq programs or put them in a file, fail on an empty parse, and check that every expected field name is present. Keep stderr out of compared values.
- With a GitHub App installation token, `gh api user` returns 403 by design: probe the repo endpoint instead. A write-rate 403 has its own reset time: wait for it and serialize writes.
- A token without `read:org` can edit a body with `gh api -X PATCH repos/<owner>/<repo>/pulls/<N> -F body=@<file>`. A GraphQL mutation with no repository argument needs `GH_REPO=<owner>/<repo>` on that command. Check every write with a separate GET.
