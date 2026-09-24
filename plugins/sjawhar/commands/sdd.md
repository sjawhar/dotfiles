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

## Skill catalog

Planning starts with an explicit skill search. Enumerate the repo's skill directories — list them, never recall from memory — and select per role: skills for designing, for implementing, for testing, and for operating infrastructure. The planner loads the ones planning itself needs, and the plan carries a `## Skill catalog` section mapping each step to the skills its worker is required to load. Every worker brief names its step's catalog entries: a subagent does not inherit the coordinator's loaded skills, so a skill absent from the brief does not exist for that worker (inferred — the dispatch-context rule in `using-subagents`).

## Plan contract

Plan the full request. Divide parallel work only into disjoint ownership units and name every shared contract before dispatch.

**Where the work happens.** The coordinator works in the workspace its session started in, and a worker works in the workspace its task block names — normally that same one, since steps run in sequence on one tree. A new workspace exists only for a genuinely disjoint parallel lane, created with `jj workspace add ~/.worktrees/<repo>/<name> --name <name>`; never under `/tmp`, never a clone. Finish every cutover: migrate all callers and remove obsolete paths, shims, aliases, compatibility exports, and dead code unless Sami explicitly requires compatibility.

**Major design changes are brainstormed with Sami on Dispatch, not in the session transcript.** For a major design change — a new subsystem, a workflow restructure, anything the brainstorming skill classifies architectural — the design conversation itself runs on Dispatch: the spec document on the issue, each open question an anchored `dispatch_ask` with options and a recommendation, his answers recorded as the decision provenance. A chat message dies when he scrolls past it; the Dispatch thread is durable and reaches him wherever he is. Chat remains right for execution and for bounded designs he is already discussing live.

Every plan includes:

- `## Hardening ledger`, initially empty.
- `## End-to-end verification plan`, with one scenario per deliverable: the real user/operator surface; the existing end-to-end driver and its location; a reusable driver task when none exists; and, for a required shared or costly resource, the cheapest genuine substitute (for example staging or a branch run).
- `## Skill catalog`, mapping each step to the skills its worker is required to load (see above).
- `## Contract change census`, for any step that tightens a boundary (a new refusal, a newly required field, a removed or renamed field, or a changed signature at a process or package boundary): the search commands, every hit with its planned disposition, and the rollout line, in the form `opening-a-pr` requires of the PR body.

Plan verification describes a user-observable outcome. A reviewer rejects missing, proxy-only, or internal-only verification paths.

Track each work item separately as **implemented**, **integrated**, and **acceptance-verified**. Do not report completion from unresolved dependency evidence.

## Execution and acceptance

**Every worker brief is the role's prepared prompt plus the task.** The prompts live in the legion repository and are read from GitHub at dispatch time, never from a local copy:

- shared opening: `https://raw.githubusercontent.com/sjawhar/legion/main/packages/pi-envoy/roles/core/common.md`, fetched for implementer, tester, and reviewer dispatches (the oracle's core stands alone);
- core: `https://raw.githubusercontent.com/sjawhar/legion/main/packages/pi-envoy/roles/core/<role>.md`, where `<role>` comes from the dispatch's PURPOSE, not its agent tier: an implementation or debug dispatch (`agent: "deep"`) fetches `implementer`; the acceptance dispatch (also `agent: "deep"`) fetches `tester`; a review dispatch (`agent: "reviewer"`) fetches `reviewer`; a research dispatch (`agent: "oracle"`) fetches `oracle`; a delegated planning dispatch does not exist (the coordinator plans), so `planner` is fetched only by Legion;
- mechanics: `https://raw.githubusercontent.com/sjawhar/legion/main/packages/pi-envoy/roles/mechanics/interactive.md`.

Fetch each part with the check that catches both failure shapes (a non-200, and an empty 200):

```bash
fetch_part() {
  url="$1"; out=$(mktemp)
  code=$(curl -sS -o "$out" -w '%{http_code}' "$url") || { echo "role prompt unavailable: $url (curl: $code)" >&2; return 1; }
  [ "$code" = "200" ] || { echo "role prompt unavailable: $url (HTTP $code)" >&2; return 1; }
  [ -s "$out" ] || { echo "role prompt unavailable: $url (HTTP 200, empty body)" >&2; return 1; }
  cat "$out"
}
```

The brief is: the shared opening (when fetched), one blank line, the core, one blank line, the mechanics fragment, one blank line, then a `# Task` block naming the workspace (absolute path), the step's brief file, the acceptance criteria, the bookmark the worker may move, the report-file path, and this step's `## Skill catalog` entries. Never paraphrase, summarise, or edit the fetched text. A failed fetch stops the dispatch — there is no fallback copy of the prompts anywhere, on purpose. A change to what a worker is told is a reviewed commit in the legion repository, and it reaches every session's next dispatch with no change here.

The coordinator names a check command in a brief only as the CI job's exact recipe (the repo `AGENTS.md` Commands section or the workflow file), never a subset: a brief that says `biome lint` where CI runs `bun run lint` (= `biome check`, lint + format) sends a worker to prove the wrong thing "clean".

Dispatch independent work in parallel. Use native, event-driven subagent results; do not poll. Continue other dispatchable work when a lane blocks. Send one direct clarification to a genuinely blocking, silent worker, then re-dispatch only if needed.

After integration, dispatch `task` with `agent: "deep"` for acceptance through each exact driver named in the plan.

The tester dispatches no extra review-agent layers nobody mandated; review belongs to the `reviewer` gate below.

A tester defect returns to the resumed implementer; the tester's red test travels with the resumption.

For every acceptance scenario, record the source, dependency, and image revisions, then mark it:

- `RAN` — real-surface observation;
- `BLOCKED` — exact blocker, stated as the COMMAND that failed and the AUTHORITATIVE RECORD you
  checked, never as a person. Most named blockers dissolve against that record: the answer is
  already in a tag, a PR, or a queue's own behaviour, and the credential nobody needs was never on
  the path. A blocker another lane reports is re-verified, not adopted.

A step with a contract change census carries one more required acceptance scenario: **every human entry point in the census exercised through its real surface**, meaning the client that builds the input, run through the new check. An entry point no scenario ran is `BLOCKED`, not done.

Production-like acceptance verification is mandatory. A missing or blocked surface is work, not a question: build or repair the infrastructure, tooling, skill, or driver at the head under test, then rerun each affected acceptance scenario there. `BLOCKED` stops merge readiness, not independent work; the change is not merge-ready until every scenario has run. Asking Sami whether to omit it is itself the failure.

Iterate on the fastest loop the repository offers; its slowest surface is the once-per-PR proof, never an edit loop. The repository's testing skill defines the rungs and what counts as production-like there.

After acceptance, a final `reviewer` examines integrated correctness and security, plus dead code, shims, aliases, dual paths, and half-migrations. Resolve grounded findings before PR readiness; a reviewer preference without a grounded finding is not automatically binding. Do not park a real defect as follow-up work.

Review depth scales to the diff, and the `reviewer` decides it inside its own pass. Before the pair, the implementer runs `ce-simplify-code` once per pull request at the head where the last review round closed, scoped to the pull request's own diff: nothing applied leaves that head final; applied → the applied head is the final head: CI runs on it, the thermonuclear pair runs once on it, and the E2E proof re-runs on it for the surface the simplify diff touched (Sami, 2026-09-13: test on the real surface before merging, no shortcuts — a refactor that "preserves behaviour" is a claim until it is executed). That cost is why 0-applied is the expected outcome and a pass that applies is spent sparingly. A change to runtime code gets the thermonuclear pair (`thermonuclear-deep-review` + `thermonuclear-code-quality`) once, at the final code head, and never a second time on a push that changed only prose or a Minor. The built-in `reviewer` agent cannot dispatch it (its frontmatter says `spawns: scout`), so the coordinator dispatches the pair at that head, after the last review round and the simplify pass, and hands its findings to the `reviewer`, whose verdict at that head weighs them. The pair runs in sequence with the review, never in parallel beside it. A docs-only or otherwise runtime-free change gets neither pass: no simplify and no thermonuclear pair. The skip is bound to the paths touched — no runtime code in the diff — not to the change "looking safe"; a one-line behaviour change is still a behaviour change.

The implementer opens its own pull request under `opening-a-pr` and owns `landing-a-pr` for it, resumed on each event (a check, a review, a conflict) rather than re-dispatched; each stacked PR gets the same, and a `reviewer` as it lands. The coordinator sends the merge packet to the merge queue, and the queue merges. Use `gh-stack` autonomously when multiple PRs are needed; consolidate completed applicable stacks with `squash-stack`, and if that changes the delivered diff, refresh affected acceptance and the `opening-a-pr` gate before readiness. Do not ask whether to stack, state a PR arrangement, or pause mid-stack.

A PR waiting in the merge queue or the deploy lane never idles the lane. Unless you are revising that
PR, the next change stacks on its branch (`gh-stack`) and work continues; a dependency on another
lane's open PR is handled the same way, based on their branch. Merging and deploying are never a
reason to wait. The one thing that genuinely waits for the merge is driving the change in
production afterwards, and that runs alongside the next stacked change, not instead of it.

## Dispatch status

The coordinator moves the issue's Dispatch status at every transition, the way a human moves a card:
`in_progress` when implementation starts, `testing` when acceptance begins, `needs_review` when the
PR is open and its merge packet is sent, `done` once the change is verified in production AND the
person who asked for it has seen it on the live surface — a green suite, a harness screenshot, or a
merge is groundwork, not done. `done` is a render in front of the requester: work that shipped
without one is invisible to the person who asked, and that costs more trust than the work took. An issue
left at `triage` while work is underway is a defect: the roadmap view is read from these statuses,
and Sami has no other way to see delivery without interrupting a session. Do this for child issues
you own as well as the root. Deploy queueing is not a status: a merge that waits for the deploy lane
is still `needs_review`→`done` on its own schedule, and nobody is told about the wait.

**Producer-contract visibility.** When a plan, specification, or implementation changes a producer contract in a way a downstream surface can show, the `needs_review` packet and PR body identify every known downstream surface and its visible delta; the owner of an affected consumer reviews that account. If no consumer is known, record the search that established it. This is a review boundary, not a Sami approval gate.
