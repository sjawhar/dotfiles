---
name: opening-a-pr
description: "Use before opening a pull request, and before telling Sami that any work is merge-ready, done, or blocked on his merge. Also use when an open PR has changed enough that its verification is stale, and when tempted to leave anything for later or after the merge. Owns every PR-opening flow here: other shipping skills supply mechanics inside step 8 and never replace this gate."
---

# Opening a PR

A green PR means nothing to Sami. What matters is that the intent is implemented, the architecture he asked for is respected, and the thing actually runs. This gate is what stands between "I believe it works" and spending a human's attention on it.

Shortcuts on the way here were fine. That is what the hardening ledger is for. **Nothing in the ledger survives to the PR.** "Later" means after end-to-end proof and before human review; it never means after merge.

Work the steps in order. Each produces evidence; step 8 assembles it. **Every step runs on every change.** The depth of evidence scales with the diff, not the number of steps. A two-line fix still gets a completeness check, a real run of the affected surface, a targeted doc grep, and a brief oracle pass. "This change is too small for the gate" is how the gate dies.

1. **Empty the hardening ledger.** Every shortcut logged during implementation is now either done or explicitly blocked with the blocker named (missing access, or a decision only Sami can make). "I'll follow up" is not a legal state, and neither is a GitHub issue. Then reconcile scope: diff the change against the authorized plan or issue (`jj diff --git` against the target branch) and finish anything promised but absent. **If there is no ledger because the work started as an interactive request rather than through `sdd`, write one now** from the session: what Sami asked for, which decisions got settled along the way, every shortcut you took, and every part of the request you are unsure you covered. An absent ledger means nothing was written down; it never means there was nothing to audit.
2. **Have a fresh agent check completeness.** Dispatch `task(category="ultrabrain")` with the diff, the ledger, and the authorization: the plan or issue if one exists, otherwise Sami's original request and the decisions settled in this session, quoted. Its mandate: *"You did not do this work. List everything the authorization promised that this diff does not deliver, and every hack that is still in it."* Fix what it returns and re-dispatch until it comes back clean. You cannot audit your own scope because you already believe you finished.
3. **Run the `analyze` skill and fix what it finds.** Critical and high severity get fixed, not noted. Re-run after fixing. If the same finding survives three rounds, stop hammering it: checkpoint what you tried, what failed, and what you learned, then either switch to a materially different approach or bring Sami the options with your recommendation. Do not hand him a bare "stuck."
4. **Have a fresh agent prove it works.** Dispatch `task(category="deep")`: *"You did not write this code. Boot the actual artifact and use it through its real surface: TUI in tmux, web in a real browser, API via curl, or library via a driver script against the user-facing workflows this change is supposed to serve. Tests passing is not evidence; you have to run the thing. Report what you ran, what you saw, and every defect."* Fix every defect it reports and re-dispatch until it is clean.

**Every artifact has a consumer surface. Find it.** No plan listing workflows, or no conventional runtime, is not an exemption: read the request and the diff, work out who or what actually consumes the thing, and exercise that. A skill or prompt gets loaded and walked by a fresh agent. A config change gets applied and the dependent service observed. A CI workflow gets triggered. A doc gets followed literally, start to finish, by someone who has not read the diff. If you genuinely cannot reach the surface because the environment or credential is unavailable, the honest report is **not merge-ready, verification blocked on X**, never "verified by inspection."

**The surface means the user's own access path.** Exercising a restricted workflow through a privileged shortcut proves nothing about the user's experience: a minted session cookie is not the real SSO login, `kubectl exec` is not the contractor's SSH-via-jumphost, an admin API call is not the customer's API call, and running a checker command that doesn't drive the real surface is naming a command, not verifying. If the real path is awkward to drive, that awkwardness is missing test tooling — building it is in scope (see the repo's "test tooling is part of the feature" rule), not a reason to substitute.

**A blocked acceptance checkpoint is a merge blocker, not a footnote.** Merge-ready requires the full end-to-end acceptance scenario to have executed. When a checkpoint is environment- or access-blocked, it does not become a "post-merge follow-up" or "remaining work" — surface the environment decision to Sami as a pre-merge decision (current state → desired state → options) and report NOT merge-ready until the scenario has run or Sami has explicitly waived it.

   **This step has no exemptions.** Merging first and testing afterwards is the exact failure this skill exists to prevent, and "it should work" has never once been true.
5. **Sweep the docs.** Search the repo for anything the diff invalidated: renamed commands, changed flags, moved paths, config keys, or altered behavior across READMEs, `docs/`, skills, and AGENTS.md files. Update them here, in this change, with the `updating-docs` skill. Do not trust memory for which docs exist; grep.
6. **Get an oracle review.** Give the oracle the final diff and the QA report. Its findings get fixed, not acknowledged.
7. **Capture the learning, if there is one.** Did this work involve a gotcha that cost more than half an hour, or contradicted the documentation? If yes, run `ce-compound mode:headless`. If no, say so in one line and move on.
8. **Structure the review before opening or updating the PR.** Multi-commit PRs are allowed when they improve the review narrative. Good commit groupings usually follow dependency order:
   1. Schema/storage or generated API definitions.
   2. Core logic.
   3. Wiring and integration.
   4. UI or surface behavior.
   5. Tests.

   Each commit must remain internally coherent, and the PR body must explain the narrative and call out intentionally coupled changes. When code behavior should stay untouched, prefer PR description and review notes:
   - Add a TL;DR that matches the actual diff.
   - Separate core files from generated or mechanical files.
   - Call out risky behavior changes, migration order, rollout plan, and test coverage.
   - Link issue trackers, dashboards, or design docs when they explain intent.

   Do not hide meaningful behavior changes inside "cleanup". Do not bypass hooks unless the user explicitly asks. If review notes cannot make the scope understandable, split the PR before opening it.
9. **Preserve the final tree across history-only rewrites.** Before reordering, splitting, or squashing commits, record the PR head and base refs with `gh pr view <PR> --json title,headRefName,baseRefName,state,commits`, then record a content fingerprint: `before_tree=$(jj diff --from 'root()' --to @ --git | sha256sum)`. After the rewrite, run `after_tree=$(jj diff --from 'root()' --to @ --git | sha256sum)` and compare them. A different fingerprint is allowed only for an intentional, separately explained content change. Use `jj diff --summary <before-change> @` to inspect it. Do not push if the tree changed unintentionally. This is the jj equivalent of preserving Git's final tree while making history easier to review.
10. **Resolve the PR target — never our own fork.** If the repository is a knives-managed fork (`knives repos` lists it), the PR targets the **upstream** repository; our org forks (trajectory-labs-pbc/*, sjawhar/*) hold branches and releases and never receive PRs. Walk the `pr-preflight` skill (`knives preflight`) before any `gh pr create`. **Upstream PRs additionally require Sami's explicit go-ahead, given after you present evidence the change works** — the verification package from step 4, not green CI. Present the evidence, get the yes, then open.
11. **Open or update the PR.** First check whether one already exists for this branch: `jj bookmark list`, then `gh pr view 2>/dev/null`. **If a PR exists, push to it and update its body; never open a second one** (`jj git push`, then `gh pr edit`). If none exists, set the bookmark from the change description (`jj git push --named=<name>=@`) and `gh pr create` with closing keywords for the issues it resolves. Either way the body must carry a `## Verification` section written for a human: which user-facing workflows were exercised end to end, who ran them, and what was observed. Write behavior, not a command transcript. **A PR without that section is not merge-ready, and neither is one whose section is stale.**

    **If the change touches a user-visible surface, that section must SHOW it, not describe it.** Embed the render in the body — screenshot, before/after pair, or a recording where motion is the point. A reviewer cannot see a diff, and a prose description or a hand-drawn markdown table of what it looks like is the author asking to be believed. "Frontend-only" is the case where this matters most, because the diff is then the least informative thing in the PR. Name what the image is evidence of: a component in a harness proves the component and its stylesheet, not the API wiring behind it. Both are legitimate; conflating them is not. In agent-c, publish via the `pr-screenshots` skill so the images survive.

Then run `pr-inbox <N>` (dotfiles `scripts/`, on PATH) once before leaving this skill — automated reviewers post within minutes of open, and a review that lands while you are still here is yours, not the babysitter's — and hand off to the `landing-a-pr` skill, which re-runs the inbox at every touchpoint: after every push, after every check completion, and always before the words "merge-ready".

## The excuses, and why none of them work

Every line in the left column is a real thing an agent said in a real session on this machine, right before shipping something it had never run.

| The excuse | The reality |
|---|---|
| "The mechanism says this should work." | You watched a mechanism, not a result. One session shipped that reasoning three times in a row; the gap was a single command away each time. |
| "Tests pass, CI is green." | Green CI means the code did not crash the way you anticipated. It says nothing about whether the feature does what Sami asked for. |
| "It gets live-verified after the merge / on the next deploy." | Then it is unverified now, and you are asking a human to merge on faith. Verification scheduled for after the merge is the exact failure this gate exists to stop. |
| "Want me to run the smoke test first?" | Do not ask. Running it is the job, and it was authorized the moment the work was. |
| "The rest should follow in a separate PR." | Separate PR = deferral. If you can describe it precisely enough to defer it, you can do it now. |
| "Want that as a follow-up issue?" | Filing is not fixing. Do not open an issue Sami did not ask for. |
| "I deliberately left that for later." | "Later" is right now. That is what this step is. |
| "Nothing further is actionable on my side until you merge." | Check the list above before you say that. It is usually false, and it burns Sami's attention to find out. |
| "This change is too small to boot." | Booting a small change is fast. That is an argument for doing it, not skipping it. |
| "I verified it earlier, before the last few fixes." | Those fixes are the diff now. Stale verification is a false claim. |
| "This is docs / config / a skill only: there is nothing to run." | Then find its consumer and exercise that: a fresh agent walks the skill, the dependent service gets observed after the config applies, a human follows the doc literally. Every artifact has a surface. |
| "The environment for testing isn't available here." | Say that out loud as a blocker and report not merge-ready. Missing tooling is a finding, not a pass. |
| "The QA agent couldn't run it either, so I'll describe the code instead." | Two agents failing to reach the surface is twice the evidence that it is unverified. Escalate the blocker; do not narrate the source. |
| "The unit tests exercise the same code path." | They exercise it with your assumptions wired in. The surface is where the assumptions get tested. |
| "Sami wanted this fast." | He wants it working. He has said so about every fast thing you ever shipped broken. |
| A `## Verification` section that describes anything other than actual use by you or the QA agent. | A heading is not evidence; describe the real surface that was exercised and what happened. |
| "Verified by inspection", "verified by reading the code", or "the frontmatter parses" offered as end-to-end evidence. | Inspection can establish syntax or intent, not the user-observable result. |
| "I described what it renders as / drew the layout as a table, which is clearer than an image." | It is clearer to you, who has seen it. The reviewer has not. A table of cell contents is a claim about a render, not the render. |
| "It's frontend-only, so the diff shows the change." | A diff shows JSX and CSS. It does not show what those produce, which is the only thing under review. Frontend-only raises the bar for an image, it does not remove it. |
| "I drove it in a browser and read the DOM back, so it is verified." | That is verification, and it is necessary. It is not evidence a reviewer can see. Capture the frame from the session you already had open. |

Catch yourself writing any excuse above → go run step 4.

> **jj workspace note:** in a non-default workspace there may be no `.git` directory. If `gh` fails, point it at the default workspace: `GIT_DIR=/path/to/default/.git gh ...`
