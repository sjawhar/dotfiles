---
name: developing-ci
description: "Use before any CI edit or re-run, even small: job, matrix, runner, cache, concurrency, CI cost. Also when CI is slow or expensive (\"why is CI expensive\"), for workflow triggers and cancel-in-progress, in any CI system, not only GitHub Actions."
---

# Developing CI

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

CI minutes are money: dollars = billed minutes x the runner's price per minute. Most waste is a whole suite that runs on every push, on a runner too large for it, more often than a merge needs it. Documentation-only runs and runs of replaced heads are usually a few percent.

## 1. Before you add or change a job: estimate it

- Runs per day = events that start it per day (pull-request pushes, merges, pushes to main, schedules) x matrix size. Count each trigger in `on:`, because one extra trigger can double the count.
- Cost per run = billed minutes x price per minute of its runner. GitHub rounds each job up to a whole minute, so ten 20-second jobs bill ten minutes: merge tiny jobs into one.
- Read prices from GitHub's current page (https://docs.github.com/en/billing/reference/actions-runner-pricing), never from memory or this skill, because they change. Standard runners on a public repository are free; larger runners are billed there too.
- Write runs per day and dollars per day in the pull request description. A reviewer cannot weigh a cost nobody stated.

## 2. Run a job only when its inputs changed

- Route: a first job reads the changed paths (pull request: base to head; merge queue: the group's base to head) and sets one output per job. Each other job has `needs: <router>` and `if: needs.<router>.outputs.<job> == 'true'`. A documentation or unrelated change then starts nothing heavy.
- Make one final job the required check: `if: always()`, `needs:` every job, and it fails unless each job the router selected succeeded. A workflow that a `paths:` filter skips never reports, so its required check waits forever; and a skipped job counts as passing, so the final job must judge the results itself.
- List in each job's paths every file it reads: its code, the code it imports, lockfiles, its workflow and scripts. A list that is too narrow merges a change that nothing tested.
- Trigger pull-request runs on `opened`, `synchronize` and `reopened` only. `edited`, `labeled` and comments re-test a head that is already tested. Draft pull requests run nothing heavy.

## 3. Run the full suite less often

- On a pull-request push run a smoke set and one version of each matrix; run the full suite and the full matrix in the merge queue or on main. This is usually the largest saving (about half of the bill), because pull-request pushes outnumber merges several times.
- Moving a required check off pull requests changes the merge gate. Propose it to the repository owner with your numbers; do not change the gate alone.

## 4. Cache

- Restore dependency and build caches keyed on the hash of the lockfile and toolchain version (`hashFiles('**/uv.lock')`, `Cargo.lock`), so no job rebuilds from scratch what an earlier run built.
- Save caches from runs on main; let pull-request runs restore only. A cache that a pull-request run writes can be read only by that pull request, and the repository's cache limit (10 GB by default) evicts the oldest entries, so pull-request writes push out the shared ones.
- Key a cache by the job that restores it, not one key for everything, and read `gh api repos/<owner>/<repo>/actions/cache/usage` after the change.

## 5. Right-size the runner

- Use the smallest runner that keeps the job's wall time acceptable. A larger runner costs more per minute in proportion to its cores (a 16-core one about 7 times a 2-core one), so it pays only for work that uses the cores in parallel. Lint, `pip install` and scripts go on a 2-core runner.
- Before you choose a larger runner, run the job on both and compare billed minutes x price.
- Build for another architecture on a native runner of that architecture, not under emulation, which multiplies build time many times.

## 6. Cancel only what a newer head replaced

- Measure first: the dollars billed AFTER a newer push of the same branch, not the cost of all runs whose head was replaced. Most replaced runs finished before the newer push, so a cancel can recover only the part after it.
- Never put a pull-request workflow in a `cancel-in-progress: true` group keyed only on the branch or pull request when an event can arrive for the same head (`ready_for_review`, `reopened`, a re-run) or late for an older head. The same-head event cancels the live run and leaves cancelled checks that block the merge; the late event cancels the newest head's run.
- Key the group on the head SHA with `cancel-in-progress: false`, and cancel older heads explicitly: a step cancels a run only when an existing newer run of the same branch tests a different head, compared by head SHA and run id. Cancel only runs that have started; a run cancelled while it waits writes no check at all.
- On main, never cancel a run that publishes per commit. For test-only main runs, spare the oldest running one, so the trunk always has a verdict.

## 7. Never re-run CI by hand to check

- Do not start a run (`gh workflow run`, `gh run rerun`, an empty commit, a draft and ready toggle) on a head that a push or pull-request run already tested. The verdict exists: read it and its logs.
- A test that failed and passed on the same code is flaky: fix it at its cause. Re-run only the failed jobs, and only for an infrastructure failure (a lost runner, a network error), never to test a theory.

## 8. After the change: measure

- Measure the workflow's billed minutes and dollars per hour over the same length of window before and after, from the runs and jobs API (`gh api repos/<owner>/<repo>/actions/runs/<id>/jobs`), and post both numbers with the command on the pull request or issue. Without the after number nobody knows the change worked.
- Check per-job figures against GitHub's own billed usage per repository per day where your token can read it (`gh api '/orgs/<org>/settings/billing/usage?year=<Y>&month=<M>&day=<D>'`, which needs organization administration read), because the jobs API misses runs it cannot list.
