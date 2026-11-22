# Watchers and the babysitter's brief

Read before you brief `ce-babysit-pr`, start a watcher, diagnose a silent one, or watch the deploy run after the merge. Delivery after the merge is `post-merge`'s. "The deploy run" adds what it leaves out.

## The babysitter's brief

Give `ce-babysit-pr` these instructions before its first tick:

1. No automatic base update.
   - `mergeStateStatus: BEHIND` alone is no reason: no `update-branch`, no base merge nobody asked for.
   - For a real conflict, or a base change the PR needs to build or pass:
     - Message the person who owns the work.
     - Integrate only when they decide to.
   - On a published or reviewed head use only a forward jj merge; do not rebase, amend, squash or force-push it, because that voids the reviews of that head.
2. Wake on Envoy `notifications.github.<owner>.<repo>.pr.<N>.>`.
   - Run `pr-snapshot watch` in the background.
   - Do not block on `gh pr checks --watch`, `gh run watch` or a sleep loop: the turn misses review events.
   - On a repo our GitHub App is not installed on (every upstream project):
     - The subscription is accepted but never fires.
     - `envoy_subscribe` warns that no GitHub event from that repo is in the stream's retention window.
     - There, `pr-snapshot watch` or another timed re-check is the only wake.
3. When one check completes, wait without a full tick until every expected check is present and done: each check the branch rules require (`gh pr checks <N> -R <owner>/<repo> --required`) and each check the previous head ran.
   - Tick then, on a review event, or on a push.
   - Zero checks is not "all passed".
4. Skip your checkout step.
   - Edit the PR title, body and `## Verification` directly (`ce-commit-push-pr` is disabled here).
   - Your sub-skills do not rebase, force-push, approve gated runs or merge.
5. `MERGEABLE`, `CLEAN`, no open review items and settled checks do not make the PR ready.
   - Proof and a review of the current head decide.
6. Keep your dedup, stale-sha cancellation, trajectory triggers, settle window and three-day backstop, so one event does not start two fix rounds.
   - The trajectory triggers are your own `references/tick.md` thresholds over `pr-snapshot`'s `trajectory` facts, which you pass to `ce-debug` or `ce-resolve-pr-feedback`: `check_recur_max >= 2`, `stream_alternations >= 3`, a rising `unresolved_trend` with new threads, `heads_since_progress >= 2`, or any `invariant_rounds` at 2.

## The watcher

- One watcher per PR across all sessions; watching three PRs is one loop over three.
- Do other work while the watcher runs, as a background job whose output or exit reaches you as an Envoy message or a tool follow-up.
- Do not run a bare `gh run watch` or `gh pr checks --watch`: they poll every 3 and 10 seconds, and every agent on the organisation shares one App installation's REST budget.
  - Pass `--interval 120` or longer to `gh run watch` or `gh pr checks --watch`, or run one `gh pr checks` without `--watch` every 120 seconds or more.
- On a rate-limit error, back off; do not retry sooner.
- Read its first poll.
  - Silence fits a missing command, a broken parser, an expired token and no event alike.
- Before you act on its exit, read the run's own status.
- List the real job names before you write a filter.
  - Match the full nested name, not a substring such as "deploy".
  - Record the exact job and conclusion you wait for.
- A review someone acknowledged but has not posted keeps the PR not ready, green CI included.
- Events wake you; a fresh snapshot says what is true.
- Call `gh` fresh for each observation so the token refreshes; do not capture one token for a long loop.
- Check run-list limits and event filters before you conclude every run for a sha finished.
- A long watch stream can stop cleanly.
  - When it underpins proof, reconcile its object IDs with independent reads or driver actions.
  - When it underpins proof, resume from the last resource version where supported.
- Count and kill a watcher by its parent.
  - A wrapped watch (`knives gh -- pr checks … --watch`) is a wrapper and a `gh` child.
  - A killed child is re-spawned by the wrapper.
  - Kill the wrapper by pid, then confirm the child is gone.
- After a two-call `gh` operation (`pr ready --undo` then `pr ready`, a label added then removed, auto-merge disabled then enabled), one half can land while the other fails.
  - Read the field you meant to change (`gh pr view <N> --json isDraft,labels,reviewDecision,autoMergeRequest`), not the last error.

## The deploy run

`post-merge` finds the deploy run that carries the merge by ancestry, pins it, and diagnoses its failure. Also:

- If the pinned run is cancelled, superseded or gone, re-resolve it and check ancestry again.
- When a queue coalesces or replaces runs, account for every candidate run.
- Diagnose a failed run at the named job and step, folded outcomes included.
- Cite evidence from an intermediate (pre-squash) commit by its sha and PR number.
