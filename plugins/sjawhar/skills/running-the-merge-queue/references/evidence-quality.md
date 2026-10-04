# Evidence quality and stalled checks

Read this when a packet reports no findings, no checks, missing content, an expected
red or a stalled run. A suspiciously empty result first needs a working-reader
check; it is not automatically a fact about the world.

## Main-side fixes and CI

Do not ask an owner to rebase or push simply to pick up main without a conflict.
This includes runner-label changes and a main-side fix for a whole red class.
A rerun of an existing cached test-merge keeps its original base; it cannot prove
that a later main-side fix repaired that PR's run. Do not repeat a known no-op.

Distinguish the cases from actual run evidence:

- A flake can warrant rerunning the failed job.
- A runner-lost or incomplete run can warrant a rerun of the relevant attempt;
  first verify the run belongs to the current head, not a superseded push.
- A red explained only by a newer main-side fix waits for real owner work or an
  explicit new ruling; do not solicit a pointless refresh push.
- A failure not covered by that fix needs its own diagnosis by the owner.

Required red CI is still not mergeable. If a main-red class needs a different
queue-wide action, ask Sami once on the existing tracking issue instead of inventing
an exception. Continue other work while it is unanswered.

A queued check-suite with zero check-runs is not itself a failed check. Read
mergeability and the repository's App/check contract; use `landing-a-pr` for that
owner workflow. A superseded cancelled run is not a current required failure, but
identify it by run and head, not by conclusion alone.

## Missing logs are not a diagnosis

Read full errors. A streaming job-log response can be empty or reject terminal
escape sequences while the completed run's logs ZIP still contains the per-job
text and summary. Use the supported escape-sequence option if appropriate, or
fetch the completed run-level archive through its documented API. Do not suppress
stderr and then call an empty output a pass or an unclassifiable permanent blocker.

Name each failed test/job from its own artifact. Two logs plus two job conclusions
cannot support “all four failed identically.” A stalled wait can mean an unfinished
ancestor or a missing artifact; ask its own decision output which it is. State the
observed mechanism separately from the one not yet measured.

## Scope, counts and negative findings

- Record the population, queried revision, paths and pagination limits beside a
  count. A grep counts matches, not consumers, behaviors or PRs.
- A claim that a primitive is absent needs a current authoritative revision and
  its callers checked. A stale local checkout or base-only read is not grounds to
  hand-roll a replacement. Treat “flagged, unverified” as an investigation, not a
  confirmed defect a fixer must explain.
- An absence claim names where it looked. Content absent from main can exist on
  another recorded branch. Widen the search according to the question before
  declaring the content nonexistent.
- First make the reader return a known-present control by an independent mechanism.
  An empty revset, missing SHA or zero-byte failed file read must stop comparison.
- Preserve file boundaries and parse the actual data shape. A notice preceding
  single-line JSON must not cause a line filter to delete the payload. Use the
  structured tool output or parse the JSON payload, not ad hoc head/tail slices.
- Inspect full relevant output. Head-limited searches can return only historical
  blocker tokens and hide the actual final verdict.
- An unexplained file/line count is a scope mismatch to return to the owner, not
  proof of a particular cause. For a claimed prose-only migration, the evidence
  should compare actual identity assignments and executable function bodies, not
  keywords in a patch that also occur inside docstrings.

An observation must name the image/revision/config it describes. Correlation is
not a reusable mechanism: do not turn an unverified explanation into a constraint
other owners must obey.

If an authorized action removes a shared diagnostic artifact other lanes were
watching, notify those owners and name the removed artifact again in the next
relevant status. Say which observation became unavailable; they may already have
reasoning in flight against it.
