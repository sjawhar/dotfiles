# Evidence quality and stalled checks

Read when a packet reports no findings, no checks, missing content, an expected red or a stalled run. Before you treat a suspiciously empty result as a fact, check that the reader works.

## Main-side fixes and CI

- Do not ask for a push to pick up main when main only changes runner labels or fixes a failure that reds many PRs at once.
- A rerun of a cached test-merge keeps its original base, so it cannot prove a later main-side fix. Do not repeat a known no-op.

| Run evidence shows | Action |
|---|---|
| Flake | Rerun the failed job |
| Runner lost or run incomplete | Check that the run belongs to the current head, then rerun that attempt |
| Red explained only by a newer main-side fix | Wait until the owner pushes a content change, or Sami gives a new ruling |
| Failure that fix does not cover | The owner diagnoses it |

- Required red CI is not mergeable. If a main-red class needs a different queue-wide action, ask Sami once on the existing tracking issue and continue other work.
- A queued check-suite with zero check-runs is not a failed check. Read `mergeable_state` and the repository's docs on which GitHub App posts each required check. The owner follows `landing-a-pr`.
- A superseded cancelled run is not a current required failure. Identify it by run and head, not by conclusion alone.

## Missing logs are not a diagnosis

- Read full errors. A streaming job log can be empty, or reject terminal escape sequences, while the completed run's logs ZIP holds the text. Use the escape-sequence option or fetch the run archive through its API.
- Never suppress stderr and then call empty output a pass or a permanent blocker.
- Name each failed test or job from its own artifact. Two logs cannot support "all four failed identically".
- A stalled wait can mean an unfinished ancestor or a missing artifact. Read the waiting job's status or log to see which it waits on.
- In the report, mark each cause as measured (cite the command and output) or as a hypothesis.

## Counts and negative findings

- Put the population, revision, paths and pagination limits beside a count. A grep counts matches, not consumers, behaviors or PRs.
- An absence claim names where it looked, on a current authoritative revision, with callers checked. If the content was absent only in a stale checkout or on the base branch, search the current PR head before anyone writes a replacement. Content absent from main can be on another recorded branch, so widen the search to fit the question.
- "Flagged, unverified" is an investigation, not a proven defect a fixer must explain.
- Before you trust an empty result, run the same query for an item you know exists (found another way); if that also returns nothing, the query is broken. An empty revset, missing SHA or zero-byte failed read stops the comparison.
- Keep file boundaries and parse the actual data shape. A notice before single-line JSON must not make a line filter drop the payload. Use structured output, not head/tail slices.
- Read the full relevant output. A head-limited search can show only historical blocker tokens and hide the final verdict.
- An unexplained file or line count is a scope mismatch for the owner, not proof of a cause. For a claimed prose-only migration, compare identity assignments and executable function bodies, not keywords that docstrings also contain.
- An observation names its image, revision or config. Never turn an unverified explanation into a constraint other owners must obey.
- If an authorized action removes a shared diagnostic artifact other lanes watch, tell those owners which observation is gone. Name it again in the next relevant status.
