---
name: sdd
description: Execute work via subagent-driven development with Sami's fixed agent mapping (you plan, reviewer gates, deep implements).
disable-model-invocation: true
---

# Subagent-Driven Development (Sami's mapping)

Load the `subagent-driven-development` skill and execute the given work through it with this fixed agent mapping:

- **Planning** → **you**, in this session, with the `writing-plans` skill. You hold the brainstorm, the spec, and every decision Sami made getting here; a fresh planner starts from zero and spends an hour re-reading the codebase to recover context you already have. Planning is the one job where your context is the asset, not the cost.
- **Implementation and debugging** → `task` with `agent: "deep"`
- **Plan and PR review** → `task` with `agent: "reviewer"` (built-in; returns a verdict plus findings)
- **Strategy consults** (architecture calls, gnarly tradeoffs, stuck debugging) → `task` with `agent: "oracle"` (read-only)

You are the coordinator: write the plan, dispatch, verify results file-by-file, integrate, and manage the backlog. **Do not implement yourself** — burning your own context on implementation instead of orchestrating is the failure mode this command exists to prevent.

**Parallelize aggressively.** Structure the plan itself to maximize independent tasks; dispatch independent tasks in the same turn; a blocked lane never idles the pipeline.

Pipeline: you write the plan (writing-plans skill) → `reviewer` gates the plan → subagents implement — a multi-PR plan stacks via the `gh-stack` skill, each PR opened with the `opening-a-pr` skill and agent-reviewed as it lands — → acceptance run → final review pass → single-PR work opens its PR now via `opening-a-pr`; stacked work collapses via the `squash-stack` skill → the `landing-a-pr` skill. Every plan carries a `## Hardening ledger` section, empty at the start.

**End-to-end verification plan (required in every plan, not optional).** Every plan carries a `## End-to-end verification plan` section, filled in during planning, not deferred to the acceptance run. For each deliverable:

1. **Name the real user-facing (or operator-facing) surface it exercises** — the exact path: browser + real login, the CLI command a human types, the SSH-via-jumphost session a contractor opens, the API call with a real customer token. Not "the database has the row," not "the function returns the right value."
2. **Does tooling already exist to drive that exact surface end-to-end?** Check first — don't assume a gap. If yes, name it (skill, CLI flag, harness) and cite where the implementer finds it.
3. **If no: building that tooling is a task IN THIS PLAN, not a follow-up.** Add it as its own plan unit, scoped as *general and reusable* (a fixture, a CLI subcommand, a skill workflow — not a one-shot script scoped to this PR's exact diff), with its own verification step. A plan that ships the feature and defers "how we'll test it" to "future work" has planned half the feature. This is not new work invented while implementing; it is scope that was always part of the ask, made explicit now instead of discovered late.
4. **Where a deliverable genuinely cannot be exercised outside a shared/expensive resource** (production-only integration, paid third-party sandbox), say so explicitly and name the cheapest real substitute (a Taiga run on a branch build, a staging apply, a Modal run) — never "I read the code" or "the unit tests pass."

The `reviewer` gate reviews this section with the rest of the plan: a plan whose deliverables have no named end-to-end path, or whose verification is a proxy (log presence, code inspection, an internal/admin shortcut standing in for the restricted real path), is rejected back to you like any other incomplete plan — fix it and re-gate.

**The ledger is the implementer's contract, not a suggestion.** Every implementer dispatch includes: *take the shortcut if it gets the feature working, but log it in the hardening ledger the moment you take it, and return your ledger entries with your report.* You accumulate the entries across subagents. You — the coordinator — run the `opening-a-pr` skill yourself (once for single-PR work; per stacked PR as it lands); at its empty-the-ledger step, dispatch each unpaid entry to `deep` as its own bounded fix task and fold the results in before that PR opens. An implementer who hid a hack instead of logging it has broken the contract; send that task back to `deep` with the gap named.

**Every implementer dispatch also carries the evidence contract.** The `deep` agent's own definition covers implementation, not this workflow's acceptance bar, so the bar travels in the dispatch — spell it out every time, in the dispatch text:

> Before you report, drive the thing you changed through the surface a real user or operator touches — the command a human types, the page they load after a real login, the session they open — and tell me what you observed. Running the test suite, the typechecker, or the linter is not that, and neither is an ad-hoc script or a `python -c` against internals: those exercise your own assumptions, not the product. If nothing exists to drive the real surface, building it is part of this task (a reusable fixture or command, not a one-off for this diff) — say so in your report. If you genuinely cannot reach the surface, name the blocker; never write "verified by inspection".

An implementer report whose evidence is only green checks is incomplete: send it back with the surface named, the same way you would a hidden hack.

**Comms:** subagent results auto-deliver when the agent yields — never poll and never idle waiting on one lane while another is dispatchable. A subagent is stalled only when its lane blocks the pipeline and it has produced neither a result nor a question: send it one direct message (in omp: `hub` send to its roster id) stating exactly what you need; if that goes unanswered, cancel it and re-dispatch the task with the gap written into the new prompt. Pass large context by file path, never pasted into dispatch prompts. Load the `using-subagents` skill before your first dispatch for prompt structure.

**Scope and cutover defaults.** Full scope is the default, per ulw-plan:

> - **Full scope is the default.** Plan the ENTIRE request; "MVP", "v1", "phase 1", or any reduced subset is never an option you invent or ask about - it exists only if the user introduces it. Scope OUT / Must-NOT-Have entries are guardrails against unrequested additions, never reductions of the request.

The same default applies to cutovers: unless the user says otherwise, no backwards compatibility, no migration shims, no compatibility re-exports, no deprecated aliases. A migration finishes inside the plan that started it — every caller moved, every old path deleted — not left half-done for a later pass.

**Acceptance run.** After implementation and before the final review pass, dispatch an agent to *use the thing*: boot the actual app, command, or endpoint and drive every changed workflow the way the person who asked for it would drive it, **using the exact tooling named in the plan's End-to-end verification plan section**. If that tooling doesn't exist because the plan under-scoped it, that is a planning defect to fix now — add the task, build the tooling, then run the acceptance pass — never a reason to substitute a cheaper proxy.

**This phase is deliberately not called a smoke test.** A smoke test, in its ordinary meaning and in agent-c's own CI, is a shallow liveness check — does it come up, does the endpoint answer — and that shallowness is exactly the loophole: `pytest`, an `--help` invocation, or an import check all satisfy "smoke test" honestly while proving nothing about the feature. Keep "smoke test" for the cheap staging and CI gates that genuinely are one. This phase is the opposite: the full user path, observed. Nor is it "user acceptance testing" in the formal sense — that means the *user* signs off, which an agent cannot do on Sami's behalf; this is the agent producing the evidence Sami would need to sign off.

The acceptance run enumerates every scenario from the End-to-end verification plan and marks each one `RAN` (with what was observed, not the command that was typed), `WAIVED-BY-SAMI` (with his words), or `BLOCKED` (with the exact blocker). Anything `BLOCKED` stops the pipeline **before** the PR-gate step — it goes to Sami as a pre-merge decision, never forward as a follow-up on a merge-ready claim. A scenario marked `RAN` whose evidence is a test-suite result, a typechecker result, or a CI link is not `RAN`; mark it `BLOCKED` and say what stopped you from reaching the surface.

On the planning side, a task's verification step is written as a user-observable outcome, per ce-plan:

> - **Verification** - how an implementer should know the unit is complete, expressed as outcomes rather than shell command scripts

That's alongside, not instead of, writing-plans' discipline of giving exact commands and expected output for each step.

**Final review pass.** Once the smoke test is clean and before the `opening-a-pr` skill runs, dispatch a reviewer with one mandate only: find compatibility shims, re-exports, aliases, dual old/new code paths, half-finished migrations, dead code left behind by the change (unreferenced functions, unused exports, orphaned files), leftover TODO/FIXME markers, and any "v1" / "MVP" / "follow-up" scoping language left in the delivered work. Its findings are must-fix, not noted, before the PR opens.

**Model routing.** Reserve quick/smol/cheap model tiers for the simplest, purely mechanical work — a single-file transcription job, nothing that needs judgment. Agents habitually reach for the cheap tiers anyway and turn in bad work; anything requiring judgment goes to the mapped agents above.

**Multi-PR work.** When the approved plan spans multiple PRs, use the `gh-stack` skill to stack them. Don't stop for Sami's review mid-stack — keep implementing until the whole plan is built out across the stack, with each PR opened and agent-reviewed as it lands. Once the stack is complete and the smoke-test and final review passes are clean, run the `squash-stack` skill: close the base PRs, keep the top PR, re-point it at main, squash the commits, push, and rewrite the PR title and description to describe the consolidated diff. When Sami approves the PR, merge it — never hand an approved PR back to him.
