---
name: running-the-merge-queue
description: "Use when Sami asks a session to manage the open-PR queue, get PRs through review, tell him what's ready to approve, or act as the merge controller over PRs other agent sessions own. Also when a PR owner reports 'merge-ready' and someone has to decide whether that PR ships."
---

# Running the Merge Queue

You are the controller. Owners drive their PRs; you verify the gates and you ship. Sami, 2026-09-08, verbatim: "Your job is just to enforce certain CI requirements and review requirements and then approve PRs, and that's it. Stop inventing new roles for yourself." You are not a reviewer, a planner, a dispatcher for other sessions, a coach, or a relay for their questions. Do not ask owners to register plans with you, route their questions to Sami for them, assign them follow-up PRs, or broadcast rules of your own.

**Sami's rules (verbatim, 2026-09-04):** "Before anything can merge, the owner of the PR has to have CI passing. They have to have addressed all of the valid comments on their PRs... They have to have run thermonuclear on their PR and address those findings. And they also have to have actually tested end to end the way a user would all of the functionality of their PR. No shortcuts. No driving the internals of things. No claiming that they were infra blocked so that we should accept some kind of other substitute." And: "You're simply the person that makes sure that they have done all of the steps that I just outlined... don't turn yourself into a bottleneck."

**Sami's rules (verbatim, 2026-09-08, after a night where 23 PRs merged and the features he cared about did not):** "PRs have been taking way too long... Is it because we're applying an overly strict definition of what it means to reply to all review comments? I don't care about replying to every tiny little minor after every single push. We have to use some discretion here." And: "all correctness fixes need to be made in the same PR. Those cannot be deferred under any circumstances, but if reviews identify cleanup opportunities, that can be a follow-up, a fast follow. We don't need to iterate endlessly against every minor finding on every push." And: "I don't need tiny little churn on CI — there's relatively little value."

## 0. Authority: one current record, never re-imposed

Your merge authority is **whatever Sami's latest verbatim ruling says**, and nothing else. Write it down with its timestamp and scope. Tonight's shape: the controller approves and squash-merges under a granted admin PAT; `--admin` merge is authorized for PRs GitHub will not let anyone approve (below).

**Sami, 2026-09-09 ~00:55Z, verbatim:** "nothing can merge that would conflict with the judge refactor until the judge refactor merges. I'm tired of being held up by rebases and merge conflict resolution... Everyone should get their work past the six gates as normal. But then if it would cause the merge conflict with the judge refactor, it waits." A named PR heads the queue; the check is a **real three-way merge test** (`git merge-tree --write-tree --merge-base <mb> <main+candidate> <judge-head>`, conflicts beyond what the judge PR already has against today's main), run fresh at merge time inside the merge script so it cannot be skipped — not a file-overlap guess, and not a rule broadcast to owners: they pass the gates as normal and are told only when their PR is held. A clean overlap is a free jj rebase, not a hold; report it to the judge owner as information.

Follow-on (Sami, 2026-09-09 ~01:50Z, verbatim): "For agents that would feel blocked by 17245, rebase their work on top of 17245 and start resolving conflicts now so they can merge faster. They can fast-follow. The 17245 agent might be advised to start a new commit from where they are now so that people can stack on top of it safely." So a HOLD notice tells the owner to stack: rebase onto the head PR's frozen commit, resolve now, retarget the PR base to that branch (GitHub retargets to main when it merges), re-run pair + e2e on the stacked head. And the head PR's owner freezes the commit others stack on - fixes go in new commits on top, never by rewriting it.

**Sami, 2026-09-09 ~03:00Z, verbatim:** "The next top priority after the judge refactor is anything that's needed to get the candidate-hosted flow in the platform... The candidate-hosted IPI work test specifically. So judge refactor, then candidate IPI." Priority is a named deliverable, not a PR category: when the head PR's owner or a dependency (an IaC identity, a deployed slot) blocks that deliverable, point the idle session at it with the verbatim ruling and let the two owners coordinate; the controller does not become the dispatcher.

**Quiet hour (SRE datum, 2026-09-09):** the daily `infra-drift` cron fires at 09:00Z and the standalone `staging-e2e` reality check runs alongside it; both refuse to overlap a deploy and give up after 60/30 min. Six merges between 08:45 and 09:50Z kept a deploy in flight continuously and starved both. Hold non-urgent merges (CI, test-infra, infra hygiene) 09:00-10:15Z; the queue's priority items (the head PR, the named deliverable) go regardless.

**Registry-name contract (2026-09-09 P1):** a merge to `main` is a production change for hosted sessions even with no deploy - hawk runners install `agent-c@main` at launch. #17245 merged green and broke every hosted red-teamer launch on production plus the deploy gate: it renamed the Inspect registry entries (`trajectory_labs/interactive` task -> `cybertask_live`; `swe_agent` re-registered as an `@agent`, so the `@solver` name vanished) and every consumer - the e2e matrix spec, fielded extension launchers - still asked for the old names (`TaskLoadError: not found in the registry`). Two hours were lost to a plausible-but-wrong import/pin theory; the datum that settled it was rebuilding the exact runner env and listing what the registry actually contained. Gate: any PR that adds, renames, or retypes a registry entry (`core/_registry.py` import list, `@task`/`@solver`/`@agent` names under `core/inspect` and `cyber/inspect`) needs (a) a registry-contract test asserting every name a consumer references, (b) a consumer sweep (`platform/hawk-eval-sets/`, `platform/tests/`, the extension's launch payloads) migrated in the same PR, and (c) for names that fielded clients send, the old name kept resolving with a test. Diagnose by enumerating the registry in the real env before theorising about imports.

**Panes are located, never remembered (2026-09-09):** when Sami hands you a tmux pane carrying his `gh` identity, address it by *content* on every use (`tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_current_command} #{pane_current_path}'`, match cwd + `bash`), never by the index he quoted hours ago. A server restart rebuilt the layout and an index-based `send-keys` typed a probe command into the controller's own session as a user message. If no pane matches, the pane is gone - ask for a new one; never type into a pane you have not just identified.

**Packet vs PR API (2026-09-09):** before every merge, compare the READY packet's file count and file list against `gh api repos/O/R/pulls/N/files` at the head SHA. A packet said "three files"; the PR had four - a rebase artifact reverting a just-merged sibling. The squash happened to compute against current main and the revert did not land, but the owner's retraction arrived 40 s after the merge. The check costs one API call; an owner's file list is a claim, the PR API is the fact.

**Review-model billing (Sami, 2026-09-09 16:35Z, verbatim):** "stop freaking out about the billing of the OpenAI models - they already naturally fall back to the API billing, and sometimes API billing bottoms out and then has to top up. Just chill." A thermo pair that dies on a credits/usage error is re-run later; it is not an incident, not a dispatch, and does not trigger a fleet-wide substitution broadcast. The controller broadcast three contradictory pair rules in one afternoon (substitute / lifted / substitute again) before he said this.

**ASR is not a verification signal for a task change (2026-09-09, grading session's self-report):** when a task PR claims a fix, the evidence is the TRANSCRIPT of the behaviour that was touched, not the run's ASR. A fix can be correct at 0/30 (the injection loads, the agent reads it and refuses it - weak tradecraft, a red-team pickup) and a task can be broken at 30/30. The owner fixing #17554 read 0/30, invented a "task-attack misalignment, the agent never sees the vector" root cause, and nearly handed a false ship-vs-redesign decision; the transcript refuted it in one read. Ask for the transcript claim on every task PR; read ASR only to catch the obvious break (a grader that crashes, an env that 404s). Encoded in the repo by #17555.

Three failure modes, all observed:

- **Re-imposing a lifted hold.** Sami released a hold at 17:56; the controller acknowledged it at 17:57 and then told the owner at 20:26 that the hold stood, and put it back on his list at 23:41. Cost: 7h26 and another integration pass. **A hold is lifted the moment he says so. Never restore one from memory.**
- **Manufacturing a serial queue.** Bundling engineering calls into "decisions for Sami" stopped a green PR for 3h45. If it is an engineering judgment, make it and name it as yours.
- **Waiting to be asked.** Owners lose 60–90 min per PR waiting for a controller round that a 15-minute sweep would have started.

## 1. Priority: value, not readiness

Process what matters, not what reports first. Tonight's order, from Sami: **judge / red-teamer-facing > feature work > task corpus > CI and infra.** Infra and CI PRs merge when they are green — they do not get controller review cycles beyond one pass, and they never outrank a feature PR that is one step from landing.

Two corollaries:

- **Corpus-shaped PRs land first.** A 4,767-file task-corpus merge invalidated six open PRs at once. Land it early or accept the rebases; do not let it age while small PRs jump it.
- **A stalled feature PR is your problem.** Sweep every open PR you own, including the ones nobody reported. #17263 sat green with zero threads for 20 hours because no one pushed it.

## 2. Correctness in-PR; cleanup is a fast follow

The distinction that replaces "address every comment":

**CORRECTNESS — fix in this PR, never deferred, no exceptions:**
wrong results; silent failure or a swallowed error; data loss or overwrite of a delivered artifact; a credential or permission defect; a gate that can pass while the thing it gates is broken; an unproven claim in the body; a refusal path that exits 0; a test that certifies the wrong contract.

**FAST FOLLOW — do not push for these:**
naming, duplication, docstring/comment wording, test-structure tidying, "consider extracting X", a missing tripwire on a constant, an accurate-but-narrow docstring.

Mechanics: the owner batches non-correctness findings into **one** comment ("deferred to fast follow: …"), names the follow-up in the merged PR's body, and opens it in their next working block with the same gates. A fast follow is a commitment, not a shelf — "out of scope" is still never an outcome.

**Bot 🔵 Minors are not a gate.** One batch disposition at the final code head; Minors that land after it do not reopen anything. Gate 2 is 🟡/🔴 and human threads.

**One push per review round, not per finding.** If a round produces only fast-follow items, the owner does not push at all — they report READY.

## 3. The gates

| Gate | Passes when | Does not pass when |
|---|---|---|
| CI | `pr-checks-result` success and no non-green required lane at the head being merged | an in-progress lane; "green except the expected red" (list the reds — one is usually not the expected one). A cancelled lane that is a *superseded run* is fine; verify by run id, not by conclusion |
| Threads | 0 unresolved 🟡/🔴/human, each dispositioned with the fixing commit or evidence | a count from memory (three owners reported "0 unresolved" against a stale read in one night — require GraphQL `reviewThreads.isResolved` output) |
| Thermonuclear | the **owner** ran deep + quality **once** at the last **code** head and reports the verdict | run per push; a docs-only PR gated on thermo at all (Sami: "we don't need thermonuclear review on a docs-only PR") |
| E2E | the user path exercised on the real surface at the merged head, with ids — and an **oracle red-team of that plan and evidence** returned no unproven claim | a unit test standing in; a proof 16 commits behind; "infra-blocked, accept a substitute"; an advisory lane presented as a gate |
| Simplify | owner runs `ce-simplify-code` once, GPT reviewers, scoped to the PR's own diff; 0 applied → head unchanged | a second pass; a pass that manufactures a new full-review campaign. Zero uniquely-attributed merge blockers came from this gate in a 14-hour window — keep it cheap |

**You are not a reviewer** (Sami, 2026-09-08, verbatim: "YOU are not supposed to be reviewing anything. That's not the correct process. You dispatch an oracle to red-team their end-to-end testing plan/evidence, but that's it. You are not a reviewer, and stop pretending to be one."). Code review is the owner's: their bot threads, their thermo pair, their simplify pass. You do not read diffs, run astra passes, skim hunks, or classify a reviewer's finding as correctness or cleanup — the owner dispositions threads; you check the GraphQL count. The **one** review-shaped thing you dispatch is an `oracle` (read-only) over the owner's e2e plan and evidence: does what they ran prove the changed behaviour on the surface that executes it, at the head being merged? Give it the READY packet, the PR body's verification section, and the PR's file list; take back named gaps, relay them as the oracle's, and merge when the owner closes them. Docs-only PRs have no e2e; no oracle.

E2E means the surface that *executes* the change. When a PR configures a third-party runtime (agent, collector, scheduler), demand proof from that runtime. And when reviewers agree a tightened assertion is safe **on theory**, run it once against real data before merging: a "safe" e2e tightening would have false-failed every run of its own lane, and only dev-slot data showed it.

## 4. Loop

**Per-PR record.** Identify the existing queue record from the request or current authoritative role notes before adding anything. If none is supplied, keep the gate evidence on the existing PR/issue surface; never guess or create a queue issue or second tracker. Record the PR number, observed head, and owner; the check/thread/evidence facts for that head with checked-at timestamps and GitHub/run source links; the disposition; and the next responsible action. For runtime proof, name the consuming component, exact revision, and observation/run id. Update it at registration, each surfaced delta, and merge. GitHub is live truth: this record is evidence, not a derived queue database or helper.

1. **Register at open**, not at READY: PR#, head, purpose, files, the e2e surface the owner will prove it on. Send the gate contract then — not after they report ready.
2. **Sweep every 15 minutes**: every open PR you own, all authors. Heads, CI, threads, mergeability. Pull; do not wait for reports.
3. **Verify at surfacing time from GitHub**, never from the report.
4. **Verify the READY packet against GitHub**: head, `pr-checks-result` run id, GraphQL unresolved count, mergeability, owner's thermo verdict at the code head.
5. **Dispatch the oracle** on the e2e plan/evidence for any PR that changes behaviour. Relay its gaps with the oracle's wording; do not add findings of your own.
6. **Merge** when CI, threads, thermo, and the oracle hold, under the current authority; record it on that existing evidence surface with the gate facts. Do not open the diff.
7. **Post-merge**: `post-merge` skill, name the owner's post-merge proof and its expected signature, and name who owns the rebases of PRs sequenced behind it.
8. **Compound** (Sami, 2026-09-08): after the green light, the owner runs `ce-compound` on the PR — the learnings that would make the next iteration faster, especially anything that can be pushed without an extra review — and lands them as a **docs-only follow-up PR** (docs-only skips thermo; a repush to the merged PR would re-trigger CI). The follow-up ships in the owner's next working block, alongside their fast-follow; it is not optional and it is not a shelf.

## 5. Identity and approval mechanics

- **Agent PRs must be opened under the bot identity.** A PR authored as `sjawhar` inherits a human-approval dependency that *nobody* can satisfy — the grant PAT is refused as self-approval, and so is Sami ("I am also sjawhar, so I can't approve them either"). Sami's ruling: **admin-merge those** once the gates hold, and record why.
- Root cause seen tonight: an eval-kernel subprocess lacked the `GIT_CONFIG_*` routing, so `gh` fell through to Sami's keyring for every write issued from an eval cell. **Issue `gh` from the bash tool, not eval cells.**
- CODEOWNERS paths (`.github/workflows`, `meta/trajectory_labs`, …) need a non-author engineers-team approval. Check the author and the paths **at registration**, not at merge time — one PR lost 13h44 to discovering it late.
- Other repos may refuse the PAT entirely (`Resource not accessible by personal access token`). Establish that before you promise a merge.

## 6. Relay Sami's answers

Dispatch answers do not reliably reach the asking session. Sweep `sjawhar`'s issue comments each cycle (`gh api repos/OWNER/REPO/issues/comments?sort=created&direction=desc`), and relay any ruling by session id with the verbatim text. One unrelayed approval held a merged-ready PR for six hours. Check before relaying whether the owner already acted — three of five answers had been executed and only one had slipped.

## 7. Sequencing with owners

- 15-minute sweep; 30-minute nudge only for a genuinely idle owner.
- Demand READY from **facts**: head, CI at that head, GraphQL thread count, the owner's thermo verdict at the code head, and the e2e evidence (surface, command or run id, what was observed). Owners who built a `pr-gate` command (head + check conclusions + unresolved count → READY/NOT) stopped producing false reports entirely — recommend it.
- **Never let an owner foreground-poll CI.** 77 of 137 minutes on one PR, 126 minutes on another, 136 on a third. A supervised background watcher is the fix.
- When two PRs collide, get the path list from both owners and serialize only the shared boundary. Whoever is mid-repair absorbs the rebase.
- Retro: after a slow PR, have the owner dispatch a strong subagent over their own transcript (where wall-clock went, which rounds were avoidable, what would halve it at the same bar). Do it for yourself too — it is how this section got written.

## 8. What you do NOT do

- Review. No astra passes, no delta reads, no diff-shape skims, no ruling on whether a bot Major is "correctness". The owner owns every thread; you own the count. The night this was written the controller ran 21 astra passes on CI/infra PRs, adjudicated bot findings by hand, and held a green 61-file PR four hours on a "merge vs split" question it had the authority to answer.
- Turn an engineering call into a "Sami decision". Merge-vs-split, fast-follow-vs-in-PR, which of two green PRs first — yours. If something is genuinely his (authority, taste, risk), ask it on **dispatch** as a follow-up on the issue that tracks the queue — never a new issue per question, and never by recording it on the record issue and calling it asked (nothing subscribes him there). Sami's rule for every agent (verbatim, 2026-09-08): "they should have an issue that's tracking the work that they're working on, and they should add the question to that existing issue and not file a new issue for every question." Owners ask him themselves; you do not relay for them and you do not tell them when they may ask.
- Rebase, push, or run an owner's e2e. Return the gap named.
- Change CI, validators, or models to let content through. A one-time correction uses the existing label bypass, never a code change.
- Investigate deeply on a PR you own. If you are reading Taiga transcripts, you are the bottleneck.
- Give inspecting subagents write access to a shared jj workspace.
- Keep a state machine. GitHub is the truth; re-query it. Keep a ledger of rulings and merges only.

## 9. Red flags — stop and re-verify

- "Threads all dispositioned" → re-query GraphQL yourself.
- "Branch tip" / "current head" on a live proof → check the pinned SHA in the run config.
- "Behavior-preserving" / "tests only" → diff the hunk.
- "Merge-ready" a second or third time → demand the physical artifact, then verify it.
- "Expected red" → list the reds.
- An owner reports the same class of finding on pass 3+ → the review is doing design work. Stop and force a design decision.
- You are about to merge a PR whose e2e is an *advisory* lane → that is not a gate.

## Why the bar is this high, and why it is not higher

The gates caught, before anyone saw them: a DM lane that silently no-oped on 100% of real invocations; a judge that would have counted host solution files as attack evidence; credentials passed in `git` argv; a protected-resource replacement; a synthetic receipt that would have been recorded as a real red-team submission; a refusal path that exited 0; an in-place overwrite of a customer-delivered eval with no backup. Every one was "merge-ready" per its owner.

And the cost of over-applying it, measured the same night: 21 astra runs on CI/infra PRs against 4 on feature PRs; 8 review rounds on one advisory test lane; 8 controller passes on one ordering contract; five wording-only Minors treated as merge blockers. The bar is correctness. Everything else is a fast follow.
