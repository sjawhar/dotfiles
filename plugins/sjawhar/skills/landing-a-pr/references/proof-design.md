# Proof recipes for silent failures

Read when review or a packet claims coverage, independence, a guard's safety or
absence of a regression. Follow the repo's testing conventions; use throwaway
probes for evidence that does not justify a permanent consumer-contract test.

## Prove that the assertion reaches the decision

Name the test and exact assertion a mutation must violate. “Test the behavior”
alone lets a probe mutate a neighboring guard and prove the wrong thing.

Run the real consumer at the candidate, revert or mutate the decision in a
scratch copy, and require the predicted red. Confirm the copy loaded from inside
the run (`module.__file__` or a head-unique symbol/output). An editable install can
silently route a scratch test back to the unchanged tree. A setup crash proves
neither the contract nor a mutant kill; read failure types before counting.

A green property can be vacuous even with many generated examples if its
assertion sits behind a branch the examples never enter. A fake that implements
the same filtering as production can likewise answer itself while the actual
query never runs. Surviving mutants require investigating the test's reach,
not accepting a large pass count as compensation.

## Separate fixtures from oracles

- The oracle must not import, call or reproduce the production decision it is
  meant to check. Search it for the matcher/parser/comparator's symbol. Shared
  decision logic makes both sides agree under the same bug.
- A fixture should use the real constructor, especially for identity contracts.
  A hand-built pair can share one object where the production resolver copies
  every binding. Inspect copying/memoization and construct the real shape.
- A shared constant is right for fixture paths the consumer reads. Spelling a
  path from memory can place the fixture where the reader never looks. Prove
  the fixture resolves through the real reader.
- Deriving expected values from the same constant proves shape, not a fixed
  consumer-visible value. If the external consumer hard-codes a value, check
  that contract at its layer. If the contract is merely shape, derivation can
  be correct; do not turn every implementation constant into a literal pin.

For defense-in-depth, revert each control **alone** and record the cases each
remaining layer protects. Two fixes do not imply two independent layers on every
path. For “guard B makes A redundant”, compare their quantified predicates. Two
checks that mention organization can bind to different organizations or scopes;
each may be necessary.

## Plant outside the guard's claimed reach

A review can read only where the implementation looks. To certify coverage,
plant a defect in a region the guard might miss and require a red there.
Enumerate dimensions, not just directories:

- Location: top-level versus nested or newly introduced paths.
- Shape: names, aliases, signatures and configured discovery patterns.
- Binding: which consumer, role, object or requested scope is checked.

A widening also needs a probe in the previously covered region, so it cannot
silently narrow another dimension. Derive discovery rules from the actual tool's
configuration rather than keeping a second interpretation in the guard.

A text-matching gate facing diverse traffic needs replay and manual inspection
of false refusals and misses. A matcher can be a measured lower bound; do not
promote it to an authority over cases it cannot recognize. For exactly one
controlled emitter, mutate that emitter to prove coupling, or give it a
structured field instead of widening a fragile text matcher. Bound any retained
proximity window so unrelated nearby numbers cannot become false matches.

A pre-registered failure signature is different: unmatched means “new defect,
read it”, never an automatic refusal of valid work.

## Test the new ordering when a guard changes

Relaxing a guard accepts a state/ordering it previously refused. Name that case
before changing it and reproduce both the newly accepted case and the still
refused boundary. This includes retries after resets, edits made empty, mixed
identities and callbacks arriving across a user turn. A time or generation
counter read later may refer to a different operation; carry the identity from
the event whose result is being judged.

Tightening does the converse: measure the legitimate ordering it now refuses,
not just the failure that motivated the bound. Include a stall shorter than a
new timeout, and distinguish the per-call allowance from the total budget.

An exemption set and a sweep of its complement are coupled. Adding exemptions
shrinks the measured set; probe the exempt members for what they actually do.
A precondition that held only because a capability did not exist must be
re-derived once the capability is added.

## Position and deterministic alternatives

A guard moved earlier for a reason needs a probe that reds if moved back.
Deletion alone cannot prove ordering. Observe the prohibited side effect, such
as fetching before validation, or the erroneous exception context. Do not pin
source text, a YAML literal or an incidental default and call that behavior
coverage. Where no runnable workflow harness exists, name the uncovered ordering
and document the dependency at the site rather than claiming it tested.

When a fix chooses among deterministic orderings, one example may coincide with
the wrong first/last/insertion-order answer. Mutate to each plausible wrong
choice and require each to red. If a test example changes while its prose is
corrected, rerun those mutations; more accurate prose does not preserve reach.

For positional producer/consumer contracts, compare the produced slots against
the indices consumed and deliberate gaps. A clean textual merge of two appends
can ship wrong values without a conflict marker.

## Stop repairing one instance at a time

On the second defect of the same origin, audit the driver against the whole
contract: mocks, injected failures, assumptions and response reads. Construct
interactions with collaborators rather than classifying them from only the side
you own. The output is an invariant that closes the family, not another patch.

When the repair surface grows larger than the choice that caused it, review the
original design. Do not keep polishing a default or mechanism whose premise is
wrong. Tell the next reader the defect family so it searches related shapes.

Before adding a copy because one module cannot import another, ask whether the
source can stop needing the heavy dependency at import time. Enumerate existing
helpers and constants, and preserve shared validation/argument contracts. A
second copy of a selector or regex is not a dependency-boundary solution merely
because it keeps one caller light.

A repeatedly violated ordering rule may need a refusal at the action boundary
rather than more prose. Establish that mechanism in the owning tool, with its
real failure case, when that is the approved work. Do not add a new gate as an
incidental part of preparing a PR packet.
