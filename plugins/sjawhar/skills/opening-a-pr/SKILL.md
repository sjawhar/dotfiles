---
name: opening-a-pr
description: "Use before opening a pull request, and before telling Sami that any work is merge-ready, done, or blocked on his merge. Also use when an open PR has changed enough that its verification is stale, and when tempted to leave anything for later or after the merge. Owns every PR-opening flow here: other shipping skills supply mechanics inside step 8 and never replace this gate."
---

# Opening a PR

A green PR means nothing to Sami. What matters is that the intent is implemented, the architecture he asked for is
respected, and the thing actually runs. This gate is what stands between "I believe it works" and spending a human's
attention on it.

Shortcuts on the way here were fine. That is what the hardening ledger is for. **Nothing in the ledger survives to the
PR.** "Later" means after end-to-end proof and before human review; it never means after merge.

Work the steps in order. Each produces evidence; step 8 assembles it. **Every step runs on every change.** The depth of
evidence scales with the diff, not the number of steps. A two-line fix still gets a completeness check, a real run of
the affected surface, a targeted doc grep, and a brief oracle pass. "This change is too small for the gate" is how the
gate dies.

Some rules link a reference under `references/`. Each link says when to read it; open it at that point rather than
working from memory of it.

1. **Empty the hardening ledger.** Every shortcut logged during implementation is now either done or explicitly blocked
   with the blocker named (missing access, or a decision only Sami can make). "I'll follow up" is not a legal state, and
   neither is a GitHub issue. Then reconcile scope: diff the change against the authorized plan or issue (`git diff`
   against the target branch) and finish anything promised but absent. **If there is no ledger because the work
   started as an interactive request rather than through `sdd`, write one now** from the session: what Sami asked for,
   which decisions got settled along the way, every shortcut you took, and every part of the request you are unsure you
   covered. An absent ledger means nothing was written down; it never means there was nothing to audit.
2. **Have a fresh agent check completeness.** Dispatch `task(category="ultrabrain")` with the diff, the ledger, and the
   authorization: the plan or issue if one exists, otherwise Sami's original request and the decisions settled in this
   session, quoted. Its mandate: *"You did not do this work. List everything the authorization promised that this diff
   does not deliver, and every hack that is still in it."* Fix what it returns and re-dispatch until it comes back
   clean. You cannot audit your own scope because you already believe you finished.
3. **Run the `analyze` skill and fix what it finds.** Critical and high severity get fixed, not noted. Re-run after
   fixing. If the same finding survives three rounds, stop hammering it: checkpoint what you tried, what failed, and
   what you learned, then either switch to a materially different approach or bring Sami the options with your
   recommendation. Do not hand him a bare "stuck."
4. **Have a fresh agent prove it works.** Dispatch `task(category="deep")`: *"You did not write this code. Boot the
   actual artifact and use it through its real surface: TUI in tmux, web in a real browser, API via curl, or library via
   a driver script against the user-facing workflows this change is supposed to serve. Tests passing is not evidence;
   you have to run the thing. Report what you ran, what you saw, and every defect."* Fix every defect it reports and
   re-dispatch until it is clean. The proof follows the repository's own workflow for the kind of change (application
   or platform, infrastructure, task authoring, task queueing) and exercises every boundary the change crosses; one
   kind's checklist is not proof for another.

**Producer contracts need a consumer-visible delta.** When a plan, specification, or change alters a producer's schema,
publish path, or a condition under which a field may be null, the PR body names every known downstream surface and what
it will show differently; the owner of an affected consumer reviews that delta. If no consumer is known, record the
search that established it. This is a review boundary, not a Sami approval gate.

**A contract tightening carries a `## Contract change census` section.** Include it when adding a refusal, requiring,
removing or renaming a field, or changing a process/package signature. It has three parts: the search commands and
their scope, every hit with its disposition, and one rollout line. Read
[references/contract-census.md](references/contract-census.md) when you write it; it defines each part.

**Requested renders need visibility evidence.** When a plan, specification, or change commits to a render requested by
an identifiable person, the delivery record identifies that person and the channel through which they will see the live
render. A harness screenshot or a green suite can establish the artifact; it does not establish that the requester has
seen the live result. No response or approval is required.

**Review verdicts belong on the pull request.** When a PR packet or body reports a review verdict, link the comment or
review under the App identity that names the current head SHA. Envoy text and lane files can report the verdict, but are
not its record. This applies to review evidence, not ordinary progress updates.

**A figure or outcome in a PR body is pasted from the command that produced it, never transcribed.** Paste the command
and its output verbatim — counts, exit codes, test tallies, timings, sha — and if it is too long, cite the artifact path
and paste the lines that carry the numbers. A transcribed number is a claim about a run, not the run.

**Review stays on the critical path under urgency.** An incident fix is the PR most likely to be a non-fix: it is
written against the symptom, by someone who can see the outage, fast. "It's on fire" shortens the review's scope to the
change, never removes the review; a fix that cannot wait for one is shipped with the reviewer already dispatched and the
merge gated on their verdict.

**Every artifact has a consumer surface. Find it.** No plan listing workflows, or no conventional runtime, is not an
exemption: read the request and the diff, work out who or what actually consumes the thing, and exercise that. A skill
or prompt gets loaded and walked by a fresh agent. A config change gets applied and the dependent service observed. A CI
workflow gets triggered. A doc gets followed literally, start to finish, by someone who has not read the diff. If you
genuinely cannot reach the surface because the environment or credential is unavailable, the honest report is **not
merge-ready, verification blocked on X**, never "verified by inspection."

**The surface means the user's own access path.** Exercising a restricted workflow through a privileged shortcut proves
nothing about the user's experience: a minted session cookie is not the real SSO login, `kubectl exec` is not the
contractor's SSH-via-jumphost, an admin API call is not the customer's API call, and running a checker command that
doesn't drive the real surface is naming a command, not verifying. If the real path is awkward to drive, that
awkwardness is missing test tooling — building it is in scope (see the repo's "test tooling is part of the feature"
rule), not a reason to substitute.

**The surface is production-like, and the proof is attached to the PR before merge.** The agent that developed a change
tests it before production, and in production after the merge; merge-then-test-for-the-first-time is not a development
method. Whatever blocks that is the work — infrastructure, tooling, or the skill itself. Production-like means a dev
stack, staging, or a local stack with real migrations — a surface that has the resource the change touches. Merge-ready
therefore requires a **link in the PR** to that pre-merge proof (a run, a screenshot, an e2e on the dev stack) — a green
unit suite is not it — and auto-merge, which `landing-a-pr` arms once the PR is ready, is not armed without one. A code
path that only executes after merge (a deploy workflow's inline step, a post-merge helper, a production-only resource)
is untested until you have executed it against a dev stack yourself; if no surface can reach it, that missing surface is
the blocker to name, and building it is in scope.

**A proof that performs by hand a step the product owns hides a missing feature.** It passes because a person or
harness did the product's job, and the gap stays invisible for as long as the proof keeps doing it. A proof may not
perform a step the product owns; when it has to, it records the gap as an issue and names that issue at the step.

**The affected end-to-end scenarios run before merge, with outcome and duration.** This covers every change that alters
what runs: application and platform code, infrastructure, a dependency or fork pin, a harness, an agent or its prompt, a
task or its grader, a CI or deploy workflow. List the scenarios the change adds, changes or claims to fix, and every
existing scenario that exercises what it changed, as the repository's own end-to-end suite names them. Run each at the
head under review. In `## Verification`, record per scenario what ran and where, its outcome, its duration, and a link
to the run, marked one of:

- `RAN`: observed through the real surface.
- `BLOCKED`: the failed command and its authoritative record. Not merge-ready.
- `WAIVED-BY-SAMI`: a named blocker Sami explicitly waived, linked to his words. It covers that blocker on that change
  only.

The size of the change, its kind ("only a pin", "only YAML", "only a prompt"), green CI and a reviewer's or
coordinator's approval are never waivers. There is no duration threshold: record the time, and never skip a scenario for
taking it. This adds to the repository's own required checks and gates and replaces none of them.

**A blocked acceptance checkpoint is a merge blocker, not a footnote.** Refuse PR readiness and merge while any
acceptance scenario is unverified or any grounded finding remains unresolved; green tests, skipped jobs, a narrower
substitute, a coordinator's approval or a promised post-merge run do not satisfy acceptance. Build or repair the missing
surface and rerun the full scenario at the tested head; report NOT merge-ready with the failed command and authoritative
record. Never ask to omit acceptance. A named blocker that survives that work goes to Sami as a pre-merge decision.

   **This step has one exception: a named blocker Sami has explicitly waived.** Nothing else exempts it. Merging first
   and testing afterwards is the exact failure this skill exists to prevent, and "it should work" is not evidence.

   **The proof is a walk-through as the user, not a run.** For anything with a page or a workflow, the fresh agent walks
   the changed surface the way each affected user would (every role the repository's own docs name), clicks every
   element on the pages the diff touched, reads the data a user would read, and reports every defect — on the local
   stack or the lane's own dev slot, at the final head. Findings are fixed in this change; production afterwards gets
   only a smoke of the changed path.

   **Walking the change includes its consumers.** Grep the e2e and spec suites for the locators, labels and DOM the
   change invalidates, and update them in the same PR. A walk exercises the page, not the page's consumers.
5. **Sweep the docs.** Search the repo for anything the diff invalidated: renamed commands, changed flags, moved paths,
   config keys, or altered behavior across READMEs, `docs/`, skills, and AGENTS.md files. Update them here, in this
   change, with the `updating-docs` skill. Do not trust memory for which docs exist; grep.
6. **Get an oracle review.** Give the oracle the final diff and the QA report. Its findings get fixed, not acknowledged.
7. **Capture the learning, if there is one.** Did this work involve a gotcha that cost more than half an hour, or
   contradicted the documentation? If yes, run `ce-compound mode:headless`. If no, say so in one line and move on.
8. **Structure the review and draft the PR body before opening or updating it.** Multi-commit PRs are allowed when they
   improve the review narrative. Good commit groupings usually follow dependency order:
   1. Schema/storage or generated API definitions.
   2. Core logic.
   3. Wiring and integration.
   4. UI or surface behavior.
   5. Tests.

   Keep commit groups coherent; do not hide behavior changes as "cleanup" or bypass hooks; split an unclear scope.

   Read the repository PR template before drafting. Keep required fields and facts once under existing headings. Do not
   summarize with files, functions, or implementation jargon.

   Write in short paragraphs for a technically capable reader who missed the work. Ordinary bodies should be roughly
   150–250 words; small changes may be shorter, and added detail should be for material reviewer risks. Open with the
   named actor or task, its failure or limitation, and why it matters. State the changed behavior and only the mechanism
   needed for a credible causal claim. In `## Verification`, name the actor who drove the end-to-end scenario, the
   observed result, and the evidence artifact or link, with each affected scenario's outcome and duration. If acceptance
   is blocked, name the actor and scenario that cannot run and why. Never invent a user report, baseline, measurement,
   screenshot, or successful run; an unqualified `Ran` does not identify evidence. On behavior-preserving work,
   distinguish an intended invariant from a scenario actually observed. Unit tests, static checks, and CI support the
   claim but do not substitute for the observed workflow.

   Make material risks, migration order, rollout, and test coverage visible. Retain required fields, but omit empty
   optional rollout/risk sections. Do not turn a body into an internal gate checklist or an absent-metric caveat
   unrelated to its claims. Do not invent risks or bury fields for a word target. Link docs only to clarify intent.
   Finally, check that a cold reader can say what failed, what is different, and what observation supports it.

   Every factual claim in the body is verified at the bar you verify code: a statement about what a suite, gate, or
   library does names the executing source you read (the script, the installed module, the lockfile), never a code
   comment, a docstring, another PR's body, or a memory from a different suite. When you brief a reviewer, point them at
   the body's claims explicitly, not only the diff.
9. **Preserve the final tree across history-only rewrites.** Before reordering, splitting, or squashing commits, record
   the PR head and base refs with `gh pr view <PR> --json title,headRefName,baseRefName,state,commits`, then record a
   content fingerprint: `before_tree=$(git diff $(git merge-base origin/<base> HEAD) HEAD | sha256sum)`. After the
   rewrite, run `after_tree=$(git diff $(git merge-base origin/<base> HEAD) HEAD | sha256sum)` and compare them. A
   different fingerprint is allowed only for an intentional, separately explained content change. Use `git diff --stat
   <before-sha> HEAD` to inspect it. Do not push if the tree changed unintentionally. This preserves the final tree
   while making history easier to review.
10. **Resolve the PR target — never our own fork; upstream only when the change belongs there.** If the repository is a
    knives-managed fork (`knives repos` lists it), our org forks hold branches and releases and never receive PRs; a
    PR, when one is warranted, targets the **upstream** repository. Do not open an upstream PR without loading the
    `maintaining-inspect` skill (and `fork-work`/`using-knives` for the fork mechanics) and writing down, in the PR body
    or the `knives notch`, whether the change actually needs to go upstream at all. That judgment is yours to make and
    record, not a permission to ask Sami for. Most of our changes are fork members that go into the release in hand and
    never need an upstream PR, and using a fork change never waits on upstream review. Walk the `pr-preflight` skill
    before any `gh pr create` against upstream; once an upstream PR is open, its whole lifecycle is the session's under
    `maintaining-fork-pr`. Before shipping a fork member or opening an upstream PR, read
    [references/fork-and-upstream.md](references/fork-and-upstream.md) for the placement test and the release mechanics.
11. **Open or update the PR — and default to NOT opening a new one.** One line of work gets one PR. Before creating,
    inspect this session's existing PRs in the repo (`git branch -vv`, `gh pr view 2>/dev/null`, `gh pr list --author
    @me`): fold same-line work into the existing PR (`git push`, then `gh pr edit`), and use `gh-stack` only for
    genuinely dependent work that lands as one reviewed unit. Sibling one-off PRs violate the minimal-PR rule. If none
    exists, create a branch named for the change (`git checkout -b <name>` and `git push -u origin <name>`) and `gh pr
    create --head <name>` with closing keywords for the issues it resolves. Pass `--head` always, and read the URL from
    `gh pr list --head <name>`, never from the tail of the create output. Before posting, complete step 8's cold-reader check and
    retain every repository-required field exactly once. A PR without current, observed end-to-end `## Verification`
    evidence is not merge-ready; test-suite and static-checker output — `pytest`, `basedpyright`, lint, a CI link — are
    supporting evidence, not verification.

    **The unit is a reviewable slice of one line of work, and it is sized before review, not after.** A PR heading for
    its ninth review round, or a diff in the thousands of lines, should have been split at the point where its pieces
    could each be verified alone; ship those pieces in dependency order and rebase the rest onto the merged one, so the
    conflict surface shrinks early. A slice nobody uses yet may ship dark, as long as production is not broken. Splitting
    is not sibling PRs for the same line of work (the rule above still holds); it is the same line landing as verifiable
    pieces. Correctness findings are still fixed before merge, every time; the discretion is cadence (one push per
    review round), never deferral.

    **Before every push — the first one and every one after — run the repository's fast local checks on what the diff
    touches, and push only when they are green.** Fast means the lint, type, and package-local unit lanes the repo
    documents as its pre-push checks (its AGENTS.md or CI config names them), scoped to the packages the diff changes
    AND every test file that imports a module the diff changed — minutes on a laptop, not the full CI matrix, not
    deployments or image builds. Run them outside your agent shell's environment, and without the devbox instance role
    when anything touches AWS: a test can pass in every agent session and fail on every runner. Cite the green output
    lines (command + result) in the push's report; a push without them is a red head you chose not to see. Before the
    first push, and before any push after a red CI run, read [references/push-and-open.md](references/push-and-open.md)
    for the exact commands and how the scope is chosen.

    **Never pass `--label` to `gh pr create`, and never add a label the repository's CI applies itself.** A label at
    creation fires `opened` and `labeled` together, doubling every workflow that runs on `labeled`. Add a label a lane
    genuinely needs only after the current head's checks have settled, never mid-run.

    **The automated review runs once on open (and on `ready_for_review`); every later review is requested, not
    automatic.** After the PR exists, hand off to `landing-a-pr`, which owns when to ask for the next one.

    **A PR that claims to unify, dedupe, or consolidate must show a net reduction of source lines in the files it
    unifies — or explain precisely why not.** A unification PR in which no meaningful source file shrinks is a
    half-migration wearing a unification title. Added tests do not excuse it: the claim is about the duplicated paths,
    and the diff either removes one or it does not.

    **If the change touches a user-visible surface, that section must SHOW it, not describe it.** Embed the render in
    the body — screenshot, before/after pair, or a recording where motion is the point. A reviewer cannot see a diff,
    and a prose description or a hand-drawn markdown table of what it looks like is the author asking to be believed.
    "Frontend-only" is the case where this matters most, because the diff is then the least informative thing in the
    PR. Name what the image is evidence of: a component in a harness proves the component and its stylesheet, not the
    API wiring behind it. Both are legitimate; conflating them is not. Publish through the repository's screenshot skill
    where it has one (`pr-screenshots`) so the images survive.

Merging does not end your responsibility for the change: after it lands, **you** — the agent that developed it, not the
reviewer, the SRE, or auto-merge — verify it in production and record what you observed (see `landing-a-pr` §6 and the
`post-merge` skill).

Then hand off to the `landing-a-pr` skill immediately; under it you arm auto-merge at the head once the PR is ready (a
migration PR is merged by hand instead). Automated reviewers post within minutes of open, and that skill's watch
(`ce-babysit-pr` under its envelope) snapshots the full review surface at every touchpoint: after every push,
after every check completion, and always before the words "merge-ready". Do not run one manual `gh pr view` and leave;
the first snapshot is the babysitter's.

## The excuses, and why none of them work

Each excuse below is a reason given for shipping something that was never run.

| The excuse | The reality |
|---|---|
| "The mechanism says this should work." | You watched a mechanism, not a result. The gap is usually a single command away. |
| "Tests pass, CI is green." | Green CI means the code did not crash the way you anticipated. It says nothing about whether the feature does what Sami asked for. |
| "It's only a pin bump / a prompt / YAML, and CI is green." | What runs changed. Its affected scenarios run before merge, with outcome and duration; the kind of change is not a waiver. |
| "It gets live-verified after the merge / on the next deploy." | Then it is unverified now, and you are asking a human to merge on faith. Verification scheduled for after the merge is the exact failure this gate exists to stop. |
| "I disclosed the gap honestly in the PR body, so opening it is fine." | Honesty about an unverified change does not verify it. A `## Verification` section that says "could not run it, blocked on X" means NOT merge-ready unless Sami has explicitly waived that named blocker. State the exact failed command and authoritative record in the status, then build or repair the needed surface or driver at the head under test and run the scenario. Writing the gap down is step 1's ledger, never a substitute for step 4's real run. Opening the PR anyway converts "I ran out of runway" into "please review my untested code," the exact human-attention spend this gate exists to prevent. |
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
| "Sami wanted this fast." | He wants it working. Speed does not waive the proof. |
| A `## Verification` section that describes anything other than actual use by you or the QA agent. | A heading is not evidence; describe the real surface that was exercised and what happened. |
| "Verified by inspection", "verified by reading the code", or "the frontmatter parses" offered as end-to-end evidence. | Inspection can establish syntax or intent, not the user-observable result. |
| "I described what it renders as / drew the layout as a table, which is clearer than an image." | It is clearer to you, who has seen it. The reviewer has not. A table of cell contents is a claim about a render, not the render. |
| "It's frontend-only, so the diff shows the change." | A diff shows JSX and CSS. It does not show what those produce, which is the only thing under review. Frontend-only raises the bar for an image, it does not remove it. |
| "I drove it in a browser and read the DOM back, so it is verified." | That is verification, and it is necessary. It is not evidence a reviewer can see. Capture the frame from the session you already had open. |

Catch yourself writing any excuse above → go run step 4.

> **Repository note:** when acting inside a box or worktree other than the current shell's own, pass the repository
> explicitly: `gh -R <owner>/<repo> ...`. Never export `GIT_DIR` or `GIT_WORK_TREE` for this: they redirect unrelated
> Git subprocesses, including test fixtures, into the shared repository.
