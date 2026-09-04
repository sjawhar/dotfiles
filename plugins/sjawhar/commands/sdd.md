---
name: sdd
description: Execute work via subagent-driven development with Sami's fixed agent mapping (you plan, reviewer gates, deep implements).
disable-model-invocation: true
---

# Subagent-Driven Development

This contract overrides conflicting stock workflow details.

Load `subagent-driven-development` and `using-subagents`. The coordinator owns the workflow:

- **Plan:** the coordinator uses `writing-plans`; plan authorship is never delegated.
- **Implement or debug:** dispatch `task` with `agent: "deep"`.
- **Review plans and PRs:** dispatch `task` with `agent: "reviewer"`.
- **Bounded research or advice:** dispatch `task` with `agent: "oracle"`; it is read-only.

This mapping is mandatory: never delegate plan authorship, substitute a cheap tier for a mapped role, or serialize disjoint work.

The coordinator does not implement. Bounded research and advice may inform the plan or resolve a concrete gap, but do not restart an approved design or turn the researcher into a planner. Reuse existing specs, plans, and reviews; repair an actual gap, then re-review the affected work. A `reviewer` gates every new or materially changed plan before implementation.

## Plan contract

Plan the full request. Divide parallel work only into disjoint ownership units and name every shared contract before dispatch. Finish every cutover: migrate all callers and remove obsolete paths, shims, aliases, compatibility exports, and dead code unless Sami explicitly requires compatibility.

Every plan includes:

- `## Hardening ledger`, initially empty.
- `## End-to-end verification plan`, with one scenario per deliverable: the real user/operator surface; the existing end-to-end driver and its location; a reusable driver task when none exists; and, for a required shared or costly resource, the cheapest genuine substitute (for example staging or a branch run).

Plan verification describes a user-observable outcome. A reviewer rejects missing, proxy-only, or internal-only verification paths.

Every worker brief requires both:

1. **Shortcut ledger:** log each shortcut immediately in the hardening ledger and return its entries. Group repayment by root cause and file ownership, while preserving the resolution of every entry; the ledger is empty before the coordinator's PR gate.
2. **Real-surface evidence:** drive the named user/operator path and report what was observed. A pytest fixture qualifies only when it drives the real product path. Green counts, internal shortcuts, substituted implementations, unit-only checks, and code inspection do not qualify.

Track each work item separately as **implemented**, **integrated**, and **acceptance-verified**. Do not report completion from unresolved dependency evidence.

## Execution and acceptance

Dispatch independent work in parallel. Use native, event-driven subagent results; do not poll. Continue other dispatchable work when a lane blocks. Send one direct clarification to a genuinely blocking, silent worker, then re-dispatch only if needed.

After integration, dispatch `task` with `agent: "deep"` for acceptance through each exact driver named in the plan.

For every acceptance scenario, record the source, dependency, and image revisions, then mark it:

- `RAN` — real-surface observation;
- `WAIVED-BY-SAMI` — Sami's exact waiver;
- `BLOCKED` — exact blocker.

`BLOCKED` stops the PR-readiness gate, not independent work. Resolve or obtain a waiver before readiness; after any fix, rerun each affected acceptance scenario.

After acceptance, a final `reviewer` examines integrated correctness and security, plus dead code, shims, aliases, dual paths, and half-migrations. Resolve grounded findings before PR readiness; a reviewer preference without a grounded finding is not automatically binding. Do not park a real defect as follow-up work.

The coordinator owns `opening-a-pr`. On every PR open or push, including each stack layer, invoke `landing-a-pr` immediately while independent implementation continues; each stacked PR also receives `opening-a-pr` and a `reviewer` as it lands. Use `gh-stack` autonomously when multiple PRs are needed. Consolidate completed applicable stacks with `squash-stack`; if it changes the delivered diff, refresh affected acceptance and the `opening-a-pr` gate before final readiness. Do not ask whether to stack, state a PR arrangement, or pause mid-stack. Merge only after Sami approves.
