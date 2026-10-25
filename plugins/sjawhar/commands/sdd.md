---
name: sdd
description: Run work as subagent-driven development with a mandatory agent mapping: the coordinator plans, `reviewer` gates, `deep` implements and tests.
disable-model-invocation: true
---

# Subagent-Driven Development

This contract overrides conflicting stock workflow details. Load `subagent-driven-development` and `using-subagents`.

## Roles

| Work | Who |
|---|---|
| Plan | The coordinator, with `writing-plans`. For several independent avenues, one `planner` each, and the coordinator integrates their plans. |
| Implement, debug, acceptance | `task` with `agent: "deep"` |
| Review plans and PRs | `task` with `agent: "reviewer"` |
| Bounded research or advice | `task` with `agent: "oracle"` (read-only) |

- The mapping is mandatory. Never hand the plan to another session, put a cheaper tier in a mapped role, or serialize disjoint work.
- The coordinator does not implement.
- The plan lives in the issue (its Dispatch document or GitHub issue body), never in a repo file or a PR, whatever `writing-plans` says.
- Write the plan's skeleton from what you know first, then fan subagents out to fill the gaps it names. Don't read before a first draft exists.
- Research may inform the plan or close a concrete gap. It never restarts an approved design, and the researcher never becomes a planner.
- Reuse existing specs, plans and reviews. Repair an actual gap, then re-review the work it affects.
- A `reviewer` gates every new or materially changed plan before implementation.

## 1. Adopt the existing work

1. Before planning, find the tracker that answers (Dispatch, GitHub issues, other): a tool that is present, a CLI that authenticates. Don't assume one.
2. From the tracker and the repo, read:
   - the issue and its goal.
   - the approved design or spec, and what changed since approval.
   - answered questions and settled decisions, with the place of each decision.
   - rejected approaches, and why.
   - branch, diff, CI and review state, checked against the code, not the PR description.
   - unfinished acceptance items, naming the next one. Check items marked done against the code.
3. Carry adopted decisions and rejections, with sources, into the plan and every worker brief they apply to.

- An approved design stays approved. Don't restart brainstorming, re-ask an answered question, re-propose a rejected approach, or ask Sami to approve it again.
- Edits that record his answers or measured corrections don't reopen it. Ask again only when your plan changes what he approved, and say what the approval is for.
- No tracker reachable: say so in one line, naming what failed. Ground on the repo (checked-in plans and specs, `git log`, branch diff) and the conversation. Never skip this step or restart approval.
- New work on Dispatch: search for related work first, per the `dispatch` skill.

## 2. Classify each slice

| Class | Proven by |
|---|---|
| Platform or application development | the application's own surface |
| Infrastructure development | what the repo's own instructions name (below) |
| Task authoring (evaluation tasks, other content the repo ships) | running the task |
| Task queueing (finished content through the repo's submission or QA queue) | the state the queue records per item |

- Take each class's commands, driver, acceptance and PR template from the repo: AGENTS.md, contributing guide, skills, the CI that gates those paths. This command supplies none.
- Never prove one class with another class's checks. Work that crosses boundaries exercises every boundary it touches and the join between them.
- A repo template asks for another class's checks: follow the class the change is, and name the mismatch in the plan.
- No documented workflow for a class: derive verification from the consumer-surface rule in `opening-a-pr`.

## 3. Skill catalog

- List the repo's skill directories. Never recall them. Select skills for design, implementation, testing and infrastructure operations.
- The planner loads what planning needs. The plan's `## Skill catalog` maps each step to the skills its worker must load, and each brief names them (`using-subagents`).

## 4. Plan contract

- Plan the full request. Split parallel work into disjoint ownership units and name shared contracts before dispatch.
- Before the reviewer gate, check every claim about existing code against the artefact it names (constant, schema object, emit site, threshold) and cite file:line. A false claim, implemented faithfully, is the defect.
- A skill's description of a threshold is a claim. Check it against the code that applies it.
- **Slices.** Each slice merges as one PR. It ends in something the requester can try: a command that runs, a page that renders, a real record that moves.
  - Cut thin end-to-end paths, not layers. Order them so the first is usable quickly.
  - A slice is a merge and acceptance boundary, not a work assignment. Its tasks still split into parallel ownership units, and independent slices run at the same time.
  - A slice's acceptance is its own user-visible outcome.
  - Later slices extend the contracts named up front, never replace them. A slice that needs throwaway scaffolding merges into the next.
- **Workspaces.** The coordinator uses its starting workspace. Each worker uses the one its task names, normally the same tree for sequential steps. Make a new one only for a disjoint parallel lane: `git clone <url> ~/.worktrees/<repo>/<name>` outside a box, `~/boxes/<box>/<name>` inside one. Never `/tmp`.
- Complete cutovers: migrate every caller. Remove obsolete paths, shims, aliases, compatibility exports and dead code, unless Sami requires compatibility.
- **Major design changes** (architectural, under `brainstorming`) are a conversation with Sami in the issue's spec (on Dispatch, per `dispatch-brainstorming`). Start from the problem and its evidence, with sections that follow the topic.
  - Put each open question right after its section as a decision block (on Dispatch, an ask block): the options, what each costs, your recommendation.
  - Record each answer there in his words, with its date. It settles what it answers.
  - Ask for approval only when each block carries his answer, folded into the text. Ask only if the spec still proposes something he did not settle. Name those proposals in the request, never an open question.
  - Without Dispatch, hold the same conversation on the tracker you have, or in chat when none is reachable.
  - Outside that conversation, chat is for execution and for bounded designs already under live discussion.

Every plan has these sections:

| Section | Contents |
|---|---|
| `## Hardening ledger` | Empty at first. |
| `## End-to-end verification plan` | Per slice: workflow class and user-visible outcome. The real production transport and environment. The production identity and grant path (a human grant for human-facing work). The existing driver, or a task to build one. The cheapest real shared-resource substitute (staging, a branch run), never a local simulation of the transport. |
| `## Skill catalog` | Each step's required worker skills. |
| `## Contract change census` | For boundary tightenings as `opening-a-pr` defines them: search commands, each hit's disposition, and the rollout line. Trace producers, consumers and shared builders in this repo and its siblings, including callers that rebuild values without spelling fields or endpoints. The brief's caller list is not evidence. |
| `## Permission source census` | Each new or widened permission, role, scope or capability flag, with its real holder in every shipped environment. The group, profile, binding or seed that grants it. The enforcement point. A test that fails for a permission with no holder. Manual grants and test-only identities are not production sources. |

Every worker brief carries its slice's full acceptance scenario and the decisions and rejections for its step, with sources.

- The scenario covers existing data, the real user and transport, required live or write probes, and a pre-merge run with outcome and duration recorded.
- Only Sami's explicit scope change narrows, substitutes or defers it.
- A named corpus is absent, or a probe reaches no cases: mark the scenario `BLOCKED` and repair the plan against existing data. Never drop or narrow it.
- Verification describes a user-observable outcome. The reviewer rejects missing, proxy-only or internal-only verification.
- Track **implemented**, **integrated** and **acceptance-verified** separately. Unresolved dependencies don't establish completion.

## 5. Execution and acceptance

**Briefs.** Each worker brief is a role prompt plus the task. At dispatch time, fetch each part from `https://raw.githubusercontent.com/sjawhar/legion/main/packages/daemon/internal/prompts/roles/`, never from a local copy.

| Purpose | Agent | Parts, in order |
|---|---|---|
| implement or debug | `deep` | `core/common.md`, `core/implementer.md`, `mechanics/interactive.md` |
| acceptance | `deep` | `core/common.md`, `core/tester.md`, `mechanics/interactive.md` |
| review | `reviewer` | `core/common.md`, `core/reviewer.md`, `mechanics/interactive.md` |
| research | `oracle` | `core/oracle.md`, `mechanics/interactive.md` |

Only Legion uses `core/planner.md`, because here the coordinator plans. Reject non-200 responses and empty bodies:

```bash
fetch_part() {
  url="$1"; out=$(mktemp)
  code=$(curl -sS -o "$out" -w '%{http_code}' "$url") || { echo "role prompt unavailable: $url (curl: $code)" >&2; return 1; }
  [ "$code" = "200" ] || { echo "role prompt unavailable: $url (HTTP $code)" >&2; return 1; }
  [ -s "$out" ] || { echo "role prompt unavailable: $url (HTTP 200, empty body)" >&2; return 1; }
  cat "$out"
}
```

- Join the parts and a `# Task` section with blank lines. The task names the absolute workspace, step brief, acceptance criteria, allowed bookmark, report path and skill-catalog entries.
- Keep fetched text verbatim. A failed fetch blocks dispatch, with no fallback. Change the prompts only through review in Legion.
- Check commands in a brief are CI's exact recipe from the repo guide or workflow, not a subset.

**Running.**

- Dispatch independent work concurrently. Take results as events, never by polling, and keep unblocked work moving.
- A silent worker blocks you: clarify once, and redispatch only if needed.
- A `deep` worker's bash tool breaks: give it the kernel-hosted shell from `using-subagents`. It has no eval tool.
- While a PR waits for merge or deploy, continue from its branch or a dependency's branch, unless you are revising it. Post-merge production proof runs alongside the next work, not instead of it.

**Acceptance.**

- After each slice integrates, dispatch `deep` acceptance for its outcome. The last slice runs every driver in the plan.
- The tester adds no review layers. Defects and their red tests go back to the resumed implementer.
- For a slice that changes what runs, run its affected end-to-end scenarios before merge, under the pre-merge scenario rule in `opening-a-pr`.
- For every scenario, record source, dependency and image revisions, then mark it:
  - `RAN`: observed through the real surface, with outcome and duration.
  - `BLOCKED`: the failed command and the authoritative record, never a person's name. Check another lane's reported blocker against that record.
  - `WAIVED-BY-SAMI`: a named blocker Sami explicitly waived, linked to his words. It covers that blocker on that change only.
- Contract census: exercise every human entry point through the real client and the new check. Each unrun entry is `BLOCKED`.
- For a slice with a browser page, the implementer runs ce-dogfood (read ~/.dotfiles/vendor/compound-engineering/skills/ce-dogfood/SKILL.md) on its branch, with the real data the user sees and the user named as the persona, before handing over. It fixes clipped text, empty states and dead clicks; the report goes on the slice's Dispatch issue, and each decision it leaves for a person becomes a decision block in the spec.
- Build or repair missing infrastructure, tooling, skills or drivers at the tested head, then rerun the affected scenarios.
- `BLOCKED` stops merge readiness until every scenario runs or Sami waives its named blocker. Independent work continues.
- Never ask Sami to omit acceptance or to accept a proxy. A named blocker that survives building or repairing the surface goes to him as a pre-merge decision.
- His explicit waiver of that blocker is the only exception. The change's size, its kind and green CI never are.
- Iterate on the repo's fastest loop. Use its slowest surface for final proof, not for each edit. The repo's testing skill defines the rungs and what counts as production-like.

**Review.**

- After acceptance, a final `reviewer` checks correctness, security, dead code, shims, aliases, dual paths and half-migrations.
- Resolve grounded findings before readiness. No other approval or follow-up assignment waives them. Ungrounded preferences don't bind.
- The reviewer sets review depth to the diff inside its own pass.
- Runtime changes: run `ce-simplify-code` once per PR after the last review round, scoped to its diff. An applied change becomes the final head for CI, review and refreshed real-surface proof. A pass with no change is normal.
- Then the coordinator dispatches `thermonuclear-deep-review` and `thermonuclear-code-quality` once at that final head, in sequence with the reviewer, who weighs their findings. The built-in reviewer can't dispatch the pair.
- Prose-only or Minor changes: don't repeat the pair. Docs-only or runtime-free diffs skip both simplify and the pair. Path scope decides the skip, never a claim that the change looks safe.
- **Gate ledger.** Keep one row per PR and one cell per required gate. At the packet, fill each cell from the artifact at the head (the comment, the check run, the summary), never from memory or a summary.
  - The artifacts show what a summary hides. Look for a skipped gate, an automated review on an earlier commit, and a non-blocking item for the PR unapplied under an approved head.

**PRs.**

- The implementer opens each PR under `opening-a-pr` and owns `landing-a-pr`, resumed for checks, reviews and conflicts. When a merge-queue controller runs (`running-the-merge-queue`), the coordinator sends it the packet and it merges. Otherwise the implementer arms auto-merge.
- Each stacked PR follows the same path, plus a reviewer as it lands. Use `gh-stack` for dependent PRs and `squash-stack` for finished stacks it applies to.
- If consolidation changes the delivered diff, refresh acceptance and PR gates. Don't stop to ask about stacking.

## 6. Issue status

On Dispatch, move the status at each transition, for the root and every child issue you own. Other trackers: keep the equivalent state current at the same points.

Every piece of work you own is a child issue of the root before it waits on anything: each PR, follow-up, held slice, adjacent defect found in review, and promise made in a PR or comment. Create it the moment you defer or find it, with what it waits on in its spec and status `backlog`. A step that waits on Sami is an ask on that issue. A local ledger, todo list or handoff file may index these issues, but holds no work that has no issue.

| Status | When |
|---|---|
| `in_progress` | implementation starts |
| `testing` | acceptance starts |
| `needs_review` | the PR is open and you sent its merge packet |
| `done` | your own check through the real surface shows the change working in production |

- A green suite, a harness screenshot or a merge is groundwork, not `done`.
- Never ask Sami or the requester to look at or approve the result before `done`. Close with a reason: `dispatch issue-update --issue <KEY> --status done --reason '<what shipped; the check>'` (it refuses `done` without one).
- An issue left at `triage` while work runs is a defect. The roadmap view reads these statuses, and they are Sami's only view of delivery that doesn't interrupt a session.
- Waiting for the deploy lane is not a status. The issue goes from `needs_review` to `done` on its own schedule, and nobody is told about the wait.
- At each transition, run the owner audit from the `dispatch` skill over the project you own.

**Producer-contract visibility.** A plan, spec or implementation can change a producer contract in a way a downstream surface shows. Then the `needs_review` packet and PR body name every known downstream surface and its visible delta. Each affected consumer's owner reviews that account. No consumer known: record the search that showed it. This is a review boundary, not a Sami approval gate.
