# Proof and claims

Read before you certify end-to-end evidence, refresh `## Verification`, or claim scope, coverage, a guard's safety, no regression or "cannot happen".

## Proof rows

What a change must prove, and the fields each `## Verification` row records, are in `opening-a-pr`'s step 4 reference (`opening-a-pr/references/proof.md`). Add to each row: the full tested sha with the base, version and configuration it ran with; the assertion that decides the scenario, not a job name; the exact command or driver; and each blind spot or skipped part, with its cause.

A change that deploys is proved on an isolated development stack or a recorded throwaway probe, never in production; infrastructure reaches production only from main after the merge.

## Match the instrument to the claim

- Name what the check decides, then make its input that thing. An import does not prove a credential can be minted; a template render does not prove the committed config; a YAML expression does not prove its secret reaches the consumer.
- For a changed credential or externally evaluated config, send one real request through the exact consumer, version and environment. Code, stubbed tests and docs that agree do not show the provider accepts it. Do not print credentials.
- Raise log verbosity only on your own logger: third-party libraries can print credential responses.
- Coverage needs a positive record of what executed. Zero covered is a failure, and a nonzero count can come from an error body.
- A tool's verdict has three outcomes: judged good, judged bad, never judged. Read its verdict vocabulary and scope, not its exit code. Mutants: killed, survived, never ran.

## Mutation and guard probes

- Name the test and exact assertion a mutation must violate; otherwise a probe can mutate a neighboring guard.
- Mutate the decision in a scratch copy, run the real consumer, and require the predicted red. Check from inside the run that the copy loaded (`module.__file__` or a head-unique output): an editable install can load the unchanged tree. A setup crash is not a kill.
- An assertion behind a branch the examples never enter is vacuous: count the examples that reach it. A large pass count never makes up for a surviving mutant.
- The oracle must not import, call or reproduce the production decision. A fake that filters like production can answer itself.
- Build fixtures with the real constructor where the contract compares identity (`is`, the same instance). Use the shared constant for fixture paths the consumer reads.
- Plant a defect where the guard might not look (nested or new paths, aliases, other scopes), and require a red. A widened guard also needs a probe in the old region. A text pattern over free-form traffic finds some cases, never all.
- Relaxing a guard: name the newly accepted case, and reproduce it and the still-refused boundary. Tightening: measure the legitimate ordering it now refuses, including a stall shorter than a new timeout.
- A guard moved earlier needs a probe that reds if it moves back. Pinning source text, a YAML literal or a default is not behavior coverage.
- Defense in depth: revert each control alone and record what the rest still protects.
- A second defect from the same test driver (the harness code that feeds inputs to the system under test): audit the whole driver against the contract, and write the invariant that closes the family.
- When the fix grows larger than the choice that caused it, review that choice.

## Keep each claim within its evidence

- Bind each evidence line to its measured sha; abandon, revert, restore and amend change the artifact.
- Read when something happened from the event record (timeline event, audit log), not from current fields such as `updated_at`.
- A claim about another branch, deployment or release expires when it changes.
- Summarize a run from its terminal categories (failed, passed, errors). A pass count does not prove a named test ran; read the per-test artifact.
- Before "X requires Y", look for X without Y in the same evidence.
- Write "cannot happen by construction" only with the construction: the type, check or code path that prevents it.
- Put an accepted deviation or verification limit in the PR body. A regression row that passed before the fix is not evidence of the fix.

## Scope and generated artifacts

- Describe scope from the complete file list and diff, measured against workflow path filters and consumer paths, not the body's intent.
- A census of callers searches callers of the shared helper and code that does the same thing without it. Count again at the ref under judgment.
- When serialization changes, search for consumers that compare (hashes, signatures, golden files, stored bundles). Run a differing fixture at head, base and main before you regenerate it.
- Prove "text-only" by restoring the old text in a scratch copy and comparing raw stdout, stderr, exit status and files.
- Set an acceptance target from the part of the measured cost the change can affect.
- A fork-pin bump carries `## Production behavior delta` in the PR body: each behavior change per release member, with concrete values and fork-only policy marked, or `none`.

## Preserve the evidence

- Run long checks as supervised jobs on an unchanged tree, and keep full logs.
- Each assertion must fail when the defect the reviewer named is present. When a gate stays red after a fix, read its current reason. Mark unfinished jobs PENDING, never "no red".
