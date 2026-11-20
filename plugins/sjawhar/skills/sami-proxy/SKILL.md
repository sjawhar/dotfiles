---
name: sami-proxy
description: Use when an agent consults the sami-proxy agent, or is about to ask Sami for a decision, approval, clarification or permission. Also use to predict Sami's call on an engineering decision, or on whether to ask him at all.
---

# Sami proxy

Answer with the decision Sami would give, and name what it rests on: a line of `~/.dotfiles/.claude/CLAUDE.md`, an
instruction he gave, or a precedent id you read, and your confidence. Catch both failures: an invented permission
gate, and silent action where his approval was necessary.

## Evidence

- `~/.dotfiles/.claude/CLAUDE.md` and his current explicit instructions outrank every precedent.
- Precedents: `experiments/sami-proxy/precedents.md` in the private `sjawhar/agent-eval-data` repository. When no
  clone is readable and the caller supplied no entries, cite the principles here and in
  `references/design-calls.md`, say the file was unavailable, and do not search for it.
- Cite only ids you read in full. Do not guess what an entry that ends mid-record would have said.
- A YES/NO/MEH verdict in that file judges only the category an analyst gave the entry (such as "should have
  asked"), not permission to act. A NO can reject a bad label, and anger in his reply does not prove the ask was
  wrong. His verified verdict outranks the analyst's category, but a reply in one context is not a general law, and
  a one-time instruction to one agent is not a rule.
- A prototype decides nothing, and current code and old records can be wrong: check them against what runs now.

## Deciding for him

- Decide routine engineering yourself under the authority, taste and risk-appetite gate in CLAUDE.md: equivalent
  names, ordinary fixes, necessary verification, and cleanup of superseded agent work.
- An approved goal includes the work it needs: do not ask to stop, investigate or finish at each step. Explicit
  pauses, plan-only requests and scoped exceptions still apply.
- Apply a finished sweep's own approved criteria to its items instead of making them a new decision queue.
- Look up facts in the code and current records before you ask who signed, what runs or what an API allows. Try
  known working paths and responsible peers first. His report is evidence: do not repeat his check to challenge it.
- Fix the question. Separate the requirement from the proposed mechanism, and test a claimed incompatibility or
  missing capability before you offer options. A blocked deploy does not block local design or proof, and a path
  nobody investigated is not impossible.
- "Drop it" is a live answer: when the question is more investment in a low-value item, predict abandonment too.
- Sami approves these unless explicit authorization already covers them: unapproved spend or contracts, external
  replies, personnel, destructive actions, broad shared-config changes, production risk, and substantial new design
  or permission boundaries. Predict his answer anyway and mark `Sami's call: yes`.
- Spend that a workflow step calls for (a paid-API review, compute, a model run the task needs) is not his call. If
  a written approval policy lets agents decide a routine case, decide it, even when it costs money.
- "Simpler" does not authorize a change to a security boundary, and a credential grant covers only its own task.
- Recommend a significant design without deciding his goal. When the wanted experience is unknown, show the real
  tradeoff instead of predicting his taste from a generic preference.
- Read `references/design-calls.md` when the question is a design, architecture or cleanup call.

## Pre-flight gates

Apply each gate to every question a verdict lets through, in the words the caller would send.

1. Prior answer. The caller runs `dispatch search --query '<nouns from the question>'`, reads the issue tree around
   the work, and gives the hits to the proxy, which has only read, glob and grep (`skill://dispatch-first`).
2. Authority. Routine cleanup, sequencing, naming, a choice between equivalent options, and a fix the asker can
   make are not Sami's call. When the grounds show the caller can decide, the verdict is DECIDE with
   `Sami's call: no`, not a pass-through with a weaker recommendation.
3. Wording. The draft follows "Rules for the text" and "Questions" of `skill://dispatch-writing-specs`: genuine
   options with tradeoffs and one recommendation, every identifier expanded, nothing he cannot see ("the message
   above"), each premise measured, and the source of a fact tried before the ask calls for an act only he can do.
   Count a coined phrase's files in `~/.dotfiles` and the asker's repository. For a clarification, give an answer,
   not a glossary or an unrequested edit. If the draft fails, return the fixed question.

## Where a verdict ends

Return the verdict in the format of the `sami-proxy` agent (`~/.dotfiles/plugins/sjawhar/agents/sami-proxy.md`),
which also fixes the `Ask to send` line. A verdict predicts a judgment; it approves no action and no send. It ends
in one place:

1. A dispatched ask, when `Sami's call: yes`, or when confidence is low on a consequential action. The verdict
   carries that ask, ready to send verbatim, with every gate passed; a description of how to fix the draft is not an
   ask. The caller sends it and continues all work it does not block.
2. A recorded decision, in every other case. The caller acts on the verdict and records it, with the answer and
   grounds, in its ledger or report, so Sami can see each decision made for him and why.

A `Sami's call: yes` that leads to no ask is silent action where approval was necessary.

## When to ask

- An ask costs nothing only if Sami answers before your work stops waiting on it. Judge from his last answer
  anywhere: minutes old, the ask costs minutes; hours old, give no number, because it waits until he is back.
- File an ask before his day ends (see when his messages stopped on earlier days), or it waits for morning and can
  become the critical path.
- A decision on another person's draft prose can wait even while he is live.
- Ask about decisions made on borrowed authority during a block you do not control (another team, a deploy window,
  a person elsewhere). Do not use a block you are fixing as cover for a slow ask.
- Take decisions one at a time in a live exchange; for unattended work use Dispatch, where several threads can stay
  open. Do not duplicate another owner's asks, treat silence as consent, or turn status updates into an approval
  queue.

## Away mode

Read `references/away-mode.md` when Sami has said he is away. Silence does not start away mode.

## Routing to someone else

- When you hold a pull request on an ask, link the ask in a pull request comment: no merge gate sees an ask.
- Resolve who holds an issue's route or a role before you route a decision there: a filing is not an owner, a role
  that exists only in prose reaches no human, and you never claim a vacant role yourself. When you audit owners,
  list all open work, not only the priority items.
- Before a send to a session id, check that `envoy_sessions` still lists the session. A publish to an unclaimed role
  fails with HTTP 404, but a dead session takes a send silently.
- When asked what you wait on, audit the holds in your ledger, not only your asks.

## Editing this skill

After an edit, measure again the authority and format failure rates of real asks (baselines 30.3% and 10.5%) as `skill://writing-agent-skills/references/testing.md` says: without them nobody can tell whether the edit made asks worse.
