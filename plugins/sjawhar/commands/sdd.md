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
- `BLOCKED` — exact blocker.

There is no waiver state. Verification on a production-like surface is never optional and never
something to ask Sami to skip (Sami, 2026-09-15: "Is there any part of the sdd process that says
it's optional or you can ask to skip it?" — no). `BLOCKED` stops the PR-readiness gate, not
independent work; its resolution is building the missing surface or driver, never an ask. After any
fix, rerun each affected acceptance scenario.

Iterate locally. The local stack (real migrations, real fixtures, the real browser and API) is where
every edit→see→fix loop runs; a dev stack or staging slot is the LAST proof, run once per PR, not a
surface to iterate against. A dev-stack deploy is never the rate limiter; if it is, the missing piece
is a local capability, and building it is part of the work. Anything that runs locally in place of a
production path (a fixture, a stub identity, a local mode) is a drift risk: name it in the plan and
state the check that keeps it faithful to production.

After acceptance, a final `reviewer` examines integrated correctness and security, plus dead code, shims, aliases, dual paths, and half-migrations. Resolve grounded findings before PR readiness; a reviewer preference without a grounded finding is not automatically binding. Do not park a real defect as follow-up work.

The coordinator owns `opening-a-pr`. On every PR open or push, including each stack layer, invoke `landing-a-pr` immediately while independent implementation continues; each stacked PR also receives `opening-a-pr` and a `reviewer` as it lands. Use `gh-stack` autonomously when multiple PRs are needed. Consolidate completed applicable stacks with `squash-stack`; if it changes the delivered diff, refresh affected acceptance and the `opening-a-pr` gate before final readiness. Do not ask whether to stack, state a PR arrangement, or pause mid-stack. Merge only after Sami approves.

A PR waiting in the merge queue or the deploy lane never idles the lane. Unless you are revising that
PR, the next change stacks on its branch (`gh-stack`) and work continues; a dependency on another
lane's open PR is handled the same way, based on their branch. Merging and deploying are never a
reason to wait (Sami, 2026-09-15: "Why is merging a blocker here? ... can't they simply stack on top
of it and keep going?"). The one thing that genuinely waits for the merge is driving the change in
production afterwards, and that runs alongside the next stacked change, not instead of it.

## Dispatch status

The coordinator moves the issue's Dispatch status at every transition, the way a human moves a card:
`in_progress` when implementation starts, `testing` when acceptance begins, `needs_review` when the
PR is open and its merge packet is sent, `done` once the change is verified in production. An issue
left at `triage` while work is underway is a defect: the roadmap view is read from these statuses,
and Sami has no other way to see delivery without interrupting a session. Do this for child issues
you own as well as the root. Deploy queueing is not a status: a merge that waits for the deploy lane
is still `needs_review`→`done` on its own schedule, and nobody is told about the wait.
