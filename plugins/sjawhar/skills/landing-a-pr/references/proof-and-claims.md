# Changed-path proof and claims

Read before you certify end-to-end evidence, refresh `## Verification`, or make a scope or behavior claim.

## Each proof row records

- Full tested SHA, and the relevant base, version and configuration.
- The consumer scenario and its assertion, not a job name.
- Surface, exact command or driver, outcome, duration.
- A link to evidence that the scenario executed.
- Each blind spot or blocked or skipped part, with its exact cause.

A unit suite, static check, preview or unrelated green deployment is groundwork, not this proof. Never request a waiver from the person who owns the work to avoid reachable work. An author, a reviewer or green CI cannot grant an exemption.

If the merge deploys, prove the change first on an isolated development surface or a recorded throwaway probe, never in production. Apply infrastructure to production only from main after the merge; a branch may go to a dev stack at any time.

## Match the instrument to the claim

- Name what the check decides, then make its input that thing. An import does not prove a credential can be minted. A template render does not prove a committed config that enables a different path. A metric's vocabulary does not prove no stop or delete was sent. A YAML expression does not prove its secret reaches the consumer.
- For changed credentials or externally evaluated config, use the exact consumer, version and environment. Ask the person who uses that credential or config when it last worked, and on which version. An old CLI's login cannot certify a newer CLI's store. Code, stubbed tests and docs that agree do not show the provider accepts the credential; send one real request to the provider. Never print credentials.
- Raised log verbosity includes third-party libraries, which can print credential responses. Raise verbosity only on your own logger, never on the root logger or a third-party library's.
- Coverage needs a positive record of what executed. Read the cells and assertions, not the color. Zero covered is a failure, not a pass. A nonzero count is not enough either, because an API error or an unauthorized-response body has lines. Check status, content and labels through the real read path.

## Controls that can disagree

- Before a probe, write what the old version, the broken version and a failed instrument would return. The signals must differ. A no-op that passes on both images proves nothing. A negative control must fail for the named contract, not a setup error, stale import or timeout.
- For mutable live state, show fire and no-fire on fixtures first. Without fixtures, run a positive control in the same call, query shape and access path, proven on known-good and known-bad inputs. A control with the measurement's blind spot proves nothing.
- A tool with a verdict has three outcomes: judged good, judged bad, never judged. Check its verdict vocabulary and scope, not its exit code or non-empty stdout. Mutants: killed, survived, never ran. Put a clean control beside each mutant.
- Before you conclude from a result list, name the other explanation and check the list for the entry that explanation predicts. Absence decides only when the read is complete and can find that shape. Back a keyword filter with a differently shaped source or the emitter's code. Search the real vocabulary and structured fields. Test exact-match and prefix behavior on a known result.

## Keep each claim within its evidence

- Bind each evidence line to its measured SHA. Abandon, revert, restore and amend change the artifact too.
- Before you blame an actor, read the time from the event record (timeline event, audit log entry), not from current fields such as `updated_at`, which describe now.
- Check that a cited commit is relevant to the head you will change. A claim about another branch, deployment or release expires when it changes. Recheck predicted downstream behavior against the new state.
- Summarize a run from its terminal categories: failed, passed, errors. A pass count does not prove a named test ran. Read the per-test artifact.
- Prove "all failures are X" with the not-X residual. State the population, window, unit and retrieval limit.
- Before "X requires Y", look for X without Y in the same evidence. One error string can have several mechanisms. Compare the conditions that tell them apart.
- Put an accepted deviation or verification limit in the PR body. A regression row that passed before the fix is not evidence of the fix.
- Write "cannot happen by construction" only with the construction: the type, check or code path that prevents it. That beats an unavailable live observation; the bare phrase does not.

## Scope, inventories and generated artifacts

- Before you describe scope, read the complete file list and diff. Measure against workflow filters and consumer paths, not the body's intent.
- A census needs a defined population and an instrument that finds every shape. Search callers of the shared helper, and code that does the same thing without it. Add a synonym and compare the found sets. Prefer an authoritative inventory with a behavioral or structural check over prose counts. Count again at the ref under judgment, the merged tree included.
- When serialization changes, search for consumers that compare (hashes, signatures, golden files, stored bundles). If a fixture differs, run the case at head, base and main before you regenerate it. Re-derive an inherited harness expectation after an intentional redesign. Never call its failures expected or delete the evidence.
- Prove "text-only" by restoring the old text in a scratch artifact and comparing raw stdout, stderr, exit and files. A new test example or docstring can void a mutation proof. Run the mutation again.
- Set an acceptance target from the part of the measured cost the change can affect: subtract the costs it cannot affect first.
- A fork-pin bump carries `## Production behavior delta` in the PR body: each behavior change per release member, with concrete values and fork-only policy marked, or `none`. Follow the repo's fork-placement and review rules.

## Preserve the evidence

- Run long checks as supervised jobs on an unchanged tree, and keep full logs.
- Identify the loaded module from inside the run. A head-unique symbol beats a path.
- Each assertion must fail when the defect the reviewer named is present. When a gate stays red after a fix, read its current reason. Mark unfinished jobs PENDING, never "no red".
