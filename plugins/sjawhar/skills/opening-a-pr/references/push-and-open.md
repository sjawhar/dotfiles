# Pushing and opening: checks and mechanics

Read this from step 11 of `opening-a-pr` before the first push of a PR, before any push after a
red CI run, and before `gh pr create`. It holds the exact commands behind the step's rules.

## Which checks, scoped how

The set is CI's own lane map for the changed paths: read the workflow that gates those paths and
run its fast steps (lint, type, package-local unit lanes, and browser/e2e suites when the diff
touches a frontend), not a subset chosen for speed. A change can pass every unit, type and review
lane locally and still go red only in the browser lane CI runs for those paths.

"Scoped to the diff" means the packages the diff changes AND every test file that imports a
module the diff changed, not the files the diff edited:

```bash
grep -rl '<module names>' tests | xargs pytest -q
```

A behavior-tightening change breaks exactly the tests that encoded the looser behavior, and those
live elsewhere. A lane in CI's *skipped* set is not a coverage gap on its own: it skips because it
is irrelevant to the paths, environment-gated, or broken, and only the third is a gap; read which
lanes do cover the tree.

A red head costs a CI cycle plus a fresh reviewer wave on code you are about to replace. Cite the
green output lines (command and result) in the push's report.

## Run them outside your agent shell's environment

An omp session exports variables a clean runner has none of (`SECRETSD_SESSION_TOKEN_FILE`,
`XDG_RUNTIME_DIR` pointing at live sockets, gh-app routing), and a test that spawns a real binary
hands them to it unless it sets its own. Run the suite once with only the toolchain's own paths:

```bash
env -i HOME="$HOME" USER="$USER" PATH="$PATH" \
  CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}" RUSTUP_HOME="${RUSTUP_HOME:-$HOME/.rustup}" \
  <command>
```

When CI goes red, re-run the whole suite that way with fail-fast off (`--no-fail-fast`) before
pushing a fix: a fail-fast runner cancels everything after its first failure, so the one failure
CI names may not be the only one. A test that never sets a variable it depends on, such as
`SECRETSD_SESSION_TOKEN_FILE`, passes in every session that exports it and fails on the runner;
`env -u <VARIABLE> <command>` reproduces that failure locally.

## `env -i` does not remove the instance role

A devbox shell reaches the EC2 metadata service, so any AWS SDK in a spawned binary signs in as
`<project>-devbox-admin-instance-role` with no variable in sight; a runner has no such role. For
anything that touches AWS or Bedrock, add `AWS_EC2_METADATA_DISABLED=true` to the `env -i` run.
Check: in an agent shell with no `AWS_*` variables, `aws sts get-caller-identity` returns that role;
with `AWS_EC2_METADATA_DISABLED=true` it finds no credentials.

## Labels at creation

A workflow that runs on `labeled` cannot filter the trigger by label name, so a label passed at
creation fires `opened` and `labeled` in the same second: two identical runs on one head, one
cancelled a minute in. Where the repository's own workflow applies classifier labels, a label at
creation duplicates that work. A label a lane genuinely needs restarts the whole graph too, so add
it after the current head's checks have settled.

## `gh pr create` from a jj workspace

The push rule moves `@` off the pushed commit in the same command (`jj new`), which leaves the
colocated HEAD detached, and `gh pr create` without `--head` then cannot infer a branch: it exits
without a URL and prints a jj hint instead, and a `| tail -1` shows only that hint. Pass `--head`
always and read the URL from the API afterwards (`gh pr list --head <name>`).
