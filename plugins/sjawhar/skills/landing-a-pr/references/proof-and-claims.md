# Changed-path proof and claims

Read before certifying end-to-end evidence, refreshing Verification or making a
scope/behavior claim. The rule covers every change that alters what runs,
including application, infrastructure, tasks, agents and harnesses.

## Pre-merge proof is about the changed consumer

Run the required changed end-to-end scenarios at the head under test on a
production-like surface before merge. A unit suite, static check, preview or
unrelated green deployment is groundwork, not that observation. When merging
triggers deployment, use an isolated development surface or recorded throwaway
probe first. Do not use production as the experiment.

For each affected path, record:

- Full tested SHA and relevant base/version/configuration.
- The consumer scenario and assertion, not just a job name.
- Surface, exact command or driver, outcome and duration.
- Linked evidence showing the scenario actually executed.
- Any blind spot, blocked/skipped part and its exact cause.

A push expires readiness at the old head. Rerun affected scenarios at the new
head, including merge-resolution content and interactions with a changed base.
Include earlier owned fixes a current-diff selector no longer sees. Patch
identity alone cannot prove compatibility with the new base.

A required blocked or skipped scenario is unmet acceptance. Repair the missing
surface, infrastructure, tool, skill or driver and run it. Only a **named blocker
Sami explicitly waived** permits the corresponding exception. Cite the scoped
decision, leave that row unverified and fulfill all other acceptance. An author,
reviewer or green CI result cannot invent an exemption. Do not request a waiver
merely to avoid reachable work, and do not present unwaived missing proof under a
ready packet.

## Match the instrument to the claim

Name what the check decides, then make its input that thing. An import does not
prove a credential can be minted. A template render does not prove a committed
configuration that enables a different path. A metric's vocabulary does not
prove no stop/delete action was issued. A YAML expression does not prove its
secret reaches the consumer.

When credentials or externally evaluated configuration change, use the exact
consumer reader, version and environment. Agreement among code, stubbed tests
and documentation is not proof of a provider exchange. Ask for the most recent
live observation; an old CLI's readable login cannot certify a newer CLI with a
different store. Never print credentials as part of the proof.

When raising log verbosity, include third-party libraries in the log surface.
A root DEBUG logger can expose credential responses through dependency parsers.
Inspect and constrain that surface rather than assuming only your own messages
will appear.

Coverage requires a positive statement of what executed. Read the cells and
assertions, not just the aggregate color. A count of zero covered is a finding,
not a pass. A nonzero count is insufficient too: an API error object can have
lines, and a downloaded file can be an unauthorized-response body. Confirm
status, recognizable content and expected labels through the real read path.

## Controls that can disagree

Before a probe, write what the old version, broken version and failed instrument
would return. The expected signals must differ. A no-op that passes on both
images proves neither the fix nor its deployment. A negative control should fail
for the named contract, not for a setup error, stale import or timeout.

For mutable live state, use controlled fixtures to establish both fire and
no-fire behavior before testing live. Where fixtures are impossible, carry a
positive control in the same invocation through the same query shape and access
path. Prove the control against known-good and known-bad inputs first. A control
sharing the measurement's blind spot proves nothing.

A tool with a verdict has three outcomes: judged good, judged bad and never
judged. Check its emitted verdict vocabulary and scope, not just zero/nonzero
exit or nonempty stdout. For mutations, distinguish killed, survived and never
ran. A clean control immediately beside each mutant helps separate a runtime
failure from a meaningful red.

Read result lists for what an alternative explanation would require to appear.
Absence is decisive only when the read is complete and capable of finding that
shape. A keyword filter needs another differently shaped source or the emitter's
own code. Search its actual vocabulary and structured fields, not remembered
words; verify exact-match/prefix behavior with a known result.

For a coverage or regression claim that needs deeper proof, read
[proof design](proof-design.md).

## Keep every claim within its evidence

- Bind every evidence line to its measured SHA. Abandoning, reverting, restoring
  or amending changes the artifact just as editing does. An old result cannot
  be reported for the new head because its pass count is remembered.
- Date artifacts before blaming an actor. Read event time from the object, not
  the tool-call time. Current fields describe now; events establish what was
  true in a historical window.
- Verify a citation's commit is relevant to the head you will change. A correct
  line at a stale checkout can describe the wrong system. Prefer live in-tree
  precedents the reader can open, and read the full statement before citing it.
- A claim about another branch, deployment or release expires when that subject
  changes. Recheck predicted downstream behavior against the actual new state.
- Start result summaries from the run's terminal categories. Account for failed,
  passed and errors separately. A pass count does not prove a particular named
  test ran; use the per-test artifact for that claim.
- Prove “all failures are X” with the not-X residual, not occurrences of X in
  tracebacks. State the population, time window, unit and retrieval limit.
- A correlation does not establish a prerequisite. Before “X requires Y”, look
  for X without Y in the same evidence set. A recurring error string can have
  different mechanisms; compare conditions that discriminate them.
- An accepted deviation or genuine verification limit belongs in the PR body
  as a decision/limit. A regression row that passed before the fix is future
  protection, not evidence that the fix corrected that row.
- “Cannot happen by construction” names a class to examine. A real construction
  proof can be stronger than an unavailable live observation, but a slogan that
  dismisses the subsystem's failure class is no proof.

## Scope, inventories and generated artifacts

Read the complete file list and actual diff before describing scope or deploy
consequence. A stat identifies files, not the particular edits inside them.
Measure against the repository's workflow filters and consumer paths rather than
the PR body's description of its own intent.

A census needs a defined population and an instrument that can find the relevant
shapes. Search both uses of the shared helper and code that implements the same
concept without calling it. Adding a synonym and comparing the found set can
expose a keyword-only blind spot. An authoritative inventory with a behavioral
or structural check suited to the contract is stronger than repeated prose
counts. Re-enumerate at the ref being judged, including the merged tree when
conflict resolution changes the population.

When serializing changes, search for consumers that **compare**, not only parse:
hashes, signatures, golden files and stored bundles. If a fixture now differs,
run the same case at head, base and current main before regenerating it. A
carried-over harness expectation is a claim about the old contract; re-derive it
for an intentional redesign rather than calling its failures expected or
deleting the evidence.

Prove “text-only” behavior with the candidate's old text restored in a scratch
artifact and compare raw stdout, stderr, exit and files with the old artifact.
Normalizing the changed phrase out of outputs can erase a real behavior change.
A new test example or docstring edit can invalidate an old mutation proof; rerun
the exact regression mutation after such an edit.

Derive an acceptance target from the measurement's residual: subtract costs the
change cannot affect before setting a number. Otherwise an apparently concrete
target can be impossible by construction.

## Preserve the evidence while measuring

Run long checks as supervised jobs against an unchanged tree; save their full
logs. Do not use a truncated tail as the only record of an expensive failure.
Identify the loaded module/version from inside the run; a path alone may still
name stale code, so a head-unique symbol or output can provide a stronger check.

Every assertion must cover the actual objection. When a gate stays red after a
fix, read its current reason rather than assuming it still objects to the last
thing you changed. Mark unfinished jobs PENDING, not “no red”; a reviewer cannot
attribute a later-discovered red if the earlier round never waited for that job.
