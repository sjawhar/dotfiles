---
name: sdd
description: "Use when building a large project autonomously with subagents (several slices or more than a day of work), usually after a spec is approved; when the user asks for SDD or /sdd by name, at any size; or when resuming such a project after a compaction. Not for a small fix."
---

# Subagent-driven development

Where this skill and a stock workflow skill (`subagent-driven-development`, `writing-plans`, `executing-plans`) disagree, follow this one. Load `subagent-driven-development` and `using-subagents`.

## Who does what

- Plan: you, with `writing-plans`. When the request has several independent avenues, one `planner` per avenue; you integrate their plans.
- Implement, debug, acceptance: `task` with `agent: "deep"`.
- Review plans and PRs: `task` with `agent: "reviewer"`.
- Bounded research or advice: `task` with `agent: "oracle"` (read-only).

You do not implement, hand the plan to another session, or serialize disjoint work. Put no cheap agent (`sonic`, `scout`, a bare `task`) in these roles: its confident plan or review misses the decision the work turns on.

## 1. Adopt the existing work

1. Find the tracker that answers with a command that exits 0, such as `dispatch issue --help` or `gh issue list`. On Dispatch, search for related work first (`dispatch` skill). None reachable: say what failed in one line, and work from the repo (checked-in plans, `git log`, the branch diff) and the conversation.
2. Read the issue and its goal; the approved design and what changed since; settled decisions and rejected approaches, with where each was decided; branch, diff, CI, review and acceptance state, checked against the code, not the PR description.
3. An approved design stays approved. Do not restart brainstorming, re-ask an answered question, re-propose a rejected approach or ask for approval again; edits that record his answers do not reopen it. Ask again only when your plan changes what he approved, and say what the approval is for.
4. Carry the decisions and rejections, with sources, into the plan and every brief they apply to: a worker without them re-proposes what Sami rejected.

## 2. Classify each slice

Each slice has one class, proven only its own way:

- Platform or application: the application's own surface.
- Infrastructure: what the repo's own instructions name.
- Task authoring (evaluation tasks, other content the repo ships): running the task.
- Task queueing (content through a submission or QA queue): the state the queue records per item.

Take each class's commands, driver and PR template from the repo's guides, skills and CI. Work that crosses classes exercises each boundary and the join. A repo template asking for another class's checks: follow the change's class and name the mismatch in the plan. No documented workflow: prove it through its consumer's surface (`opening-a-pr`).

## 3. List the skills

List the repo's skill directories (for example `ls .claude/skills .agents/skills ~/.dotfiles/plugins/sjawhar/skills`) and use only names they print. The plan's `## Skill catalog` maps each step to the skills its worker loads, and each brief names them. A step that adds or changes a CI workflow maps `developing-ci`.

## 4. Write the plan

- Write the first draft from what step 1 read before you open more files, then fan subagents out to fill the gaps it names. Research never restarts an approved design, and a research brief never asks for a plan.
- Reuse an existing spec, plan or review: repair its gap, then have a `reviewer` check what the repair affects.
- The plan lives in the issue (its Dispatch document or GitHub issue body), not in a repo file or a PR.
- Plan the full request. Split parallel work into units no two workers edit, and name the shared contracts before dispatch: two workers who meet an interface mid-flight both rewrite it.
- Check every claim about existing code against what it names and cite file:line. A number read in a skill is a claim too.
- Each slice merges as one PR and ends in something the requester can try: a command that runs, a page that renders, a real record that moves. Cut thin end-to-end paths, not layers; ship the first usable one first. A slice is a merge and acceptance boundary, not a work assignment: its tasks still split, and independent slices run at once. Later slices extend the contracts named up front. A slice that needs scaffolding you would later delete merges with the next.
- Cut over completely: migrate every caller and remove obsolete paths, shims, aliases and dead code, unless Sami requires compatibility.
- Read `references/plan-sections.md` when you write the plan: its sections, workspaces, and a major design change.
- A `reviewer` gates every new or materially changed plan before implementation, against the issue's goal and every requirement, not only whether it can be built. A requirement the plan leaves out fails unless the requester recorded it as out of scope.

## 5. Dispatch the work

Read `references/briefs.md` before the first dispatch, and `references/acceptance.md` for the scenario every brief carries. Dispatch independent work at once; results arrive as messages, so do not sit in `wait` while other work is open.

## 6. Acceptance after each slice

Once a slice's tasks are merged into its branch, dispatch `deep` acceptance for its outcome; the last slice runs every driver in the plan (the script or command that runs a scenario through the real surface). The acceptance worker dispatches no reviewer: it writes a failing test for each defect and sends both to the implementer session that built the slice. A `BLOCKED` scenario holds only that slice's merge; other work continues.

## 7. Review, then merge

Run each on the branch tip the one before it left: the final `reviewer`; `ce-simplify-code` once for a runtime change, scoped to the PR's diff (a change it applies is the new head for CI, review and proof); then `thermonuclear-deep-review` and `thermonuclear-code-quality` once at that commit, which you dispatch because the `reviewer` agent cannot, and whose findings the final reviewer weighs. Fill the gate ledger and send the merge packet (`running-the-merge-queue`). Read `references/review-and-merge.md` before the final review.

Judge the result against the issue's goal and every requirement, not only the plan's scenarios, and say so in each review brief:

- An unmet requirement fails the review, whatever the branch, PR body or a report declares, unless the requester recorded before the review that it is out of this work: an ask they answered, a comment they wrote, or a spec version they approved without it. Silence, a reaction or an agent's own spec edit is no record, and moving the missing part to a new issue is not acceptance.
- The `reviewer` dispatches one subagent to red-team the result against the goal and requirements, and its verdict names each red-team finding and what it did with it. A verdict without them is incomplete, and the work does not pass.

## 8. Keep the issue status current

- Move the root and every child issue you own at each transition: `in_progress` when implementation starts, `testing` when acceptance starts, `needs_review` once the PR is open and its merge packet sent, `done` when your own check through the real surface (what a user of the change touches, not a test, fixture or log line) shows it working in production. On another tracker keep the same states.
- Every piece of work you own is a child issue of the root before it waits on anything: each PR, held slice, defect found in review, and promise you made in a PR, comment or message. Create it when you defer or find it, as `backlog`, with what it waits on in its spec; a wait on Sami is an ask on it. Each row of a local todo or handoff file names its issue key.

## 9. Finish

Before you report anything done or move an issue to `done`, run `finding-dropped-work` over this session's transcript, close each row it finds as that skill says, and report its row counts.
