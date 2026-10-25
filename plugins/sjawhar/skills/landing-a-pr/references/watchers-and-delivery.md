# Watchers and post-merge delivery

Read before you brief `ce-babysit-pr`, start a detector, diagnose a silent watcher or check delivery.

## The rest of the babysitter's brief

- Skip its checkout step, and edit the PR title, body and `## Verification` directly, because `ce-commit-push-pr` is unavailable.
- GitHub's `mergeable: MERGEABLE` and `mergeStateStatus: CLEAN` (`gh pr view --json mergeable,mergeStateStatus`), no open review items, and checks that stopped changing do not make the PR ready; proof and a review of the current head decide.
- Its sub-skills (`ce-debug`, `ce-resolve-pr-feedback`, `mode:pipeline`) never rebase, force-push, approve gated runs or merge.

## The watcher

- Keep `ce-babysit-pr`'s dedup, stale-SHA cancellation, trajectory triggers (the `pr-snapshot` thresholds that pass its `trajectory` facts to `ce-debug` or `ce-resolve-pr-feedback`: `check_recur_max >= 2`, `stream_alternations >= 3`, a rising `unresolved_trend` with new threads, `heads_since_progress >= 2`, or any `invariant_rounds` at 2), settle window and three-day backstop, so one event does not start two fix rounds.
- A review someone acknowledged but has not posted keeps the PR not ready, green CI included.
- Events wake the owner. A fresh snapshot says what is true.
- Do other work while you wait. Run the watcher as a background job whose output or exit reaches you as an Envoy message or a tool follow-up, because a watcher nobody hears is no watcher.
- Read its first poll. Silence fits a missing command, a broken parser, an expired token and no event alike. Before you act on its exit, read the run's own status.
- List the real job names before you write a filter. Match the full nested name, never a substring such as "deploy". Record the exact job and conclusion you wait for.
- Single-quote jq programs or use a file. An empty or failed parse is a loud failure. Check partial output for every field name you expected. Keep stderr out of compared values.
- Call `gh` fresh for each observation so the token refreshes. Never capture one token for a long loop. Check run-list limits and event filters before you conclude that all runs for a SHA finished.
- A long watch stream can stop cleanly. When it underpins proof, reconcile its object IDs with independent reads or driver actions. Resume from the last resource version where supported.

## The carrying run

- Find the run whose head contains the merge, by ancestry. After you fetch both objects, `jj log -r '<merge-sha> & ::<run-head>'` must return the merge. Read the required job's state.
- Pin that run for the whole wait. Never follow "the newest live run" each tick.
- If the pinned run is cancelled, superseded or gone, re-resolve and check ancestry again. When a queue coalesces or replaces runs, account for every candidate.
- Cancellation alone is neither a production failure nor proof of non-delivery. Read a coalescing queue's semantics before you blame a cancellation. Do not apply them to other workflows.
- Diagnose a failed run at the named job and step, folded outcomes included.

## The artifact in production

- After the production apply, identify the deployed image or version of the component this PR changed.
- For installed software, check the version the consumer runs. An install the authorized change needs is part of the work. Use a head-specific artifact or behavior, never a guessed string in a minified bundle.
- Zero-write probe: an atomic batch whose first step the new version rejects and whose second step always fails on the old version. Check the ordering and predicted errors first.

## After the squash

- A deleted branch or missing pre-squash SHA does not show whether work merged. Read the destination's content (`jj file show -r <destination> <path>`) before you redo work.
- The GitHub PR-head ref can keep PR commits reachable. Check it before you rely on it. Cite intermediate evidence by SHA and PR number.
- Read the merge state and message from GitHub, not a command's exit or an armed auto-merge:

```sh
gh api repos/<owner>/<repo>/pulls/<N> --jq '{merged, merge_commit_sha}'
gh api repos/<owner>/<repo>/commits/<merge-sha> --jq .commit.message
```

- Squash defaults can join old subjects, retracted claims too. Reread the title and body against the final head before the packet, and the merged message after. Never rewrite merged history.
- Name the SHA each figure came from.
