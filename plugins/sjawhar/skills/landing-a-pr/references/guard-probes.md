# Proof design: mutations, fixtures and guards

Read before you claim coverage, independence, a guard's safety, no regression or "cannot happen".

- Name the test and exact assertion a mutation must violate; otherwise a probe can mutate a neighboring guard.
- Mutate the decision in a scratch copy, run the real consumer, and require the predicted red. Check from inside the run that the copy loaded (`module.__file__` or a head-unique output): an editable install can load the unchanged tree. A setup crash is not a kill.
- Among deterministic orderings, mutate to each plausible wrong choice and require each red. Rerun the mutations after a test example or docstring changes.
- An assertion behind a branch the examples never enter is vacuous: count the examples that reach it. A large pass count never makes up for a surviving mutant.
- The oracle must not import, call or reproduce the production decision. A fake that filters like production can answer itself.
- Build fixtures with the real constructor, above all where the contract compares identity (`is`, the same instance), and check copying and memoization. Use the shared constant for fixture paths the consumer reads, and prove the real reader finds the fixture.
- An expected value derived from the same constant proves shape only. Where an external consumer hard-codes the value, check it at that layer.
- Plant a defect where the guard might not look (nested or new paths, aliases, other scopes), and require a red. A widened guard also needs a probe in the old region.
- A text pattern over free-form traffic finds some cases, never all: replay it and read its false refusals and misses by hand. Give any "within N lines/characters" window an explicit N and test both edges.
- A failure that does not match the signature you wrote before the run is a possible new defect, not a refusal.
- Relaxing a guard: name the newly accepted case, and reproduce it and the still-refused boundary with the IDs (user, session, request) of the event judged, not current state. Tightening: measure the legitimate ordering it now refuses, including a stall shorter than a new timeout, and keep the per-call allowance apart from the total budget.
- An exemption set and the sweep of its complement are coupled: probe what exempt members do. A change that adds a capability rechecks each precondition that held only because it was missing.
- A guard moved earlier needs a probe that reds if it moves back by observing the prohibited side effect (a fetch before validation); deletion alone does not prove order. Pinning source text, a YAML literal or a default is not behavior coverage. With no harness, name the uncovered ordering at the site.
- A positional producer and consumer: compare produced slots with consumed indices and deliberate gaps.
- Defense in depth: revert each control alone and record what the rest still protects.
- A second defect from the same test driver (the harness code that feeds inputs to the system under test): audit the whole driver against the contract, and write the invariant that closes the family.
- When the fix grows larger than the choice that caused it, review that choice.
- Before you copy code to avoid an import, ask whether the source can drop its heavy import-time dependency, and reuse existing helpers. A second copy of a selector or regex is not a dependency-boundary fix.
