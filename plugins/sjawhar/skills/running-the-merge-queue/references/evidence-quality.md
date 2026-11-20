# Evidence quality and stalled checks

Read when a packet reports no findings, no checks, missing content, an expected red or a stalled run.

## Main-side fixes and CI

- Do not ask for a push to pick up main when main only changes runner labels or fixes a failure that reds many PRs at once.
- A rerun of a cached test-merge keeps its original base, so it cannot prove a later main-side fix. Do not repeat a known no-op.
- By what the run evidence shows:
  - flake: rerun the failed job;
  - runner lost or run incomplete: check that the run belongs to the current head, then rerun that attempt;
  - red explained only by a newer main-side fix: wait until the owner pushes a content change, or Sami gives a new decision;
  - a failure that fix does not cover: the owner diagnoses it.
- Required red CI is not mergeable. If a main-red class needs a different queue-wide action, ask Sami once on the existing tracking issue and continue other work.
- A queued check-suite with zero check-runs is not a failed check. Read `mergeable_state` and the repository's docs on which GitHub App posts each required check; the owner follows `landing-a-pr`.
- A superseded cancelled run is not a current required failure. Identify it by run and head, not by conclusion alone.

## Missing logs are not a diagnosis

- A streaming job log can be empty, or reject terminal escape sequences, while the completed run's logs ZIP holds the text. Use the escape-sequence option or fetch the run archive through its API.
- A stalled wait can mean an unfinished ancestor or a missing artifact. Read the waiting job's status or log to see which.

## Counts and negative findings

- Put the population, revision, paths and pagination limits beside a count. A grep counts matches, not consumers, behaviors or PRs.
- An absence claim names where it looked, on the current PR head, with callers checked. Content absent only in a stale checkout or on the base branch can be on the head or another recorded branch: search there before anyone writes a replacement.
- "Flagged, unverified" is an investigation, not a proven defect a fixer must explain.
- A notice printed before single-line JSON must not make a line filter drop the payload: use structured output.
- A head-limited search of review text can show only historical blocker tokens and hide the final verdict.
- An unexplained file or line count is a scope mismatch for the owner, not proof of a cause. For a claimed prose-only migration, compare identity assignments and executable function bodies, not keywords that docstrings also contain.
- Never turn an unverified explanation into a constraint other owners must obey.
- If an authorized action removes a shared diagnostic artifact other lanes watch, tell those owners which observation is gone, and name it again in the next relevant status.
