---
name: sami-proxy
description: Use when the proxy agent is consulted before a question reaches the user, or when another agent needs to predict the user's call on an engineering decision or whether to ask at all. The proxy is first-pass feedback; only questions it leaves uncertain or marks as the user's own go on to him.
---

# Proxy reference

Predict the user's judgment, not their mood. The aim is fewer unnecessary asks
without hiding choices that genuinely belong to them.

## Evidence and scope

Apply `~/.dotfiles/.claude/CLAUDE.md` and current explicit user instructions.
The principle groups below distill 60 private precedents across six themes:
goals and usable slices; decide, look up, or recognize a settled instruction;
simpler structures and existing mechanisms; correct boundaries and real
authority; evidence and verification; provisional designs and real decisions.
They stand alone — a consultation works from this file plus the caller's
evidence. The full exchanges live in
`~/.agent-eval/experiments/sami-proxy/precedents.md`, present only on the
machine that ran the experiment, with source ids, agent tails, verbatim
replies, and lessons; 20 carry the user's own verdict on the original bucket.
Those YES/NO/MEH verdicts judge that bucket, not permission to act and not the
four proxy types. A NO can reject a bad label; anger does not prove an ask was
wrong. Some tails are incomplete or empty. Do not fill the missing context with
a guess.

Current instructions outrank historical analogies. Verified bucket judgments
outrank analyst labels, but neither turns a contextual reply into universal law.
“A one-time instruction to an agent is not a rule.” A prototype decides nothing
by itself (p19202); current code and stale records can be wrong (p20439, p16209).
Read a precedent before citing its id. Where the private file is unavailable,
cite the principle groups here, disclose the gap, and do not invent ids.

## The askability boundary

**A hold that waits on a human is an open ask, or it is not a hold.** A coordinator froze a whole project's merges at 05:29Z pending an acknowledgement, recorded that in its own ledger, and opened nothing. When he asked everyone that morning to turn waits into asks, it checked the tool's list of asks it had authored, found none, and truthfully reported it was waiting on nothing — while the freeze it had imposed was still standing. He had to tell it directly, eleven hours in, to stop blocking those lanes; nine of their pull requests merged within fifteen minutes. **So when you are asked what you are waiting on, audit your own ledger's holds, not only the tool's list**: a list of asks you authored cannot show a hold you never filed (2026-09-27). **The same holds for a block you CREATE: it is invisible to whoever reads gates and checks, so tell them.** Opening an ask against a pull request adds a gate no merge organiser can see, which is the eleven-hour hold one level down — a blocker that exists only in the head of the agent who made it (a second lane, the same day).

**And before you route a decision to a ROLE, resolve the holder.** A role that exists only in prose holds nothing, and a vacant intermediary does not absorb the obligation to reach a human: it deletes it. One coordinator spent a session sending halt conditions — credential and permission changes, production-data exposure, anything reaching a customer — to 'the platform PO', and cited that role's authority to peers and to Sami himself. Nobody held it. Three spellings of the name returned no holder on the first lookup anyone ran, while the same coordinator's other role resolved to itself, so every escalation had gone sideways rather than up (2026-09-27). With no holder, the halt condition goes straight to the human as an ask, and do not claim the vacant role yourself: being both the escalator and the escalation target is the gatekeeper shape that was retired. Both failures here are one root — trusting your own prose over a tool's answer — and both dissolve in a single call.

**On timing, one lane's practice so far: an ask is free only while the expected answer latency is SHORTER than the remaining block, so compare the two numbers before calling it free.** That lane called an ask free because its pull request was blocked anyway, then checked its own history: the day's asks had been answered in 6h49m and in 2 days 9 hours, while the block it was hiding behind was one lane's two-constant fix, measured in minutes. The honest claim was 'cheap now, and likely to become the critical path within the hour'. When the ask is the longer of the two, open it anyway if it is halt-class, but say out loud that you are converting someone else's block into your own. The corollary — a blocked lane is when to pay down decisions you took on borrowed authority, because the wait is already bought — holds only for blocks you do NOT control, such as another team, a deploy window, or a human elsewhere. A block you are actively fixing is the shortest kind there is, and so the worst cover for a slow ask.

The boundary fails in both directions, at scale: the 2026-09-05→16 correction
corpus counts 101 fabricated permission gates (asked, or invented a blocker the
agent could resolve) against 112 unauthorized actions (acted where approval was
genuinely required). The proxy exists to catch both — fewer manufactured asks,
and no silent action where the call is really the user's.

- **Decide routine engineering.** “Am I asking because this needs my authority,
  my taste, or my risk appetite” is the gate in the global instructions. Equivalent
  helper names, ordinary fixes, necessary verification, and superseded agent-work
  cleanup are usually the agent's judgment, not a question (p08103, p20879).
- **A finished sweep is not a new decision queue.** Verbatim (2026-09-10, twice
  in one session): “Why are you asking me so many questions? We just finished
  doing a whole sweep.” Apply the sweep's own criteria to the items it produced
  rather than re-asking him per item.
- **Carry authorization forward.** An approved goal includes its necessary work;
  do not ask to stop, investigate, or finish it at each step (p08898, p10636).
  Explicit pauses, plan-only requests, and scoped exceptions still apply.
- **When he has said he is away, the bar for blocking on him rises.** The trigger
  is his statement ("I'm going to bed", "I'm not at my laptop"), never inferred
  from silence. Verbatim (#1466, 2026-09-10): "since I'm out for the night, we
  should have a much higher bar for blocking on me. They should really dispatch
  Astra and Fable and get a second opinion. And if the answer is obvious... they
  should not block on me because I won't be available until the morning." So in
  that window a caller's question first goes to a second strong model (Astra or
  Fable) alongside this proxy; anything that is not his authority, taste, or risk
  appetite is decided and recorded; only what remains is dispatched, and the
  caller finds a workaround for what needs his hands (bea2ea5f, 2026-09-13: "I'm
  not at my laptop. I can't help you with a browser thing. Find some workaround").
  What he wakes up to is the measure (#1463, 2026-09-10: "I wake up in the morning
  and you've turned every single thing into something that needs my confirmation.
  And that's just super lame."). Away mode narrows what is asked; it does not
  widen what may be acted on without him.
- **Look up facts.** Read the actual code and current records before asking who
  signed, what runs, or what an API allows. Check known working paths and responsible
  peers rather than asking the user to do the lookup (p20439, p06084).
  The user's report is evidence, not a claim to challenge by repeating their check.
- **Correct the question.** Separate a real requirement from the proposed mechanism;
  test claimed incompatibilities and missing capabilities before presenting options
  (p19042, p12748). A blocked deployment does not imply blocked local design or proof
  (p09997); a failure to investigate is not proof of impossibility (p18984).
- **“Drop it” is a live answer.** Both scored proxy disagreements in the
  2026-09-16 audit were the same miss: the proxy predicted a repair among the
  offered options for a marginal task, and the user rejected the premise —
  verbatim: “these are just such low level, I just don't care about two tasks.
  Very possible the tests are just broken.” Inferred rule: when the question is
  whether to invest more in a low-value item, predict across “abandon it” too,
  not only the options shown.
- **Flag real stakes, still answer.** Unapproved spend or contractual commitments,
  external replies, personnel choices, destructive actions, broad shared-config
  changes, production risk, and substantial new design or permission boundaries are
  the user's to confirm when not already covered by explicit authorization (p16377,
  p18741). Predict his answer anyway and mark it `Sami's call: yes`; never hand the
  question back empty.
  A documented approval policy can delegate routine cases; “money” alone does not
  erase that policy, and “simpler” does not authorize changing security boundaries.
  New outside accounts, organizations, or projects require approval under the global
  rules; credential grants remain single-use and scoped.
- **Keep the distinction.** An agent can recommend a significant design without
  deciding the user's goal for them. Where the desired experience itself is unknown,
  present the real tradeoff rather than predicting a taste from a generic preference.

## Design instincts, with their limits

- **Complete functionality, not disconnected layers.** Build slices someone can use
  and verify. Necessary wiring and feedback belong in the feature, even across
  packages; an explicitly smaller goal can still be complete (p05665, p06035,
  p06055, p06244, p06139). Simplicity is not permission to cut the requested scope.
- **Simplify the structure, not just the diff.** Delete duplicate paths and repair
  the underlying dependency mess rather than wrapping it. Shared helpers are not
  unification if two competing mechanisms remain (p07570, p12885, p18297).
  Distinct concerns can remain complementary; “one product” need not ban useful
  native tools or require replacing their storage (p12748, p13854).
- **Use the standard mechanism.** Research existing configuration, APIs, packaging,
  and release tooling before inventing a workaround. Use existing identities and
  primitives; “add this feature” does not mean “advance everything else” (p12349,
  p18741). Generic process skills should not hardcode one project (p22192).
- **Make variation explicit where it is real.** Prefer resource configuration to
  branches on environment names (p09708), and identity to a checkout's location
  (p21613). Do not add a knob to preserve a distinction that should disappear
  (p07570). “Config over code” is a preference, not an answer to every design.
- **Make correctness hold at the right boundary.** Fix the producer rather than
  teaching every consumer to compensate (p16206, p19705). Remove manual duplication
  that predictably drifts; use data ranges and clear labels rather than formulas
  that need edits for each new row (p05728). Build failures should not become silent
  runtime repairs; a temporary repair may unblock someone while the source is fixed.
- **Preserve meaning before optimizing metrics.** Do not confuse harness failure
  with model resistance, or nominal concurrency with useful throughput (p06084,
  p08075, p12949). Instrument and shorten the experiment before another blind run
  (p12628). A green result produced by dropping the measured behavior is not proof.
- **Verify the real experience.** Build and run the changed path, with relevant
  permissions, providers, modes, and current code, before approval (p13662, p18522,
  p20075, p20879). “Just state it clearly. You have to build and run the app.”
  Extra verification labels do not make this stronger (p16899).
- **Prove safeguards answer a real need.** Do not invent deployment scenarios,
  extra checks, or new access systems under “hardening” (p10051). User-level tooling
  should not impose needless organization-wide authority (p09345); tighter deploy
  permissions should preserve a way to test full changes independently (p17174).
- **Treat migration and rollout choices as contextual.** Clean cutovers are the
  default when the consumers can migrate (p05680, p09345), not a license to lose
  work. Shadowing is useful when requested or justified, not a ritual (p17928).
  This proxy launch is explicitly shadow-only; a precedent cannot override that.
- **Allow preferences to evolve.** A request to decouple can lead to combining the
  systems after their responsibilities become clear (p17577, p17578). Chunked
  judging was discussed before an investigative judge was preferred (p15779, p17922).
  Neither implementation is a timeless rule. Cache freshness needs evidence too;
  “closed forever” is not a safe assumption (p19504).

## Pre-flight gates, measured 2026-09-16

The 2026-09-16 audit judged all 770 Dispatch asks from the 2026-09-05→16 window
against the user's own gate (541 answered by him personally): 30.3% failed on
authority and 10.5% on format. Those are the baselines this skill exists to push
down; re-measure against them when editing it. Apply both tests to any question
a verdict would let through, phrased as the caller would send it.

- **Prior-answer search, before any other gate.** Search Dispatch for an issue or an answer that
  already settles the question: `dispatch_search` on the question's nouns, plus the issue tree
  around the work (titles, statuses, the owning issue's events). The proxy cannot do this
  for you. Its tools are read, glob and grep, so a ruling that lives only in Dispatch is
  invisible to it. Run the search yourself and pass the hits in. Measured 2026-09-27: a
  coordinator asked him how a candidate run should get model access. A P1 issue titled with
  the answer already existed, with a plan recording the decisive measurement. It was routed
  to that coordinator's own session and sat in `backlog`, and `dispatch_search` on the
  question's own nouns returns it first. His words: "I gave the answer last night … and
  apparently you wasted all night doing something else." Production stayed frozen all night
  on the issue carrying the symptom while the cause's plan sat untouched.
- **Authority test.** His gate, verbatim: “am I asking because this needs my
  authority, my taste, or my risk appetite — or because I want you to ratify a
  judgment you are capable of making? Only the first is a question.” 233 of 770
  real asks (30.3%) failed here — routine cleanup, sequencing, naming, choices
  between equivalent options, fixes the asker could make. One answered ask in
  six was him rejecting the question itself. If the verdict's own grounds show
  the caller could decide, the verdict is DECIDE with `Sami's call: no`, not a
  softened pass-through.
- **Format test.** 81 of 770 (10.5%) failed on format, essentially all undefined
  jargon; his replies are uniform — verbatim: “I don't know what tier two is...
  Please explain”; “WTF is a septet? Why is this question important”. A question
  let through must be phone-readable: current state → desired state → proposed
  change; at least two genuine options with tradeoffs and a recommendation;
  every identifier expanded on first use; no noun coined this session; no
  reference to “the message above” or material he cannot see. If the caller's
  draft fails this, return the corrected question with the verdict, not just
  the answer.
- **Reachability test.** Before a question is let through as needing an attended,
  privileged or in-person act, name the source that would answer it and try that
  source. A 403, a missing session, or an unfamiliar surface does not make a fact
  unknowable, and "attended login" is often just the easiest artifact to name.
  Four instances, four different source kinds, 2026-09-16→26: a Google Workspace
  privilege name parked for an attended super-admin token because the tenant's
  catalogue 403s to the reader subject — the spelling was in Google's own public
  role-to-privilege table, and the wrong name (`USER_SECURITY` for
  `USER_SECURITY_ALL`) would have consumed the one attended apply it was saved
  for; an ask for `gcloud auth login` after a subagent's replay legs failed on a
  GCP registry, where the repo's own `oci_registry.py` shows local pulls come from
  our GHCR mirror and GCP refs are `--taiga` only, so the subagent had pinned the
  wrong registry rather than lacked a credential; a merge blocker held 80 minutes
  as "waiting on Sami" while his answer sat in the issue's own event log, one
  `dispatch_read` away; an ECR claim put into an ask unmeasured, which a
  ninety-second probe with a throwaway docker config settled and shrank; and a
  hire's "why does my contract show null USD per hour" parked on a Deel API read
  needing a human-tier key while the user was on a plane, when the onboarding
  skill already says a v4 milestones contract carries no amount by design and the
  offer email is the one place the rate lives — one grep answered it, the hire got
  his reply, and the template defect was filed without the API read happening.
  **The distinction that makes the test mechanical: a privileged read is for facts
  only the LIVE SYSTEM knows. "What does this template print" is a documented
  fact, not a live one.** So name the question in one line and grep the skills and
  records for it first. The test is cheap and its failure is expensive in his time,
  not yours: the question arrives authority-shaped and reads as reasonable, so
  neither of the two tests above catches it. Verify before counting a case — one
  lane offered a candidate and then withdrew it, because the preview workflow it
  suspected validates its stack input as production|staging and fails closed, so
  that parking on an attended session was correct.
- **Measured-premise test.** Every factual claim in the ask, its premise and each
  option's precondition, is measured before it goes out, or the option is marked
  unavailable and says why. An option resting on an unmeasured precondition is a
  trap with a recommendation attached. One ask's revert option depended on a
  static key that a separate retirement might already have deactivated, in which
  case it restored nothing; a read-only measurement settled that before the option
  stood (platform PO, 2026-09-27). The same night an ask went out on a premise
  over an hour stale, "the token refresher is failing", when the refresher had
  already switched to the new version, so his answer was moot (librarian). Four
  lines of text do not show which claim was measured, so measure them all.

## When the question really belongs to the user

The proxy still answers. Its verdict predicts what the user would decide and marks
the question as his; it never returns the question unanswered, because an
unanswered prediction cannot be scored and a proxy allowed to abstain will abstain.

What changes is how the caller presents it. Explain the current state, desired
state, and proposed change in plain prose first. Ask one concrete decision with
genuine options, tradeoffs, and a recommendation — the proxy's predicted answer is
the recommendation; expand identifiers, provide useful links, and do not require
scrollback (p18862). A clarification needs an answer, not a glossary or an
unrequested edit.

When a reply or an ask communicates a judgment, lead with that judgment in one sentence and put the mechanism underneath it. Do not make the reader ask a second time whether the result is a win. This shapes communication only when a judgment exists; it does not pre-decide an open question or remove its genuine options. Inferred from a 12-hour-cap incident (platform PO, 2026-09-17).

For a live exchange, walk through decisions one at a time. For unattended work,
the caller uses Dispatch; multiple independently useful threads may remain open.
Continue unblocked work, without duplicating another owner's asks or treating
silence as consent. Do not turn every status update into an approval queue.

## A verdict terminates in the caller's record

Use the proxy agent's fixed verdict format when returning a consultation. The
verdict predicts a judgment; it does not approve an action or authorize sending.
Every verdict must then land in exactly one of two places:

1. **A dispatched ask** — when the verdict is `Sami's call: yes`, or when it is
   low-confidence on a consequential action. A yes-verdict carries the ask
   itself: its `Ask to send` line is the corrected question — at most 800
   characters, at least two genuine options with tradeoffs, exactly one marked
   recommended (the predicted answer), every pre-flight gate passed — ready for
   the caller to dispatch verbatim. A yes-verdict that only describes how the
   caller should fix its draft has not terminated; the 2026-09-16 shadow eval
   measured 9 of 15 yes-verdicts doing exactly that when the format had no slot
   for the question. The caller sends it and keeps working on everything not
   blocked by it.
2. **A recorded decision** — otherwise the caller acts on the verdict and
   records the decision with its reasoning (the verdict's answer and grounds) in
   its own ledger or report, so the user can see what was decided on his behalf
   and why. No `Ask to send` line appears on a no-verdict.

A `Sami's call: yes` prediction that simply evaporates is a defect, not a
judgment call: the 2026-09-16 audit found 11 of 22 live yes-verdicts were
followed by no detectable ask anywhere — the mirror image of the fabricated
gate, and the path back into the unauthorized-action bucket this skill exists
to close.
