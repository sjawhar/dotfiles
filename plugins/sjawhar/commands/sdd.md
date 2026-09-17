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

## Skill catalog

Planning starts with an explicit skill search (Sami, 2026-09-16, verbatim: "an explicit step where the planner searches for all of the relevant skills in the repo that each downstream step should use"). Enumerate the repo's skill directories — list them, never recall from memory — and select per role: skills for designing, for implementing, for testing, and for operating infrastructure. The planner loads the ones planning itself needs, and the plan carries a `## Skill catalog` section mapping each step to the skills its worker is required to load (Sami, 2026-09-16, verbatim: "the planner should explicitly tell each step to have a catalog of the relevant skills that they're required to load"). Every worker brief names its step's catalog entries: a subagent does not inherit the coordinator's loaded skills, so a skill absent from the brief does not exist for that worker (inferred — the dispatch-context rule in `using-subagents`).

## Read the source first

Before any design that touches Inspect (`inspect_ai`, `inspect_swe`, `inspect_scout`, `inspect_k8s_sandbox`, `inspect_sandboxes`), Hawk, middleman, or the bridge between them, the planner and every `oracle` it dispatches read the actual code paths the design will run against (Sami, 2026-09-17, verbatim: "we really need agents to actually fucking read inspect and hawk code before designing things"). The code is the fork checkout `knives repos` names or the package the venv installs — `uv run python -c 'import inspect_ai, pathlib; print(pathlib.Path(inspect_ai.__file__).parent)'` for Inspect, and for hawk the source checkout `uv` keeps at the commit `uv.lock` resolved (`~/.cache/agent-c/hawk-src/<commit>/`, or `import hawk` the same way where it is installed) — never upstream's default branch, which describes almost what we run (inferred from `docs/architecture/two-harnesses.md`). The design doc cites `file:line` for every claim about how those systems behave; a claim without a citation is an assumption and is written as one, in that word (inferred: the rule that makes the reading checkable). A consumer's behaviour is read from the consumer's config, not only the library's source — hawk's `EvalSetConfig`, not Inspect's defaults (inferred from the `using-subagents` rule and the five-raw-calls misread behind it). `docs/architecture/facts.md` is read first as the list of known misreads, and a design that contradicts one of them says so and cites the code that changed (inferred: that doc exists for exactly this step). The `reviewer` rejects a plan whose claims about these systems carry no citations, the same way it rejects proxy-only verification (inferred from the plan contract below).

## Plan contract

Plan the full request. Divide parallel work only into disjoint ownership units and name every shared contract before dispatch. Finish every cutover: migrate all callers and remove obsolete paths, shims, aliases, compatibility exports, and dead code unless Sami explicitly requires compatibility.

Every plan includes:

- `## Hardening ledger`, initially empty.
- `## End-to-end verification plan`, with one scenario per deliverable: the real user/operator surface; the existing end-to-end driver and its location; a reusable driver task when none exists; and, for a required shared or costly resource, the cheapest genuine substitute (for example staging or a branch run).
- `## Skill catalog`, mapping each step to the skills its worker is required to load (see above).

Plan verification describes a user-observable outcome. A reviewer rejects missing, proxy-only, or internal-only verification paths.

Every worker brief requires all of:

1. **Shortcut ledger:** log each shortcut immediately in the hardening ledger and return its entries. Group repayment by root cause and file ownership, while preserving the resolution of every entry; the ledger is empty before the coordinator's PR gate.
2. **Real-surface evidence:** drive the named user/operator path and report what was observed. A pytest fixture qualifies only when it drives the real product path. Green counts, internal shortcuts, substituted implementations, unit-only checks, and code inspection do not qualify.
3. **Required skills:** the step's entries from the plan's `## Skill catalog`, loaded before work starts.

Track each work item separately as **implemented**, **integrated**, and **acceptance-verified**. Do not report completion from unresolved dependency evidence.

## Execution and acceptance

Dispatch independent work in parallel. Use native, event-driven subagent results; do not poll. Continue other dispatchable work when a lane blocks. Send one direct clarification to a genuinely blocking, silent worker, then re-dispatch only if needed.

After integration, dispatch `task` with `agent: "deep"` for acceptance through each exact driver named in the plan.

The acceptance dispatch is the tester, and it starts skeptical: the work is broken until the tester proves otherwise on the real surface (Sami, 2026-09-16, verbatim: the tester needs "a strong skeptical assumption that it's broken until proven otherwise"). Acceptance means driving the changed surface end to end, climbing the repo's smoke-testing/verification ladder where one exists — unit tests, type checks, and reading the diff are not acceptance (inferred from the 2026-09-16 tester audit: 79.9% of 4,282 tester-population dispatches never touched a running surface, and 51.7% ran no verification command at all). The tester dispatches no extra review-agent layers nobody mandated — the same audit found an un-mandated thermonuclear-* layer on 63% of those dispatches; review belongs to the `reviewer` gate below (inferred).

When the tester finds a defect, the tester writes the failing red test itself, in the repo tree, and hands it to the resumed implementer, who makes it pass and may not change that test (Sami, 2026-09-16, verbatim: the tester "[s]hould write the red test, but then the implementer, when resumed, needs to make a pass. The implementer should be given strong guidance not to change that test that the tester wrote"). Mark tester-authored tests as tester-authored in the handoff; an implementer weakening, rewriting, or deleting one is a ledger entry, never a quiet fix.

For every acceptance scenario, record the source, dependency, and image revisions, then mark it:

- `RAN` — real-surface observation;
- `BLOCKED` — exact blocker, stated as the COMMAND that failed and the AUTHORITATIVE RECORD you
  checked, never as a person. Four blockers on 2026-09-16 turned out not to bind once checked: a
  lane "blocked on Ryan's reply" when the Taiga RfC tags and the Lyon PRs already held the answer;
  a `gcloud auth` ask when we never pull from Taiga's registry; a "cancelled deploy" that was the
  queue coalescing; an SSO ask another lane inherited after its own profile had expired. A blocker
  another lane reports is re-verified, not adopted. Sami, 2026-09-17: "nothing here needed Ryan".

There is no waiver state. Verification on a production-like surface is never optional and never
something to ask Sami to skip (Sami, 2026-09-15: "Is there any part of the sdd process that says
it's optional or you can ask to skip it?" — no). `BLOCKED` stops the PR-readiness gate, not
independent work; its resolution is building the missing surface or driver, never an ask. After any
fix, rerun each affected acceptance scenario.

Iterate locally. The local stack (real migrations, real fixtures, the real browser and API) is where
every edit→see→fix loop runs; a dev stack or staging slot is the LAST proof, run once per PR, not a
surface to iterate against. That proof runs against a slot YOU applied at the head under test: a gate
run against a slot deployed from someone else's branch compares your tree with their build and reds
on their schema (dev2, 2026-09-15: a 55-field parquet diff that was one column from another lane's
merge). A dev-stack deploy is never the rate limiter; if it is, the missing piece is a local
capability, and building it is part of the work. Anything that runs locally in place of a production
path (a fixture, a stub identity, a local mode) is a drift risk: name it in the plan and state the
check that keeps it faithful to production.

After acceptance, a final `reviewer` examines integrated correctness and security, plus dead code, shims, aliases, dual paths, and half-migrations. Resolve grounded findings before PR readiness; a reviewer preference without a grounded finding is not automatically binding. Do not park a real defect as follow-up work.

Review depth scales to the diff, and the `reviewer` decides it inside its own pass. A change to runtime code gets the thermonuclear pair (`thermonuclear-deep-review` + `thermonuclear-code-quality`) once, at the final code head, dispatched by the reviewer as part of its review — never bolted on in parallel by the coordinator, and never a second time on a push that changed only prose or a Minor (Sami, #1004, 2026-09-09: "the reviewer is the one that should be running the Thermo deep in quality review. If everything else passes, it should run that"; #598, 2026-09-08: "I don't care about replying to every tiny little minor after every single push. We have to use some discretion here"). A docs-only or otherwise runtime-free change gets no thermonuclear pair at all (#597, 2026-09-08: "we don't need thermonuclear review on a docs-only PR"). The skip is bound to the paths touched — no runtime code in the diff — not to the change "looking safe"; a one-line behaviour change is still a behaviour change. The 2026-09-16 dispatch audit found 63% of tester/reviewer dispatches carrying review agents this contract never names (claudemd-miner #5); that layer is what this paragraph removes.

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
PR is open and its merge packet is sent, `done` once the change is verified in production AND the
person who asked for it has seen it on the live surface — a green suite, a harness screenshot, or a
merge is groundwork, not done. Three things shipped invisibly on 2026-09-16 (the searchable
multi-select asked for three times while the component existed; the red-teamer viewer live a day
before the people who asked were told; a P0 close-out whose `done` the PO missed for two hours) and
each cost more trust than the work took. `done` is a render in front of the requester. An issue
left at `triage` while work is underway is a defect: the roadmap view is read from these statuses,
and Sami has no other way to see delivery without interrupting a session. Do this for child issues
you own as well as the root. Deploy queueing is not a status: a merge that waits for the deploy lane
is still `needs_review`→`done` on its own schedule, and nobody is told about the wait.
