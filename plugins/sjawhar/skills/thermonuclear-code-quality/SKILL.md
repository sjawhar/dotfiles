---
name: thermonuclear-code-quality
description: Use when asked for a thermonuclear code-quality review or a deep maintainability audit of a branch or PR diff. Also use when asked to review abstraction quality, oversized files or ad-hoc branching growth.
disable-model-invocation: true
---

# Thermonuclear Code Quality

A strict maintainability audit of the current branch's changes.

## Baseline

- Rethink how to structure and implement the changes so code quality improves and behavior does not change. The aim is code that is smaller, more direct and easier to maintain.
- Improve abstractions and modularity. Reduce spaghetti code. Make the code succinct and legible.
- Restructure when there is a clear path to a simpler implementation. Do not stop at local cleanup when a coherent simplification is available.
- Be thorough and rigorous. Verify each claim before you report it.

## Look for the code-judo move first

- For each meaningful change, ask: can a reorganization that uses the existing architecture better make whole branches, helpers, modes, conditionals or layers disappear?
- Push hard to delete complexity, not to rearrange it. Prefer the solution that looks inevitable in hindsight.
- A refactor that moves code but leaves the reader the same number of concepts to hold is not a simplification. Ask whether the model itself can be simpler.
- Do not accept a cleaner version of the same messy idea when a much simpler idea is plausible. Do not give "maybe rename this" feedback when the real issue is structural.
- "It works" and "tests pass" are not the bar. If the structure can become meaningfully cleaner with the same behavior, push for the cleaner version.

## What to flag, and the remedy to propose

Flag these aggressively, and any change that makes the surrounding code harder to reason about, even when the code works:

| Smell | Remedy |
|---|---|
| The PR pushes a file from under 1000 lines to over 1000 | Ask whether to decompose first into helpers, subcomponents, modules or local abstractions. Accept only for a compelling structural reason, with the file still well organized. |
| Ad-hoc conditionals, special cases, one-off booleans, nullable modes or flags in unrelated or busy flows. Narrow edge cases in the middle of a busy function. "Temporary" branches. | A design problem, not a nit. Reframe the state model so the conditionals disappear, not only move to one place. Make the special case part of a simpler default flow. Else move it behind its own abstraction, helper, state machine, policy object or module. |
| Repeated conditionals or condition chains | A typed model or an explicit dispatcher. |
| Copy-pasted logic or duplicate branches | Extract a helper or pure function. Collapse into one flow. |
| Hacky, brittle or "magic" behavior. Generic mechanisms that hide simple data-shape assumptions. | Direct, boring code. |
| Thin wrappers, identity abstractions, pass-through helpers | Delete the layer. |
| Unnecessary optionality, `unknown`, `any`, casts, loose ad-hoc objects. A silent fallback over an unclear invariant. | Explicit typed models or shared contracts, so the control flow gets simpler. |
| Feature logic in shared paths. Implementation details leaking through APIs. Logic in the wrong file, layer or package. | Move it to the layer that owns the concept, so the feature extends an existing abstraction. |
| A bespoke helper where a canonical utility exists | Reuse the canonical helper. |
| A module that became more coupled, stateful or hard to scan. Orchestration mixed with business logic. | Split into focused modules. Separate orchestration from business logic. |
| Independent work run in sequence for no good reason | Run it in parallel when that also simplifies the orchestration. |
| Related updates that can leave state half-applied | A more atomic flow. |

Flag orchestration only when the cleaner structure is obvious. Do not over-index on micro-optimizations.

## Tone

- Be direct, serious and demanding. Do not be rude.
- Do not soften a major maintainability issue into a mild suggestion. If the code makes the codebase messier, or missed a substantial simplification, say so.
- Example: `this adds another special-case branch into an already busy flow. can we move this behind its own abstraction?`

## Output

Order findings:

1. Structural regressions
2. Missed dramatic simplifications (code judo)
3. Spaghetti and branching growth
4. Boundary, abstraction and type-contract problems
5. File size and decomposition
6. Modularity and abstraction
7. Legibility and maintainability

Give a small number of high-conviction comments. When larger structural issues exist, leave out low-value nits.

## Approval bar

Correct behavior alone does not earn approval. Approve only when the diff has none of these:

- a clear structural regression
- a missed, visible path to a dramatically simpler implementation
- unjustified file-size growth
- spaghetti growth from special-case branching
- a hacky or magic abstraction
- wrapper, cast or optionality churn that hides the design
- a clear boundary leak, or avoidable duplication of a canonical helper
- a missed obvious decomposition that would materially help maintainability

These block unless the author justifies them with evidence:

- incidental complexity kept when a plausible code-judo move would delete it
- a file pushed from under 1000 lines to over 1000
- ad-hoc branching that tangles an existing flow
- a local problem solved by scattering feature conditionals across shared code
- an unnecessary abstraction, wrapper or cast-heavy contract
- a duplicated helper, or logic in the wrong layer, when a clear canonical home exists

When the bar is not met, leave explicit, actionable feedback and push for a cleaner decomposition.

## Verdict

Make the first line of the report the verdict, in exactly this form, with the short sha of the head commit you reviewed (`git rev-parse --short=8 HEAD`):

- `Verdict: APPROVE at <sha>` when the diff meets the approval bar.
- `Verdict: CHANGES at <sha>` when it does not.

`pr-gates` reads only the first line of each PR comment, so put nothing before the verdict.
