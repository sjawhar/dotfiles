# Proof recipes for silent failures

Read when a review or packet claims coverage, independence, a guard's safety or no regression. Follow the repo's test conventions. Use throwaway probes for evidence that does not need a permanent consumer-contract test.

## The assertion must reach the decision

- Name the test and exact assertion a mutation must violate. Otherwise a probe can mutate a neighboring guard.
- Run the real consumer at the candidate, mutate the decision in a scratch copy, and require the predicted red. Check from inside the run that the copy loaded (`module.__file__` or a head-unique output). An editable install can route back to the unchanged tree. A setup crash proves neither the contract nor a kill. Read failure types before you count.
- Many generated examples are vacuous if the assertion sits behind a branch they never enter. A fake that filters like production can answer itself. A large pass count never makes up for a surviving mutant. Find the branch the assertion sits behind and count how many examples reach it.

## Fixtures versus oracles

- The oracle must not import, call or reproduce the production decision. Search it for the matcher's, parser's or comparator's symbol.
- Build fixtures with the real constructor, mainly for contracts that compare objects by identity (`is`, `id()`, the same instance). Inspect copying and memoization, and construct the real shape.
- Use the shared constant for fixture paths the consumer reads, and prove the real reader finds the fixture.
- An expected value derived from the same constant proves shape only. Where an external consumer hard-codes the value, check it at that layer. Where the contract is shape, derivation is right. Do not pin every constant as a literal.
- Defense in depth: revert each control alone and record what the rest still protects. For "guard B makes A redundant", compare their predicates. Two checks can bind to different scopes.

## Plant outside the guard's reach

- Plant a defect where the guard might not look, and require a red. Vary location (nested, new paths), shape (names, aliases, signatures, discovery patterns) and binding (consumer, role, object, scope).
- A widening also needs a probe in the old region. Take discovery rules from the tool's own config.
- A text pattern run over free-form traffic finds some cases, never all; do not treat its silence as absence. Replay it and read false refusals and misses by hand. For one controlled emitter, mutate the emitter, or give it a structured field. Give any "within N lines/characters" window an explicit N, and test both edges.
- When a failure does not match the signature you wrote before the run, read it as a possible new defect; do not count it as a refusal.

## Guard changes and ordering

- Relaxing a guard accepts a state it refused. Name that case first. Reproduce the newly accepted case and the still-refused boundary: retries after resets, edits made empty, mixed identities, callbacks across a user turn. Use the IDs (user, session, request) from the event being judged, not from current state.
- Tightening: measure the legitimate ordering it now refuses, including a stall shorter than a new timeout. Keep the per-call allowance apart from the total budget.
- An exemption set and the sweep of its complement are coupled. Probe what exempt members do. When a change adds a capability, recheck each precondition that held only because the capability was missing.
- A guard moved earlier needs a probe that reds if it moves back. Deletion alone cannot prove ordering. Observe the prohibited side effect, such as a fetch before validation or a wrong exception context. Pinning source text, a YAML literal or a default is not behavior coverage. With no harness, name the uncovered ordering and document it at the site.
- Among deterministic orderings, mutate to each plausible wrong choice and require each to red. Run the mutations again after a test example changes.
- Positional producer and consumer: compare produced slots with consumed indices and deliberate gaps.

## Stop fixing one instance at a time

- When a second defect comes from the same test driver (the harness code that feeds inputs to the system under test), audit that driver against the whole contract: mocks, injected failures, assumptions, response reads. Construct the interactions with collaborators, not only your own side. Write an invariant that closes the family, and name the family for the next reader.
- When the fix grows larger than the choice that caused it, review that choice. Stop polishing a mechanism whose premise is wrong.
- Before you copy code to avoid an import, ask whether the source can drop its heavy import-time dependency. Reuse existing helpers and validation contracts. A second copy of a selector or regex is not a dependency-boundary fix.
- A rule broken again and again may need a refusal in the owning tool, built with its real failure case. Do this only when it is the approved work. Never add a gate as a side effect of a packet.
