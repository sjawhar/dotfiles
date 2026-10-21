# Observe CI before acting

Read for red, missing or misleading checks, incomplete API responses, or CI reproduction. The snapshot is the index. Raw job evidence decides what ran.

## A complete current-head population

- Name the head and the check under judgment. Read the latest attempt per check name. Zero results before the expected checks exist is not success.
- Every list has a scope and maybe a cap. Paginate, reconcile with a reported total, and say "unknown" when they differ.

| Source | Limit |
|---|---|
| `gh pr view --json files` | Short for large PRs. Use the paginated PR files endpoint, reconciled with `changed_files`. |
| compare endpoint | Files capped at 300, even paginated. Never use it for scope proof. |
| PR files, GraphQL files | 3,000 files. PR commits: 250. At a ceiling, diff locally. |
| REST lists | Often 30 rows. Reconcile check-runs with `total_count`. |
| `gh api --paginate --jq` | Runs per page. Use `--slurp` and aggregate once. |
| `runs/<id>/jobs?filter=all` | Mixes attempts. Use `runs/<id>/attempts/<n>/jobs?per_page=100`, or group by `run_attempt`. Say whether you counted jobs, legs or checks. |

- A commit's file list: `jj diff --name-only --from <parent-sha> --to <sha>`. A count shows how many. A set difference shows which.
- A failed API call can print an error object to stdout. Check the status and content before you count. Keep stderr. Use a known-content control through the same path.

## Read where the outcome lives

1. The job's conclusion and step list first. A setup failure can leave a collector's table green.
2. For a red job, compare each affected step with the base's run. A known red can hide a new failure.
3. Under `continue-on-error`, the REST step `conclusion` is post-processed. Read the outcome table and annotations. Skips after a failure are not completed checks.
4. A workflow that tolerates a nonzero exit by design: read its positive verdict artifact, and the emitter's code when unclear.
5. Check that the assertion-bearing cells ran. A green excluded lane is no evidence. Zero covered is a failure.

For tooling you own, construct a disputed state and run it, because a green history cannot show how skips and setup failures count. Report judged-good, judged-bad and never-judged apart.

## Diagnose, then decide on a retry

```sh
gh run view <run-id> -R <owner>/<repo> --log-failed
gh api repos/<owner>/<repo>/check-runs/<check-id>/annotations
gh api repos/<owner>/<repo>/actions/jobs/<job-id>/logs --allow-escape-sequences
gh run rerun <run-id> -R <owner>/<repo> --failed
```

- Take the run ID from the snapshot's failing-check URL. While a run is pending, annotations and a finished job's own log are readable.
- Classify by mechanism, not step name, error string or color. Before "all failures are X", read every not-X row, and account for every category in the terminal summary.
- Write the predicted failure signature, and what refutes it, before the next run. Keep setup, timeout and assertion failures apart. A crash is not a passing negative control.
- At the second defect of one origin, audit the whole driver ([proof design](proof-design.md)).
- Rerun only failed jobs, once, for a diagnosed external failure on a run that tested the current head. A green retry does not prove flakiness.

## Cached merges and missing runs

- Checks can run a cached test-merge. Read the checkout line and test-merge parents when a shared base (such as a migration) matters. A briefly stale test-merge is normal. Put no blanket currency block on every PR.
- A rerun can replay the cached merge with its old base and workflow. Compare the tested head and base with the intended ones before another retry. An identical failure is a reason to inspect that input, not proof of a stale base or flake. For failures in untouched files, reproduce at the exact head and a pristine base.
- A conflicted PR can get no `pull_request` run. Read mergeability before you blame webhooks or vanished SHAs. A mergeable head with zero runs: check trigger and path filters and commit-message skip directives. A rerun or close and reopen may not change the cached input. Any fix keeps published ancestry.
- Count the CI app's suites, not another integration's queued empty suite. Re-snapshot after UNKNOWN mergeability computes. Read the gate's code before you claim how it treats UNKNOWN.

## Reproduce the workflow literally

- Use the workflow's own invocation, cwd, interpreter and arguments. Include caller and census tests touched by additions, deletions, moved modules or shared-factory arguments. Search for a sibling entry or the old module name. For moved modules, collect every affected package and run each import-boundary and import-graph gate by name.
- Compare a failing file at head, pristine base and main before you regenerate a fixture. Dirty working-copy results are not main.
- Typecheck proof includes its summary (`0 errors, 0 warnings` or the equivalent JSON), not the exit status. Read the first diagnostics before you rerun an implausible count. Unresolved imports can mean the wrong interpreter. Silence at a heap ceiling can mean the wrong cwd or config and GC thrash. Silence is not progress. Use the CI command's heap and environment. Never weaken the check.
- After the push, run a full local lane only for a named question, such as a merge-with-main interaction.

## Shell and transport traps

- Never read a producer's exit status through a pager or filter pipeline. Run licensing checks bare or keep their own exit code, then read the artifact before you mutate.
- Scope shell options to a subshell. Never `export` environment pointers. Set them per command. Treat inherited venv and config pointers as untrusted, and identify what a run loaded.
- Single-quote jq programs. Fail loud on an empty parse. Compare expected labels with delivered labels. Call `gh` fresh each time so the token refreshes.
- Under App routing, `gh api user` can return 403 by design. Probe the repo endpoint. A write-rate 403 has its own reset time. Wait for it and serialize writes.
- A token without `read:org` can edit a body with `gh api -X PATCH repos/<owner>/<repo>/pulls/<N> -F body=@<file>`. Ownerless GraphQL mutations need command-local `GH_REPO=<owner>/<repo>`. Check every write with a separate GET.
