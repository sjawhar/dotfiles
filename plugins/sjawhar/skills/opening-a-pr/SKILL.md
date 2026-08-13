---
name: opening-a-pr
description: "Use before opening a pull request, and before telling Sami that any work is merge-ready, done, or blocked on his merge. Also use when an open PR has changed enough that its verification is stale, and when tempted to leave anything for a follow-up, a separate PR, or after the merge. Owns every PR-opening flow here: other shipping skills supply mechanics inside step 8 and never replace this gate."
---

# Opening a PR

A green PR means nothing to Sami. What matters is that the intent is implemented, the architecture he asked for is respected, and the thing actually runs. This gate is what stands between "I believe it works" and spending a human's attention on it.

Shortcuts on the way here were fine — that is what the hardening ledger is for. **Nothing in the ledger survives to the PR.** "Later" means after end-to-end proof and before human review; it never means after merge.

Work the steps in order. Each produces evidence; step 8 assembles it. **Every step runs on every change** — what scales with the size of the diff is the depth of the evidence, not the number of steps. A two-line fix still gets a completeness check, a real run of the affected surface, a targeted doc grep, and a brief oracle pass; they are all just fast. "This change is too small for the gate" is how the gate dies.

1. **Empty the hardening ledger.** Every shortcut logged during implementation is now either done or explicitly blocked with the blocker named (missing access, or a decision only Sami can make). "I'll follow up" is not a legal state, and neither is a GitHub issue. Then reconcile scope: diff the change against the authorized plan or issue (`jj diff --git` against the target branch) and finish anything promised but absent. **If there is no ledger — because the work started as an interactive request rather than through `sdd` — write one now** from the session: what Sami asked for, which decisions got settled along the way, every shortcut you took, and every part of the request you are unsure you covered. An absent ledger means nothing was written down; it never means there was nothing to audit.
2. **Have a fresh agent check completeness.** Dispatch `task(category="ultrabrain")` with the diff, the ledger, and the authorization — the plan or issue if one exists, otherwise Sami's original request and the decisions settled in this session, quoted. Its mandate: *"You did not do this work. List everything the authorization promised that this diff does not deliver, and every hack that is still in it."* Fix what it returns and re-dispatch until it comes back clean. You cannot audit your own scope — you already believe you finished.
3. **Run the `analyze` skill and fix what it finds.** Critical and high severity get fixed, not noted. Re-run after fixing. If the same finding survives three rounds, stop hammering it: checkpoint what you tried, what failed, and what you learned, then either switch to a materially different approach or bring Sami the options with your recommendation. Do not hand him a bare "stuck."
4. **Have a fresh agent prove it works.** Dispatch `task(category="deep")`: *"You did not write this code. Boot the actual artifact and use it through its real surface — TUI in tmux, web in a real browser, API via curl, library via a driver script — against the user-facing workflows this change is supposed to serve. Tests passing is not evidence; you have to run the thing. Report what you ran, what you saw, and every defect."* Fix every defect it reports and re-dispatch until it is clean.

   **Every artifact has a consumer surface — find it.** No plan listing workflows, or no conventional runtime, is not an exemption: read the request and the diff, work out who or what actually consumes the thing, and exercise that. A skill or prompt gets loaded and walked by a fresh agent. A config change gets applied and the dependent service observed. A CI workflow gets triggered. A doc gets followed literally, start to finish, by someone who has not read the diff. If you genuinely cannot reach the surface — the environment is missing, the credential is Sami's — the honest report is **not merge-ready, verification blocked on X**, never "verified by inspection."

   **This step has no exemptions.** Merging first and testing afterwards is the exact failure this skill exists to prevent, and "it should work" has never once been true.
5. **Sweep the docs.** Search the repo for anything the diff invalidated — renamed commands, changed flags, moved paths, config keys, altered behavior — across READMEs, `docs/`, skills, and AGENTS.md files. Update them here, in this change, with the `updating-docs` skill. Do not trust memory for which docs exist; grep.
6. **Get an oracle review.** Give the oracle the final diff and the QA report. Its findings get fixed, not acknowledged.
7. **Capture the learning, if there is one.** Did this work involve a gotcha that cost more than half an hour, or contradicted the documentation? If yes, run `ce-compound mode:headless`. If no, say so in one line and move on.
8. **Open or update the PR.** First check whether one already exists for this branch — `jj bookmark list`, then `gh pr view 2>/dev/null`. **If a PR exists, push to it and update its body; never open a second one** (`jj git push`, then `gh pr edit`). If none exists, set the bookmark from the change description (`jj git push --named=<name>=@`) and `gh pr create` with closing keywords for the issues it resolves. Either way the body must carry a `## Verification` section written for a human: which user-facing workflows were exercised end to end, who ran them, and what was observed. Prose about behavior, not a transcript of commands — Sami is deciding whether to trust it, not re-reading your terminal. **A PR without that section is not merge-ready, and neither is one whose section is stale.**

Then hand off to the `landing-a-pr` skill.

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
| "This is docs / config / a skill only — there is nothing to run." | Then find its consumer and exercise that: a fresh agent walks the skill, the dependent service gets observed after the config applies, a human follows the doc literally. Every artifact has a surface. |
| "The environment for testing isn't available here." | Say that out loud as a blocker and report not merge-ready. Missing tooling is a finding, not a pass. |
| "The QA agent couldn't run it either, so I'll describe the code instead." | Two agents failing to reach the surface is twice the evidence that it is unverified. Escalate the blocker; do not narrate the source. |
| "The unit tests exercise the same code path." | They exercise it with your assumptions wired in. The surface is where the assumptions get tested. |
| "Sami wanted this fast." | He wants it working. He has said so about every fast thing you ever shipped broken. |
| A `## Verification` section that describes anything other than actual use by you or the QA agent. | A heading is not evidence; describe the real surface that was exercised and what happened. |
| "Verified by inspection", "verified by reading the code", or "the frontmatter parses" offered as end-to-end evidence. | Inspection can establish syntax or intent, not the user-observable result. |

Catch yourself writing any excuse above → go run step 4.

> **jj workspace note:** in a non-default workspace there may be no `.git` directory. If `gh` fails, point it at the default workspace: `GIT_DIR=/path/to/default/.git gh ...`
