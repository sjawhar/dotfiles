# Observe CI before acting

Read for red checks, missing checks, incomplete API responses or CI reproduction.
The snapshot is the index; raw job evidence decides what actually ran.

## Establish a complete current-head population

Name the PR head and the check/run being judged. Read the **latest attempt per
check name**, not every historical failure. Do not infer success from a filter
that returns zero while no expected checks exist yet.

Every list has a scope and possibly a cap. Paginate, reconcile against a separate
reported total, and state unknown when they differ. Useful endpoint distinctions:

- `gh pr view --json files` is not a complete file-list instrument for large PRs.
  Use the paginated PR files endpoint and reconcile with `changed_files`.
- The compare endpoint's file list is capped at 300; pagination does not turn
  that single object into a complete list. Do not use it for scope proof.
- PR files and GraphQL files have a 3,000-file ceiling; PR commits have a
  250-commit ceiling. At/over a ceiling, use an uncapped local revision diff.
- REST lists commonly default to 30 rows. Reconcile check-runs against
  `total_count`; a missing check on page one is not absent.
- `gh api --paginate --jq` applies the query per page. An aggregate can print
  several plausible numbers. Use `--slurp` and aggregate the pages deliberately;
  reconcile the result. Multiple verdict lines are not one aggregate verdict.
- `runs/<id>/jobs?filter=all` mixes attempts. Use
  `runs/<id>/attempts/<n>/jobs?per_page=100` with pagination, or group by
  `run_attempt` before counting. State whether you counted jobs, legs or checks.

For a commit-bound file list, use concrete endpoints:
`jj diff --name-only --from <parent-sha> --to <sha>`. Completeness and membership
are different questions: a count proves how many, a set difference proves which.

Check the command's own status and content before accepting a list. A failed
API call can put an error object on stdout; counting its lines produces a number
with no relation to the requested population. Keep stderr. Use a known-content
control through the same access path, not another count of the same error body.

## Read at the level that carries the outcome

1. Read the job's own conclusion and step list first. A setup failure can leave
   a later collector's table all green/skipped while the job is rightly red.
2. For a red job, inspect every affected step against the base's run of that
   job. A known-red job can hide a new typecheck or test failure underneath it.
3. Under `continue-on-error`, step `conclusion=success` is post-processed. Read
   the emitted outcome table and annotations; the REST steps API does not expose
   the original outcome field. Skips after a failure are not completed checks.
4. If a workflow tolerates a nonzero exit by design, inspect its positive
   verdict/status artifact. An error annotation alone may not decide the review,
   just as a green collector alone may not decide the job. Read the emitter's
   code when its vocabulary, location or conditions are uncertain.
5. Confirm the exact assertion-bearing cells executed. A green excluded lane
   is no evidence; zero scenarios or files covered is not a passing proof.

A sample that cannot distinguish competing interpretations cannot establish the
rule. For tooling you own, construct the disputed state and run it: all-green
history alone cannot tell how the collector treats skipped checks or setup
failure. Report judged-good, judged-bad and never-judged separately.

## Diagnose a failure, then decide whether to retry

Read the failing run's logs, for example:

```sh
gh run view <run-id> -R <owner>/<repo> --log-failed
```

The run ID comes from the snapshot's failing-check URL. While the run is pending,
full-run log reads may refuse. Annotations are available immediately, and a
finished job's own log can be read even while sibling jobs run:

```sh
gh api repos/<owner>/<repo>/check-runs/<check-id>/annotations
gh api repos/<owner>/<repo>/actions/jobs/<job-id>/logs \
  --allow-escape-sequences
```

Classify by the mechanism, not the step name, error string or chain color. A
repeated signature need not have the same cause. Before claiming “all failures
are X”, filter failure/error rows for **not-X**, read every residual, and account
for all categories in the terminal summary.

Write the predicted failure signature before the next run, including what would
refute it. Distinguish setup, timeout and actual assertion failures. A crash is
not a successful negative control. At the second defect of the same origin,
audit the whole driver and its contracts rather than fixing one more instance.

A suspected regression is reproduced locally and fixed. A diagnosed external,
pre-diff failure gets one fresh **failed-jobs-only** attempt, and only if the run
validates the current head:

```sh
gh run rerun <run-id> -R <owner>/<repo> --failed
```

No bare rerun of the full graph. If the detector reports a recurring failure or
head churn, name the invariant the next repair will restore or return
`needs-human`. A green retry does not retroactively prove flakiness.

## Cached merge refs and missing runs

Checks may execute the event's cached test-merge, not the PR head alone or the
merge that current main would create. Read the checkout line and test-merge
parents when a changed shared base, such as a migration, matters. A briefly stale
test-merge is normal; do not impose a blanket currency block on every PR.

A rerun can replay the same cached merge, including its old base and workflow.
It does not prove a later base fix was included. Before another retry, compare
actual tested head/base with the intended ones. An identical failure is a reason
to inspect that input, not proof by itself of a stale base or external flake.
Untouched files failing can reflect base drift or an interaction; reproduce at
the exact head and pristine base before attributing it.

A conflicted PR can receive no `pull_request` dispatch. Read its mergeability
before blaming webhooks or vanished SHAs. A mergeable head with zero runs needs
its trigger/path filters and commit-message skip directives checked. Re-running
or closing/reopening does not necessarily change the cached input. Any required
repair must preserve published ancestry; use the history reference, not a
published rebase or message amendment.

Count the relevant CI app's suites, not every integration's permanently queued
empty suite. UNKNOWN mergeability triggers GitHub's computation; re-snapshot
once it has computed rather than classifying the first UNKNOWN as a conflict.
Read the gate's implementation before claiming how it handles that state.

## Reproduce the workflow, not a paraphrase

Use the workflow's literal invocation, package cwd, interpreter and arguments.
The prose command can be stale. Include caller tests and census tests affected
by additions, deletions, moved modules or shared-factory arguments. Search for a
sibling entry or the old module name to find tests a diff-only selector misses.
For moved modules, collect every affected package and run distinct import-boundary
and import-graph gates by name; similarly named gates are not interchangeable.

When a config key/helper is read by every render or import, run that package's
whole suite once before pushing. After a required base integration, cover your
own earlier fixes too. Compare a failing file at head, pristine base and current
main before regenerating a fixture. Dirty working-copy results are not main.

Typecheck proof includes its summary (`0 errors, 0 warnings`, or equivalent JSON),
not exit status alone. A threaded worker can crash with no summary. Inspect first
diagnostics before rerunning an implausibly large count: unresolved imports can
mean the wrong interpreter; silence at a heap ceiling can mean wrong cwd/config
and GC thrash. Follow the CI command's heap and environment settings rather than
inventing a different check, weakening it or treating silence as progress.

Preserve full logs for expensive runs. Use supervised processes in an unchanged
workspace, not one bounded call. After push, leave the full lanes to CI unless a
named question needs a local run, such as a merge-with-main interaction. Changed
end-to-end paths still require step 4's proof at the new head.

## Shell and transport traps

- Never read the producer's exit status through a pager/filter pipeline. With
  pipefail a closed reader can kill an otherwise useful chain; without it a
  failed test can inherit the reader's zero status. Run licensing checks bare
  or preserve their own code, then read the artifact before mutation.
- Scope shell options to a subshell. Never `export` persistent environment
  pointers; set them per command. Treat inherited venv/config pointers as
  untrusted and identify what the run actually loaded.
- Quote jq/jaq programs with single quotes or read them from a file. Make empty
  or failed parses loud, and compare expected labels against delivered labels
  so partial output does not masquerade as complete.
- Let each `gh` call obtain a fresh routed token; do not capture one in a loop.
  Under App routing, `gh api user` can return 403 by design. Probe the repository
  endpoint instead. A write-rate 403 needs its own headers/reset time, not an
  inference from a full core-rate bucket. Resume at the reset and serialize
  writes; do not hammer the endpoint.
- For an upstream token lacking `read:org`, body editing can use
  `gh api -X PATCH repos/<owner>/<repo>/pulls/<N> -F body=@<file>`.
  Ownerless GraphQL mutations need command-local `GH_REPO=<owner>/<repo>` so the
  router selects the intended identity. Confirm every write with a separate GET.
