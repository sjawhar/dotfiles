---
name: sdd
description: Execute work via subagent-driven development with Sami's fixed agent mapping (planner plans, reviewer gates, deep implements).
disable-model-invocation: true
---

# Subagent-Driven Development (Sami's mapping)

Load the `subagent-driven-development` skill and execute the given work through it with this fixed agent mapping:

- **Implementation and debugging** → `task` with `agent: "deep"`
- **Planning** → `task` with `agent: "planner"`
- **Plan and PR review** → `task` with `agent: "reviewer"` (built-in; returns a verdict plus findings)
- **Strategy consults** (architecture calls, gnarly tradeoffs, stuck debugging) → `task` with `agent: "oracle"` (read-only)

You are the coordinator: dispatch, verify results file-by-file, integrate, and manage the backlog. **Do not plan or implement yourself** — burning your own context on implementation instead of orchestrating is the failure mode this command exists to prevent.

**Parallelize aggressively.** Structure the plan itself to maximize independent tasks; dispatch independent tasks in the same turn; a blocked lane never idles the pipeline.

Pipeline: plan (writing-plans skill, dispatched to the `planner` agent) → `reviewer` gates the plan → subagents implement — a multi-PR plan stacks via the `gh-stack` skill, each PR opened with the `opening-a-pr` skill and agent-reviewed as it lands — → smoke-test phase → final review pass → single-PR work opens its PR now via `opening-a-pr`; stacked work collapses via the `squash-stack` skill → the `landing-a-pr` skill. Every plan carries a `## Hardening ledger` section, empty at the start.

**End-to-end verification plan (required in every plan, not optional).** Every plan carries a `## End-to-end verification plan` section, filled in during planning, not deferred to the smoke-test phase. For each deliverable:

1. **Name the real user-facing (or operator-facing) surface it exercises** — the exact path: browser + real login, the CLI command a human types, the SSH-via-jumphost session a contractor opens, the API call with a real customer token. Not "the database has the row," not "the function returns the right value."
2. **Does tooling already exist to drive that exact surface end-to-end?** Check first — don't assume a gap. If yes, name it (skill, CLI flag, harness) and cite where the implementer finds it.
3. **If no: building that tooling is a task IN THIS PLAN, not a follow-up.** Add it as its own plan unit, scoped as *general and reusable* (a fixture, a CLI subcommand, a skill workflow — not a one-shot script scoped to this PR's exact diff), with its own verification step. A plan that ships the feature and defers "how we'll test it" to "future work" has planned half the feature. This is not new work invented while implementing; it is scope that was always part of the ask, made explicit now instead of discovered late.
4. **Where a deliverable genuinely cannot be exercised outside a shared/expensive resource** (production-only integration, paid third-party sandbox), say so explicitly and name the cheapest real substitute (a Taiga run on a branch build, a staging apply, a Modal run) — never "I read the code" or "the unit tests pass."

The `reviewer` gate reviews this section with the rest of the plan: a plan whose deliverables have no named end-to-end path, or whose verification is a proxy (log presence, code inspection, an internal/admin shortcut standing in for the restricted real path), is rejected back to the `planner` like any other incomplete plan.

**The ledger is the implementer's contract, not a suggestion.** Every implementer dispatch includes: *take the shortcut if it gets the feature working, but log it in the hardening ledger the moment you take it, and return your ledger entries with your report.* You accumulate the entries across subagents. You — the coordinator — run the `opening-a-pr` skill yourself (once for single-PR work; per stacked PR as it lands); at its empty-the-ledger step, dispatch each unpaid entry to `deep` as its own bounded fix task and fold the results in before that PR opens. An implementer who hid a hack instead of logging it has broken the contract; send that task back to `deep` with the gap named.

**Comms:** subagent results auto-deliver when the agent yields — never poll and never idle waiting on one lane while another is dispatchable. A subagent is stalled only when its lane blocks the pipeline and it has produced neither a result nor a question: send it one direct message (in omp: `hub` send to its roster id) stating exactly what you need; if that goes unanswered, cancel it and re-dispatch the task with the gap written into the new prompt. Pass large context by file path, never pasted into dispatch prompts. Load the `using-subagents` skill before your first dispatch for prompt structure.

**Scope and cutover defaults.** Full scope is the default, per ulw-plan:

> - **Full scope is the default.** Plan the ENTIRE request; "MVP", "v1", "phase 1", or any reduced subset is never an option you invent or ask about - it exists only if the user introduces it. Scope OUT / Must-NOT-Have entries are guardrails against unrequested additions, never reductions of the request.

The same default applies to cutovers: unless the user says otherwise, no backwards compatibility, no migration shims, no compatibility re-exports, no deprecated aliases. A migration finishes inside the plan that started it — every caller moved, every old path deleted — not left half-done for a later pass.

**Smoke-test phase.** After implementation and before the final review pass, dispatch an agent to run the actual app, command, or endpoint and exercise every changed user workflow end to end, **using the exact tooling named in the plan's End-to-end verification plan section** — if that tooling doesn't exist because the plan under-scoped it, that is a planning defect to fix now (add the task, build the tooling, then smoke-test), not a reason to substitute a cheaper proxy. Sami's framing: everything is "useful functionality that you actually deliver and can prove works by actually running the app" — and that standard runs through planning and implementation too, not only at this one gate.

The smoke-test phase enumerates every acceptance scenario from the End-to-end verification plan and marks each one `RAN` (with evidence), `WAIVED-BY-SAMI` (with his words), or `BLOCKED` (with the exact blocker). Anything `BLOCKED` stops the pipeline **before** the PR-gate step — it goes to Sami as a pre-merge decision, never forward as a follow-up on a merge-ready claim.

On the planning side, a task's verification step is written as a user-observable outcome, per ce-plan:

> - **Verification** - how an implementer should know the unit is complete, expressed as outcomes rather than shell command scripts

That's alongside, not instead of, writing-plans' discipline of giving exact commands and expected output for each step.

**Final review pass.** Once the smoke test is clean and before the `opening-a-pr` skill runs, dispatch a reviewer with one mandate only: find compatibility shims, re-exports, aliases, dual old/new code paths, half-finished migrations, dead code left behind by the change (unreferenced functions, unused exports, orphaned files), leftover TODO/FIXME markers, and any "v1" / "MVP" / "follow-up" scoping language left in the delivered work. Its findings are must-fix, not noted, before the PR opens.

**Model routing.** Reserve quick/smol/cheap model tiers for the simplest, purely mechanical work — a single-file transcription job, nothing that needs judgment. Agents habitually reach for the cheap tiers anyway and turn in bad work; anything requiring judgment goes to the mapped agents above.

**Multi-PR work.** When the approved plan spans multiple PRs, use the `gh-stack` skill to stack them. Don't stop for Sami's review mid-stack — keep implementing until the whole plan is built out across the stack, with each PR opened and agent-reviewed as it lands. Once the stack is complete and the smoke-test and final review passes are clean, run the `squash-stack` skill: close the base PRs, keep the top PR, re-point it at main, squash the commits, push, and rewrite the PR title and description to describe the consolidated diff. When Sami approves the PR, merge it — never hand an approved PR back to him.
