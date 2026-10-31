---
name: sdd
description: "Use whenever you are building a large project autonomously with subagents — several slices or more than a day of work, especially after a spec or design is approved: plan, implement, test, review, merge in one session. Also when the user asks for SDD or /sdd by name, at any size, and when resuming such a project after a compaction. Not for a small fix nobody asked to run this way, and never in a session the Legion daemon started (LEGION_ROLE is set)."
---

# Subagent-driven development

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

**Stop if the Legion daemon started this session.** Run `printenv LEGION_ROLE`. If it prints a role, you are a per-process Legion phase worker (or an architect) booted from the daemon's `LEGION_*` environment: follow `skill://legion-worker` (or `skill://legion-architect`) and your role prompt, and stop reading here. The daemon runs one phase of the issue at a time under Legion's own skills, so this skill's planning, dispatch, merge and close steps would do work that belongs to other roles. If your assignment asked for this skill, tell the issue's architect with `envoy_publish` to its role topic (`notifications.role.<token>`; your system prompt's `Legion addressing` line names it), as `skill://legion-worker` says for any cross-phase decision.

Where this skill and a stock workflow skill (`subagent-driven-development`, `writing-plans`, `executing-plans`, or another) disagree, follow this skill. Load `subagent-driven-development` and `using-subagents`.

## The mapping, mandatory

| Work | Who |
|---|---|
| Plan | You, with `writing-plans`. When the request has several independent avenues, one `planner` per avenue, and you integrate their plans. |
| Implement, debug, acceptance | `task` with `agent: "deep"` |
| Review plans and PRs | `task` with `agent: "reviewer"` |
| Bounded research or advice | `task` with `agent: "oracle"` (read-only) |

You do not implement. Never hand the plan to another session, put a cheap agent (`sonic`, `scout`, a bare `task`) in a row of this table, or serialize disjoint work: a cheap agent's plan or review reads as confident and misses the decision the work turns on.

## Step 1. Adopt the existing work

1. Find the tracker that answers (Dispatch, GitHub issues, or another the repo uses): a command that exits 0, such as `dispatch issue --help` or `gh issue list`; don't assume one. New work on Dispatch: search for related work first (`dispatch` skill).
2. Read what is settled and what is really built: the issue's goal, the approved design and the changes since approval, the settled decisions and rejected approaches with the place of each, and branch, diff, CI and acceptance state read from the code, not the PR description. The full list, the rule that an approved design stays approved, and what to do with no tracker: `references/adopting.md`.
3. Carry those decisions and rejections, with sources, into the plan and every brief they apply to. A worker without them re-proposes what Sami already rejected.

## Step 2. Classify each slice

Each slice is platform/application, infrastructure, task-authoring or task-queueing work, and each class has its own proof: `references/slice-classes.md`. Read it before the verification plan — proving one class with another's checks ships a slice unverified.

## Step 3. List the skills

List the repo's skill directories (for example `ls .claude/skills .agents/skills ~/.dotfiles/plugins/sjawhar/skills`) and use only names they print; a recalled skill may not exist. Load what planning needs yourself, and pick the design, implementation, testing and infrastructure skills for the rest: the plan's `## Skill catalog` maps each step to the skills its worker must load, and each brief names them (`using-subagents`).

## Step 4. Write the plan

Write the plan's first draft from what you know: after step 1's reading, open no further files until it exists. Then fan subagents out to fill the gaps it names. Research may inform the plan or close a named gap; it never restarts an approved design, and a research brief never asks for a plan. Reuse an existing spec, plan or review: repair the gap in it, then dispatch a `reviewer` on the work the repair affects.

- The plan lives in the issue (its Dispatch document or GitHub issue body), never in a repo file or a PR, whatever `writing-plans` says.
- Plan the full request. Split parallel work into units no two workers edit, and name the shared contracts before dispatch, because two workers who meet a shared interface mid-flight both rewrite it and one loses its work.
- Check every claim about existing code against the artefact it names (constant, schema object, emit site, threshold) and cite file:line. A false claim, implemented faithfully, is the defect. A number you read in a skill file is a claim too: cite the code that sets it.
- **Slices.** Each slice merges as one PR and ends in something the requester can try: a command that runs, a page that renders, a real record that moves. Cut each slice as a thin end-to-end path, never a layer, and order them so the one the requester can use ships first. A slice is a merge and acceptance boundary, not a work assignment: its tasks still split into parallel ownership units, and independent slices run at once. Later slices extend the contracts named up front, never replace them. A slice that only works with scaffolding you would delete later does not ship alone: fold it into the next slice's PR.
- Complete cutovers: migrate every caller and remove obsolete paths, shims, aliases, compatibility exports and dead code, unless Sami requires compatibility.
- The five sections every plan carries, the rule for a second workspace, and how to run a major design change with him: `references/plan-sections.md`.
- A `reviewer` gates every new or materially changed plan before implementation, and checks that the plan meets the issue's goal and every requirement of it, not only that it is buildable. A requirement the plan leaves out fails the review unless the requester recorded that it is out of scope here.

## Step 5. Dispatch the work

Build each brief from the Legion role prompts, fetched at dispatch time, with the slice's full acceptance scenario and the decisions and rejections for its step: `references/briefs.md`, `references/acceptance.md`. A failed fetch blocks the dispatch, with no fallback. Dispatch independent work concurrently and keep unblocked work moving; results arrive as messages, so never sit in `wait` while other work is open.

## Step 6. Acceptance after each slice

Dispatch `deep` acceptance for the slice's outcome once its tasks are merged into its branch; the last slice runs every driver (the script or command that runs a scenario through the real surface) in the plan. The acceptance worker dispatches no reviewer of its own: it writes a failing test for each defect and sends each defect with its test back to the implementer session that built the slice. Mark every scenario `RAN`, `BLOCKED` or `WAIVED-BY-SAMI`, and for a slice with a browser page have the implementer run ce-dogfood first: `references/acceptance.md`.

- `BLOCKED` stops merge readiness until every scenario runs or Sami waives its named blocker. Independent work continues.

## Step 7. Review, then merge

After acceptance, run each step on the branch tip the step before it left: the final `reviewer`, then `ce-simplify-code` once for a runtime change, then you dispatch `thermonuclear-deep-review` and `thermonuclear-code-quality` once at that commit. Then fill the gate ledger from the artifacts at that commit and send the merge packet (`running-the-merge-queue` says what it holds): `references/review-and-merge.md`.

Judge the result against the issue's goal and its requirements, not only the plan's scenarios, and say so in each review brief. A requirement the result does not meet fails the review whether or not the branch, the PR body or a report declares it; it passes only when the requester recorded, before the review, that it would not be done in this piece of work. That record is one of three things: an ask the requester answered, a comment the requester wrote, or a spec version the requester approved that no longer carries the requirement; silence, a reaction, and an agent's own correction of the spec are not records. Moving a missing part to a new issue is not acceptance. The `reviewer` dispatches one subagent whose only job is to red-team the result against the goal and the requirements, and reads its findings before deciding; its verdict names each finding and what it did with it. A verdict with no red-team findings in it is not a verdict: the review is incomplete and the work does not pass.

## Step 8. Keep the issue status current

Move the status at each transition, for the root and every child issue you own; on another tracker keep the equivalent state current at the same points.

| Status | When |
|---|---|
| `in_progress` | implementation starts |
| `testing` | acceptance starts |
| `needs_review` | the PR is open and you sent its merge packet |
| `done` | your own check through the real surface — the command, URL or record a user of this change touches, not a test, a fixture or a log line — shows it working in production |

Every piece of work you own is a child issue of the root before it waits on anything: each PR, follow-up, held slice, adjacent defect found in review, and promise made in a PR, a comment or an Envoy message to another agent. Create it the moment you defer or find it, status `backlog`, with what it waits on in its spec; a step that waits on Sami is an ask on that issue. A local todo list or handoff file may list these issues, and every row in it names an issue key. A green suite or a merge is not `done`. The remaining status rules, the owner audit and producer-contract visibility: `references/review-and-merge.md`.

## Step 9. Finish

Before you report anything done or move an issue to `done`, run `finding-dropped-work` over this session's transcript. Close each row it finds the way that skill says, then report with the row counts it gives.
