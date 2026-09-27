---
name: sdd
description: Execute work via subagent-driven development with Sami's fixed agent mapping (you plan, reviewer gates, deep implements).
disable-model-invocation: true
---

# Subagent-Driven Development

This contract overrides conflicting stock workflow details.

**First action: open the goal.** Call the `goal` tool with `op: "create"` and an objective that starts `[sdd] ` followed by one line naming the work. The post-compaction reminder to re-read this file fires only while such a goal is active, so a session without it loses this contract at its first compaction. Complete the goal (`op: "complete"`) when the change is merged and verified, not before; if the work is handed off, the successor resumes the goal rather than opening another.

Load `subagent-driven-development` and `using-subagents`. The coordinator owns the workflow:

- **Plan:** the coordinator uses `writing-plans`; plan authorship is never delegated.
- **Implement or debug:** dispatch `task` with `agent: "deep"`.
- **Review plans and PRs:** dispatch `task` with `agent: "reviewer"`.
- **Bounded research or advice:** dispatch `task` with `agent: "oracle"`; it is read-only.

This mapping is mandatory: never delegate plan authorship, substitute a cheap tier for a mapped role, or serialize disjoint work.

The coordinator does not implement. Bounded research and advice may inform the plan or resolve a concrete gap, but do not restart an approved design or turn the researcher into a planner. Reuse existing specs, plans, and reviews; repair an actual gap, then re-review the affected work. A `reviewer` gates every new or materially changed plan before implementation.

**Before a reviewer gates the plan, reconcile every statement it makes about EXISTING code against the artefact it names, and cite it at file:line.** Implementing a false statement faithfully produces the defect, which is the reverse of drift. Every instance on 2026-09-27 was checkable against a constant, schema object or emit site that already existed when the plan was written. In one stack, the platform PO's plans said a path already 'adopts ... as an ordinary act' when a CHECK allows a NULL actor for only two triggers, which would fail every apply. They listed a column among results that `NEVER_APP_RLS_RESULT_COLUMNS` forbids by name, which would leak customer data, and defined a flag that would have read `false` for the plan's own headline case (AGENTC-1057). The AGENTC-1034 plan said omp's `input` event fires 'when Sami types anything'. `runner.ts:1693-1694` builds it from the submitted prompt, and the faithful build cost two fix rounds. The same holds for a skill: the hiring skill described a failed screen as 'no hijacking', while `decide.ts:20` and `:61` fail a best ASR of exactly 0.1, strictly above threshold, so the ask would have told Sami a candidate who did hijack had not (chief of staff; fixed in core-ops #183).

## Skill catalog

Planning starts with an explicit skill search. Enumerate the repo's skill directories — list them, never recall from memory — and select per role: skills for designing, for implementing, for testing, and for operating infrastructure. The planner loads the ones planning itself needs, and the plan carries a `## Skill catalog` section mapping each step to the skills its worker is required to load. Every worker brief names its step's catalog entries: a subagent does not inherit the coordinator's loaded skills, so a skill absent from the brief does not exist for that worker (inferred — the dispatch-context rule in `using-subagents`).

## Plan contract

Plan the full request, divide parallel work into disjoint ownership units and name shared contracts before dispatch.

**Where the work happens.** The coordinator uses its starting workspace; each worker uses the workspace named in its task, normally the same tree for sequential steps. Create a workspace only for a disjoint parallel lane: `jj workspace add ~/.worktrees/<repo>/<name> --name <name>` outside a box, or `~/boxes/<box>/<name>` inside one, then lock it from that box using `using-jj`. Never use `/tmp` or a clone. Complete cutovers by migrating every caller and removing obsolete paths, shims, aliases, compatibility exports and dead code unless Sami requires compatibility.

**Major design changes are discussed with Sami on Dispatch.** For architectural changes under `brainstorming`, put the spec on the issue, anchor open questions with `dispatch_ask`, give options and a recommendation, and record his answers as decision provenance. Chat is for execution and bounded designs already being discussed live.

Every plan includes:

- `## Hardening ledger`, initially empty.
- `## End-to-end verification plan`: each deliverable's user-visible outcome, actual production transport/environment, production identity and grant path (including a human grant for human-facing work), existing driver location or a task to build one, and the cheapest genuine shared-resource substitute such as staging or a branch run, never a local simulation of the transport.
- `## Skill catalog`, mapping steps to required worker skills.
- `## Contract change census`: for boundary tightenings defined by `opening-a-pr`, record search commands, every hit's disposition and rollout line after tracing the data's producers, consumers and shared builders across this repository and siblings, including callers that reconstruct values without spelling fields or endpoints; the brief's caller list is not evidence.
- `## Permission source census`: name each new or widened permission, role, scope or capability flag, its real holder in every shipped environment, the granting group/profile/binding/seed, the enforcement point and a test that fails for a permission without a holder; manual grants and test-only identities are not production sources.

Every worker brief must carry the full acceptance scenario, including existing data, the real user and transport, required live or write probes, and a pre-merge run; a coordinator cannot narrow, substitute or defer it without Sami's explicit scope change. If a named corpus is absent or a probe reaches no cases, mark the scenario `BLOCKED` and repair the plan against existing data rather than dropping or narrowing it. Plan verification describes a user-observable outcome; a reviewer rejects missing, proxy-only, or internal-only verification paths.

Track **implemented**, **integrated** and **acceptance-verified** separately; unresolved dependencies do not establish completion.

## Execution and acceptance

**Every worker brief contains the prepared role prompt and the task.** Fetch these parts from Legion's GitHub `main` at dispatch time, never a local copy:

- shared opening for implementer, tester and reviewer: `https://raw.githubusercontent.com/sjawhar/legion/main/packages/pi-envoy/roles/core/common.md`;
- core: `https://raw.githubusercontent.com/sjawhar/legion/main/packages/pi-envoy/roles/core/<role>.md`, selecting by purpose: implementation/debug uses `implementer` (`deep`), acceptance uses `tester` (`deep`), review uses `reviewer` (`reviewer`), research uses `oracle` (`oracle`, without the shared opening); only Legion fetches `planner` because the coordinator plans here;
- mechanics: `https://raw.githubusercontent.com/sjawhar/legion/main/packages/pi-envoy/roles/mechanics/interactive.md`.

Reject non-200 responses and empty bodies:

```bash
fetch_part() {
  url="$1"; out=$(mktemp)
  code=$(curl -sS -o "$out" -w '%{http_code}' "$url") || { echo "role prompt unavailable: $url (curl: $code)" >&2; return 1; }
  [ "$code" = "200" ] || { echo "role prompt unavailable: $url (HTTP $code)" >&2; return 1; }
  [ -s "$out" ] || { echo "role prompt unavailable: $url (HTTP 200, empty body)" >&2; return 1; }
  cat "$out"
}
```

Join the shared opening when applicable, core, mechanics and `# Task` with blank lines. The task names the absolute workspace, step brief, acceptance criteria, allowed bookmark, report path and skill-catalog entries. Preserve fetched text verbatim; a failed fetch blocks dispatch, with no fallback. Changes to prepared prompts are reviewed in Legion. Check commands in briefs use CI's exact recipe from the repo guide or workflow, not a subset.

Dispatch independent work concurrently, consume event-driven results without polling and continue unblocked work. Clarify a genuinely blocking silent worker once, then redispatch only if needed.

After integration, dispatch `deep` acceptance through every driver in the plan. The tester adds no review layers; defects and their red tests return to the resumed implementer.

Record source, dependency and image revisions for every scenario, then mark:

- `RAN`: observation through the real surface.
- `BLOCKED`: failed command and authoritative record, not a person's name; verify another lane's reported blocker against that record.

For a contract census, exercise every human entry point through the actual client and new check; any unrun entry is `BLOCKED`. Build or repair missing infrastructure, tooling, skills or drivers at the tested head and rerun affected scenarios; `BLOCKED` prevents merge readiness until every acceptance scenario runs, while independent work continues. Do not ask Sami to omit acceptance. Iterate on the repo's fastest loop and use its slowest surface for final proof, not each edit. The repository's testing skill defines the rungs and what counts as production-like there.

After acceptance, the final `reviewer` checks correctness, security, dead code, shims, aliases, dual paths and half-migrations. Fix grounded findings before readiness; no other approval or follow-up assignment waives them, and ungrounded preferences are not binding.

Review depth scales to the diff, and the reviewer decides it inside its own pass. For runtime changes, run `ce-simplify-code` once per PR after the last review round, scoped to its diff; any applied change becomes the final head for CI, review and refreshed real-surface proof. The coordinator then dispatches `thermonuclear-deep-review` and `thermonuclear-code-quality` once at that final code head, in sequence with the reviewer, who weighs their findings. The built-in reviewer cannot dispatch the pair. Do not repeat it for prose-only or Minor changes; docs-only/runtime-free diffs get neither simplify nor the pair. Path scope decides the skip, not a claim that changed behavior looks safe; a no-change simplify pass is expected.

**Count each PR's required gates from their artifacts at the packet, never from memory or a summary. Sessions forget and a compaction summary keeps verdicts better than findings.** Three lanes, 2026-09-27. Hiring re-read this contract after a compaction and grepped its ledger for each PR's gate agents. Nine runtime PRs had shipped with reviewer and thermo-deep but no simplify pass, and its recollection of the sequence was wrong. The Legion PO read the verdict comments on a green, fully approved head instead of its notes, and found five non-blocking items destined for that PR that had never been applied. Its compaction summary had carried the verdicts and dropped the items. On another PR, listing the comments turned up no simplify summary at all. The merge queue's status-at-head read flagged that the automated review had posted on an earlier commit, because that review fires on `opened` and `ready_for_review`, never on a push. Keep one ledger row per PR with one cell per required gate, and fill each cell at the packet from the artifact at the head: the comment, the check run, the summary.

The implementer opens each PR under `opening-a-pr` and owns `landing-a-pr`, resumed for checks, reviews and conflicts; the coordinator sends the packet to the merge queue, which merges. Each stacked PR gets the same, and a reviewer as it lands. Use `gh-stack` for dependent PRs and `squash-stack` for completed applicable stacks, refreshing acceptance and PR gates if consolidation changes the delivered diff. Do not pause to ask about stacking.

If a deep worker's bash tool breaks, provide the kernel-hosted shell described in `using-subagents`; that worker has no eval tool. While a PR waits for merge or deployment, continue from its branch or a dependency's branch unless revising the waiting PR; post-merge production proof runs alongside that next work, not instead of it.

## Dispatch status

The coordinator moves the issue's Dispatch status at every transition, the way a human moves a card:
`in_progress` when implementation starts, `testing` when acceptance begins, `needs_review` when the
PR is open and its merge packet is sent, `done` once the change is verified in production by the
coordinator's own check through its real surface — a green suite, a harness screenshot, or a merge is
groundwork, not done. That check is the coordinator's work: never ask Sami, or whoever requested the
change, to look at the result or approve it before moving the issue to `done`, and close it with a reason (what shipped and the check that proved it live; `dispatch_issue_update` refuses `done` without one). An issue
left at `triage` while work is underway is a defect: the roadmap view is read from these statuses,
and Sami has no other way to see delivery without interrupting a session. Do this for child issues
you own as well as the root. Deploy queueing is not a status: a merge that waits for the deploy lane
is still `needs_review`→`done` on its own schedule, and nobody is told about the wait.

**Producer-contract visibility.** When a plan, specification, or implementation changes a producer contract in a way a downstream surface can show, the `needs_review` packet and PR body identify every known downstream surface and its visible delta; the owner of an affected consumer reviews that account. If no consumer is known, record the search that established it. This is a review boundary, not a Sami approval gate.
