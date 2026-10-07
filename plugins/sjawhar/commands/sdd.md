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

This mapping is mandatory: never delegate plan authorship, substitute a cheap tier for a mapped
role, or serialize disjoint work.

The coordinator does not implement. Bounded research and advice may inform the plan or resolve a
concrete gap, but do not restart an approved design or turn the researcher into a planner. Reuse
existing specs, plans, and reviews; repair an actual gap, then re-review the affected work. A
`reviewer` gates every new or materially changed plan before implementation.

## Adopt the existing work first

Before planning, find out whether the work already exists and adopt it. Use the issue tracker this
environment has: Dispatch, GitHub issues, or whichever tracker the repository and your tools reach.
Check which one answers (a tool that is present, a CLI that authenticates) instead of assuming one.
From the tracker and the repository, read:

- the issue and its goal;
- the approved design or spec, and what changed since the approval;
- answered questions and settled decisions, with where each was decided;
- rejected approaches, and why they were rejected;
- the current code and PR: branch, diff, CI and review state, checked against the code rather than
  the PR description;
- the acceptance items the record shows as unfinished, naming the next one; confirm the items it
  calls done against the code before relying on them.

An approved design stays approved. Do not restart brainstorming, re-ask an answered question,
re-propose a rejected approach, or ask Sami to approve the design again. Edits that record his own
answers or measured corrections do not reopen it. Ask for approval again only when your plan would
change what he approved, and say what the approval is for.

If no tracker is reachable, say so in one line, naming what failed, and ground on the repository
(checked-in plans and specs, `git log`, the branch diff) and the conversation. A missing tracker
never skips this step and never restarts approval.

For new work where Dispatch is available, search for related work before planning or filing, as
the `dispatch` skill's "Search first" section teaches. Related-work search and duplicate grooming
belong to that skill, not to this command.

Carry the adopted decisions and rejections, with their sources, into the plan and into every
worker brief they apply to.

## Choose the applicable workflow

Classify each slice by what it changes before choosing how to verify it:

- platform or application development;
- infrastructure development;
- task authoring (evaluation tasks, or other content the repository ships);
- task queueing (moving finished content through the repository's submission or QA queue).

Take each class's commands, driver, acceptance and PR template from the repository's own
instructions: its AGENTS.md, contributing guide, skills, and the CI that gates those paths. This
command supplies none of them.

A class's checks belong to that class. A web application change is proven on the application's
own surface, not through task runs or a submission queue; a task edit is proven by running the
task, not by walking an application it does not touch; queueing is judged by the state the
repository's queue records for each item. Work that crosses boundaries exercises every boundary it
touches, including the join between them. When a repository template or checklist asks for another
class's checks, follow the class the change actually is and name the mismatch in the plan. Where
the repository documents no workflow for a class, derive the verification from `opening-a-pr`'s
consumer-surface rule.

## Skill catalog

Planning starts with an explicit skill search. Enumerate the repo's skill directories — list them,
never recall from memory — and select per role: skills for designing, for implementing, for
testing, and for operating infrastructure. The planner loads the ones planning itself needs, and
the plan carries a `## Skill catalog` section mapping each step to the skills its worker is
required to load. Every worker brief names its step's catalog entries: a subagent does not inherit
the coordinator's loaded skills, so a skill absent from the brief does not exist for that worker
(the dispatch-context rule in `using-subagents`).

## Plan contract

Plan the full request, divide parallel work into disjoint ownership units and name shared
contracts before dispatch.

**Before a reviewer gates the plan, reconcile every statement it makes about EXISTING code against
the artefact it names, and cite it at file:line.** Implementing a false statement faithfully
produces the defect, which is the reverse of drift. Check each claim against the constant, schema
object, emit site or threshold that already exists; a skill's own description of a threshold is a
claim to check against the code that applies it.

**Ship in slices.** Group the plan's tasks into slices. Each slice merges as one PR and ends in
something the requester can try: a command that runs, a page that renders, a real record that
moves. Cut slices as thin end-to-end paths, not layers, and order them so the first is usable
quickly. A slice is a merge and acceptance boundary, not a work assignment: its tasks still split
into disjoint ownership units that run in parallel, and slices that don't depend on each other run
at the same time. A slice's acceptance is its own user-visible outcome, not the whole feature's.
Later slices build forward on the contracts the plan names up front, extending earlier ones rather
than replacing them; a slice that could only ship with throwaway scaffolding merges into the next
one.

**Where the work happens.** The coordinator uses its starting workspace; each worker uses the
workspace named in its task, normally the same tree for sequential steps. Create a workspace only
for a disjoint parallel lane: clone the repo (`git clone <url> ~/.worktrees/<repo>/<name>` outside
a box, or `~/boxes/<box>/<name>` inside one). Never use
`/tmp`. Complete cutovers by migrating every caller and removing obsolete paths, shims,
aliases, compatibility exports and dead code unless Sami requires compatibility.

**Major design changes are a conversation with Sami on the tracker.** For an architectural change
under `brainstorming`, write the design in the issue's spec (on Dispatch, see the `dispatch`
skill), starting from the problem and its evidence, with sections that follow the topic rather
than a fixed template. Put each open question right after the section that discusses it, as a
decision block (on Dispatch, an ask block) carrying the options, what each costs and your
recommendation, and record each answer there in his words with its date. His answers settle what
they answer. Ask for approval only once every decision block is answered and folded into the
text, and only when the spec still proposes something he has not settled. Say in the request
exactly what those proposals are, never an open question. Without Dispatch, hold the same
conversation on the tracker you have, or in the conversation when none is reachable. Outside
that design conversation, chat is for execution and bounded designs already being discussed live.

Every plan includes:

- `## Hardening ledger`, initially empty.
- `## End-to-end verification plan`: each slice's workflow class and user-visible outcome, actual
  production transport/environment, production identity and grant path (including a human grant
  for human-facing work), existing driver location or a task to build one, and the cheapest
  genuine shared-resource substitute such as staging or a branch run, never a local simulation of
  the transport.
- `## Skill catalog`, mapping steps to required worker skills.
- `## Contract change census`: for boundary tightenings defined by `opening-a-pr`, record search
  commands, every hit's disposition and rollout line after tracing the data's producers, consumers
  and shared builders across this repository and siblings, including callers that reconstruct
  values without spelling fields or endpoints; the brief's caller list is not evidence.
- `## Permission source census`: name each new or widened permission, role, scope or capability
  flag, its real holder in every shipped environment, the granting group/profile/binding/seed, the
  enforcement point and a test that fails for a permission without a holder; manual grants and
  test-only identities are not production sources.

Every worker brief must carry the full acceptance scenario for the slice its task belongs to,
including existing data, the real user and transport, required live or write probes, and a
pre-merge run whose outcome and duration are recorded; a coordinator cannot narrow, substitute or
defer it without Sami's explicit scope change. It also carries the adopted decisions and rejected
approaches that apply to its step, with their sources. If a named corpus is absent or a probe
reaches no cases, mark the scenario `BLOCKED` and repair the plan against existing data rather
than dropping or narrowing it. Plan verification describes a user-observable outcome; a reviewer
rejects missing, proxy-only, or internal-only verification paths.

Track **implemented**, **integrated** and **acceptance-verified** separately; unresolved
dependencies do not establish completion.

## Execution and acceptance

**Every worker brief contains the prepared role prompt and the task.** Fetch these parts from
Legion's GitHub `main` at dispatch time, never a local copy:

- shared opening for implementer, tester and reviewer:
  `https://raw.githubusercontent.com/sjawhar/legion/main/packages/daemon/internal/prompts/roles/core/common.md`;
- core:
  `https://raw.githubusercontent.com/sjawhar/legion/main/packages/daemon/internal/prompts/roles/core/<role>.md`,
  selecting by purpose: implementation/debug uses `implementer` (`deep`), acceptance uses `tester`
  (`deep`), review uses `reviewer` (`reviewer`), research uses `oracle` (`oracle`, without the
  shared opening); only Legion fetches `planner` because the coordinator plans here;
- mechanics:
  `https://raw.githubusercontent.com/sjawhar/legion/main/packages/daemon/internal/prompts/roles/mechanics/interactive.md`.

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

Join the shared opening when applicable, core, mechanics and `# Task` with blank lines. The task
names the absolute workspace, step brief, acceptance criteria, allowed bookmark, report path and
skill-catalog entries. Preserve fetched text verbatim; a failed fetch blocks dispatch, with no
fallback. Changes to prepared prompts are reviewed in Legion. Check commands in briefs use CI's
exact recipe from the repo guide or workflow, not a subset.

Dispatch independent work concurrently, consume event-driven results without polling and continue
unblocked work. Clarify a genuinely blocking silent worker once, then redispatch only if needed.

After each slice integrates, dispatch `deep` acceptance for that slice's outcome; the last slice
runs every driver in the plan. The tester adds no review layers; defects and their red tests
return to the resumed implementer. Every slice that alters what runs has its affected end-to-end
scenarios run before merge, under `opening-a-pr`'s pre-merge scenario rule.

Record source, dependency and image revisions for every scenario, then mark:

- `RAN`: observation through the real surface, with its outcome and duration.
- `BLOCKED`: failed command and authoritative record, not a person's name; verify another lane's
  reported blocker against that record.
- `WAIVED-BY-SAMI`: a named blocker Sami explicitly waived, linked to his words; it covers that
  blocker on that change only.

For a contract census, exercise every human entry point through the actual client and new check;
any unrun entry is `BLOCKED`. Build or repair missing infrastructure, tooling, skills or drivers at
the tested head and rerun affected scenarios; `BLOCKED` prevents merge readiness until every
acceptance scenario runs or Sami waives its named blocker, while independent work continues. Never
ask Sami to omit acceptance or to accept a proxy. A named blocker that survives building or
repairing the surface goes to him as a pre-merge decision; his explicit waiver of that blocker is
the only exception, and the change's size, its kind and green CI never are. Iterate on the repo's
fastest loop and use its slowest surface for final proof, not each edit. The repository's testing
skill defines the rungs and what counts as production-like there.

After acceptance, the final `reviewer` checks correctness, security, dead code, shims, aliases,
dual paths and half-migrations. Fix grounded findings before readiness; no other approval or
follow-up assignment waives them, and ungrounded preferences are not binding.

Review depth scales to the diff, and the reviewer decides it inside its own pass. For runtime
changes, run `ce-simplify-code` once per PR after the last review round, scoped to its diff; any
applied change becomes the final head for CI, review and refreshed real-surface proof. The
coordinator then dispatches `thermonuclear-deep-review` and `thermonuclear-code-quality` once at
that final code head, in sequence with the reviewer, who weighs their findings. The built-in
reviewer cannot dispatch the pair. Do not repeat it for prose-only or Minor changes;
docs-only/runtime-free diffs get neither simplify nor the pair. Path scope decides the skip, not a
claim that changed behavior looks safe; a no-change simplify pass is expected.

**Count each PR's required gates from their artifacts at the packet, never from memory or a
summary.** Sessions forget, and a compaction summary keeps verdicts better than findings: gates can
be skipped, non-blocking items destined for the PR can stay unapplied under a fully approved head,
and an automated review can have posted on an earlier commit. Keep one ledger row per PR with one
cell per required gate, and fill each cell at the packet from the artifact at the head: the
comment, the check run, the summary.

The implementer opens each PR under `opening-a-pr` and owns `landing-a-pr`, resumed for checks,
reviews and conflicts; the coordinator sends the packet to the merge queue, which merges. Each
stacked PR gets the same, and a reviewer as it lands. Use `gh-stack` for dependent PRs and
`squash-stack` for completed applicable stacks, refreshing acceptance and PR gates if
consolidation changes the delivered diff. Do not pause to ask about stacking.

If a deep worker's bash tool breaks, provide the kernel-hosted shell described in
`using-subagents`; that worker has no eval tool. While a PR waits for merge or deployment,
continue from its branch or a dependency's branch unless revising the waiting PR; post-merge
production proof runs alongside that next work, not instead of it.

## Issue status

When the issue lives in Dispatch, the coordinator moves its status at every transition, the way a
human moves a card: `in_progress` when implementation starts, `testing` when acceptance begins,
`needs_review` when the PR is open and its merge packet is sent, `done` once the change is
verified in production by the coordinator's own check through its real surface — a green suite, a
harness screenshot, or a merge is groundwork, not done. That check is the coordinator's work:
never ask Sami, or whoever requested the change, to look at the result or approve it before moving
the issue to `done`, and close it with a reason (what shipped and the check that proved it live;
`dispatch_issue_update` refuses `done` without one). An issue left at `triage` while work is
underway is a defect: the roadmap view is read from these statuses, and Sami has no other way to
see delivery without interrupting a session. Do this for child issues you own as well as the root.
Deploy queueing is not a status: a merge that waits for the deploy lane is still
`needs_review`→`done` on its own schedule, and nobody is told about the wait. At each of these
checkpoints, run the `dispatch` skill's owner audit over the project you own. On another tracker,
keep its equivalent state current at the same transitions.

**Producer-contract visibility.** When a plan, specification, or implementation changes a producer
contract in a way a downstream surface can show, the `needs_review` packet and PR body identify
every known downstream surface and its visible delta; the owner of an affected consumer reviews
that account. If no consumer is known, record the search that established it. This is a review
boundary, not a Sami approval gate.
