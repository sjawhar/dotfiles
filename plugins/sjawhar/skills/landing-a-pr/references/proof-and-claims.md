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

## Controls that can disagree

- Before a probe, write what the old version, the broken version and a failed instrument would return; the signals must differ. A negative control must fail for the named contract, not a setup error, stale import or timeout.
- For mutable live state, show fire and no-fire on fixtures first. Without fixtures, run a positive control in the same call, query shape and access path, proven on known-good and known-bad inputs. A control with the measurement's blind spot proves nothing.
- A tool's verdict has three outcomes: judged good, judged bad, never judged. Read its verdict vocabulary and scope, not its exit code. Mutants: killed, survived, never ran; put a clean control beside each.
- Before you conclude from a result list, name the other explanation and look for the entry it predicts. Absence decides only when the read is complete and can find that shape. Back a keyword filter with a differently shaped source or the emitter's code, and test exact-match and prefix behavior on a known result.

## Keep each claim within its evidence

- Bind each evidence line to its measured sha; abandon, revert, restore and amend change the artifact.
- Read when something happened from the event record (timeline event, audit log), not from current fields such as `updated_at`.
- A claim about another branch, deployment or release expires when it changes.
- Summarize a run from its terminal categories (failed, passed, errors). A pass count does not prove a named test ran; read the per-test artifact.
- Prove "all failures are X" with the not-X residual, and state the population, window, unit and retrieval limit.
- Before "X requires Y", look for X without Y in the same evidence.
- Write "cannot happen by construction" only with the construction: the type, check or code path that prevents it.
- Put an accepted deviation or verification limit in the PR body. A regression row that passed before the fix is not evidence of the fix.

## Scope and generated artifacts

- Describe scope from the complete file list and diff, measured against workflow path filters and consumer paths, not the body's intent.
- A census needs a defined population and an instrument that finds every shape: search callers of the shared helper and code that does the same without it, add a synonym and compare the found sets, prefer an authoritative inventory to prose counts, and count again at the ref under judgment, the merged tree included.
- When serialization changes, search for consumers that compare (hashes, signatures, golden files, stored bundles). Run a differing fixture at head, base and main before you regenerate it. After an intentional redesign, re-derive an inherited harness expectation; never call its failures expected or delete the evidence.
- Prove "text-only" by restoring the old text in a scratch copy and comparing raw stdout, stderr, exit status and files.
- Set an acceptance target from the part of the measured cost the change can affect.
- A fork-pin bump carries `## Production behavior delta` in the PR body: each behavior change per release member, with concrete values and fork-only policy marked, or `none`.

## Preserve the evidence

- Run long checks as supervised jobs on an unchanged tree, and keep full logs.
- Each assertion must fail when the defect the reviewer named is present. When a gate stays red after a fix, read its current reason. Mark unfinished jobs PENDING, never "no red".
