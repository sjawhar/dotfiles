# Observe CI before acting

Read for red, missing or misleading checks, incomplete API responses, or reproducing CI locally. Find the checks from the `pr-snapshot` output; decide what ran from each job's own log and step list.

## A complete current-head population

- Name the head and the check under judgment, and read the latest attempt per check name. Zero results before the expected checks exist is not success.
- GitHub lists are capped. Paginate, reconcile with the reported total, and say "unknown" when they differ:
  - `gh pr view --json files` is short for large PRs: use the paginated PR files endpoint, reconciled with `changed_files`.
  - The compare endpoint caps files at 300, even paginated: do not use it for scope.
  - PR files stop at 3,000 and PR commits at 250: at a ceiling, diff locally (`jj diff --name-only --from <parent> --to <sha>`).
  - REST lists default to 30 rows: reconcile check-runs with `total_count`.
  - `gh api --paginate --jq` runs the filter per page: use `--slurp` and aggregate once with `jq`.
  - `runs/<id>/jobs?filter=all` mixes attempts: use `runs/<id>/attempts/<n>/jobs?per_page=100`, or group by `run_attempt`.
- A failed API call can print an error object to stdout. Check the status and content before you count; keep stderr.

## Read where the outcome lives

1. Read the job's conclusion and step list first. A setup failure can leave a collector's table green.
2. For a red job, compare each affected step with the base's run. A known red can hide a new failure.
3. Under `continue-on-error`, the REST step `conclusion` is post-processed: read the outcome table and annotations. Skips after a failure are not completed checks.
4. A workflow that tolerates a nonzero exit by design: read its positive verdict artifact, and the emitter's code when unclear.
5. Check that the assertion-bearing cells ran. A green excluded lane is no evidence; zero covered is a failure.

## Diagnose

```sh
gh run view <run-id> -R <owner>/<repo> --log-failed
gh api repos/<owner>/<repo>/check-runs/<check-id>/annotations
gh api repos/<owner>/<repo>/actions/jobs/<job-id>/logs --allow-escape-sequences
gh run rerun <run-id> -R <owner>/<repo> --failed
```

- Take the run ID from the snapshot's failing-check URL. Annotations and a finished job's log are readable while the run is still pending.
- Classify by mechanism, not step name, error string or color. Before "all failures are X", read every not-X row.
- Before the next run, write the predicted failure signature and what refutes it. Keep setup, timeout and assertion failures apart; a crash is not a passing negative control.

## Cached merges and missing runs

- Checks can run a cached test-merge. When a shared base (such as a migration) matters, read the checkout line and the test-merge parents. A briefly stale test-merge is normal.
- A rerun can replay the cached merge with its old base and workflow. Compare the tested head and base with the intended ones before another retry. For failures in untouched files, reproduce at the exact head and a pristine base.
- A conflicted PR can get no `pull_request` run: read mergeability before you blame webhooks. A mergeable head with zero runs: check trigger and path filters and commit-message skip directives. To get a run, add commits on top; do not rewrite published commits.
- Count the CI app's suites, not another integration's queued empty suite. Re-snapshot after UNKNOWN mergeability computes.

## Reproduce the workflow literally

- Before the push, run the fast checks and the affected suites, including callers' tests and tests that list or count members (registries, inventories) touched by an addition, deletion or move. When every render or import reads the changed config or helper, run the whole package once.
- Use the workflow's own invocation, cwd, interpreter, arguments, heap and environment. For moved modules, run each import-boundary and import-graph gate by name.
- Compare a failing file at head, pristine base and main before you regenerate a fixture. Dirty working-copy results are not main.
- Typecheck proof is its summary (`0 errors, 0 warnings` or the equivalent JSON), not the exit status. Unresolved imports can mean the wrong interpreter. A silent typecheck at its heap limit is often on the wrong cwd or config. Do not weaken the check.
- Do not edit a tree a long run is reading. After the push, run a full local lane only for a named question, such as a merge-with-main interaction.

## Shell and gh traps

- Run a check whose result permits a write (push, merge, delete) without a pipe, or keep its exit code (`${PIPESTATUS[0]}`), and read its output before the write.
- Set `GIT_DIR`, `VIRTUAL_ENV`, `KUBECONFIG` and similar per command, never `export`ed.
- Single-quote jq programs, fail on an empty parse, and check that every expected field name is present.
- With a GitHub App installation token, `gh api user` returns 403 by design: probe the repo endpoint instead. A write-rate 403 has its own reset time: wait for it and serialize writes.
- A token without `read:org` can edit a body with `gh api -X PATCH repos/<owner>/<repo>/pulls/<N> -F body=@<file>`. A GraphQL mutation with no repository argument needs `GH_REPO=<owner>/<repo>` on that command. Check every write with a separate GET.
