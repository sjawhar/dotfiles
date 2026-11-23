---
name: sdd
description: "Use when building a large project autonomously with subagents (several slices or more than a day of work), usually after a spec is approved; when the user asks for SDD or /sdd by name, at any size; or when resuming such a project after a compaction. Not for a small fix."
---

# Subagent-driven development

**Done when:** you have run `finding-dropped-work` over this session's transcript, closed each row it finds as that skill says, and reported its row counts, before you report anything done or move an issue to `done`.

- Where this skill and a stock workflow skill (`subagent-driven-development`, `writing-plans`, `executing-plans` or another) disagree, follow this one.
- Load `subagent-driven-development` and `using-subagents`.

## Who does what

| Work | Who |
| --- | --- |
| Plan | You, with `writing-plans` |
| Plan, when the request has several independent avenues | One `planner` per avenue (`task` with `agent: "planner"`); you integrate their plans |
| Implement, debug, acceptance | `task` with `agent: "deep"` |
| Review plans and PRs | `task` with `agent: "reviewer"` |
| Bounded research or advice | `task` with `agent: "oracle"` (read-only) |

- Do not implement.
- Do not hand the plan to another session.
- Do not serialize disjoint work.
- Put no cheap agent (`sonic`, `scout`, a bare `task`) in these roles: its confident plan or review misses the decision the work turns on.

## 1. Adopt the existing work

1. **Find the tracker** that answers with a command that exits 0, such as `dispatch issue --help` or `gh issue list`.
   - On Dispatch, search for related work first (`dispatch` skill).
   - **None reachable:** say what failed in one line.
   - **None reachable:** work from the repo (checked-in plans and specs, `git log`, the branch diff) and the conversation.
   - **None reachable:** do not skip this step.
   - **None reachable:** do not restart approval.
2. **Read `references/adopting.md` before you plan,** to:
   - read what is settled and what is really built;
   - keep an approved design approved.
3. **Carry the decisions and rejections,** with sources, into the plan and every brief they apply to: a worker without them re-proposes what Sami rejected.

## 2. Classify each slice

Each slice has one class (platform or application, infrastructure, task authoring, task queueing), proven only its own way. Read `references/slice-classes.md` before you write the verification plan: a slice proven with another class's checks ships unverified.

## 3. List the skills

- List the repo's skill directories (for example `ls .claude/skills .agents/skills ~/.dotfiles/plugins/sjawhar/skills`).
- Use only names they print.
- Load what planning needs yourself.
- Pick the design, implementation, testing and infrastructure skills for the rest.
- The plan's `## Skill catalog` maps each step to the skills its worker loads.
- Each brief names the skills its worker loads.
- A step that adds or changes a CI workflow maps `developing-ci`: a workflow written without it runs the whole suite on every push.
  - That step reads `developing-ci` before the first workflow edit, for the same reason.

## 4. Write the plan

- Write the first draft from what step 1 read before you open more files.
- Then fan subagents out to fill the gaps the first draft names.
- Research never restarts an approved design.
- A research brief never asks for a plan.
- Reuse an existing spec, plan or review: repair its gap.
- Then have a `reviewer` check what the repair of a reused spec, plan or review affects.
- The plan lives in the issue (its Dispatch document or GitHub issue body), not in a repo file or a PR.
- Plan the full request.
- Split parallel work into units no two workers edit: two workers who meet an interface mid-flight both rewrite it.
- Name the shared contracts before dispatch, for the same reason.
- Check every claim about existing code against what it names. A number read in a skill is a claim too.
- Cite file:line for every claim about existing code.
- **Read `references/slices.md` now:** what a slice is, what it merges as and ends in, how to cut, order and run slices, and a slice's tasks, contracts and scaffolding.
- Cut over completely: migrate every caller and remove obsolete paths, shims, aliases and dead code, unless Sami requires compatibility.
- Read `references/plan-sections.md` when you write the plan: its sections, workspaces, and a major design change.
- A `reviewer` gates every new plan before implementation.
- A `reviewer` gates every later plan edit, before it is built, that changes a slice, a shared contract, an acceptance scenario or which requirement a step meets.
- That `reviewer` checks the plan against the issue's goal and every requirement, not only whether it can be built.
- A requirement the plan leaves out fails unless the requester recorded it as out of scope.

## 5. Dispatch the work

- Read `references/briefs.md` before the first dispatch.
- Read `references/acceptance.md` for the scenario every brief carries.
- Dispatch independent work at once.
- Keep unblocked work moving.
- Take results as events, never by polling: they arrive as messages.
- Do not sit in `wait` while other work is open, for the same reason.

## 6. Acceptance after each slice

- Once a slice's tasks are merged into its branch, dispatch `deep` acceptance for its outcome.
- The last slice runs every driver in the plan (the script or command that runs a scenario through the real surface).
- Require the acceptance worker to dispatch no reviewer.
- Require the acceptance worker to write a failing test for each defect.
- Require the acceptance worker to send each defect and its test to the implementer session that built the slice.
- Mark every scenario `RAN`, `BLOCKED` or `WAIVED-BY-SAMI` as `references/acceptance.md` defines them.
- A `BLOCKED` scenario holds that slice's merge until every scenario runs or Sami waives its named blocker.
- Independent work continues while a `BLOCKED` scenario holds a slice's merge.

## 7. Review, then merge

Read `references/review-and-merge.md` before the final review.

Run each on the branch tip the one before it left:

1. **The final `reviewer`.**
2. **`ce-simplify-code`** once for a runtime change, scoped to the PR's diff.
   - A change `ce-simplify-code` applies is the new head for CI, review and proof.
3. **`thermonuclear-deep-review` and `thermonuclear-code-quality`** once at that commit.
   - You dispatch the pair because the `reviewer` agent cannot.
   - The final reviewer weighs the pair's findings.

- Fill the gate ledger.
- Send the merge packet (`running-the-merge-queue`).
- **Judge the result against the issue's goal and every requirement,** not only the plan's scenarios.
  - Say so in each review brief.
- What passes, and what the `reviewer`'s verdict must hold: `references/review-and-merge.md`, "Judging the result".

## 8. Keep the issue status current

Read `references/review-and-merge.md` before each status move: it holds the other status rules, the owner audit and producer-contract visibility.

Move the root and every child issue you own at each transition:

| Status | When |
| --- | --- |
| `in_progress` | Implementation starts |
| `testing` | Acceptance starts |
| `needs_review` | Once the PR is open and its merge packet sent |
| `done` | Your own check through the real surface (what a user of the change touches, not a test, fixture or log line) shows it working in production |

- A green suite, a harness screenshot or a merge is groundwork, not `done`.
- On another tracker, keep the same states.
- **Every piece of work you own is a child issue of the root before it waits on anything:** each PR, held slice, defect found in review, and promise you made in a PR, comment or message.
- Create each child issue when you defer or find its work, as `backlog`, with what it waits on in its spec.
- A child issue's wait on Sami is an ask on it.
- Each row of a local todo or handoff file names its issue key.
