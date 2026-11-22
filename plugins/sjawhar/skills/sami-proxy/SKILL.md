---
name: sami-proxy
description: Use when an agent consults the sami-proxy agent, or is about to ask Sami for a decision, approval, clarification or permission. Also use to predict Sami's call on an engineering decision, or on whether to ask him at all.
---

# Sami proxy

Answer with the decision Sami would give. Name what it rests on: a line of `~/.dotfiles/.claude/CLAUDE.md`, an instruction he gave, or a precedent id you read, and your confidence. Catch both failures: an invented permission gate, and silent action where his approval was necessary.

**Done when:** the verdict ends in one place, as "Where a verdict ends" lists.

## Evidence

- `~/.dotfiles/.claude/CLAUDE.md` and his current explicit instructions outrank every precedent.
- **Precedents:** `experiments/sami-proxy/precedents.md` in the private `sjawhar/agent-eval-data` repository. When no clone is readable and the caller supplied no entries:
  - cite the principles here and in `references/design-calls.md`;
  - say the file was unavailable;
  - do not search for it.
- Cite only ids you read in full.
- Do not guess what an entry that ends mid-record would have said.
- A YES/NO/MEH verdict in that file judges only the category an analyst gave the entry (such as "should have asked"), not permission to act or which verdict type the proxy returns.
  - A NO can reject a bad label.
  - Anger in his reply does not prove the ask was wrong.
  - His verified verdict outranks the analyst's category.
  - A reply in one context is not a general law.
  - A one-time instruction to one agent is not a rule.
- A prototype decides nothing.
- Check current code and old records against what runs now: they can be wrong.

## Deciding for him

- Decide routine engineering yourself under the authority, taste and risk-appetite gate in CLAUDE.md: equivalent names, ordinary fixes, necessary verification, and cleanup of superseded agent work.
- **An approved goal includes the work it needs:** do not ask to stop, investigate or finish at each step.
  - Explicit pauses, plan-only requests and scoped exceptions still apply.
- Apply a finished sweep's own approved criteria to its items instead of making them a new decision queue.
- Look up facts in the code and current records before you ask who signed, what runs or what an API allows.
  - Try known working paths and responsible peers first.
- **His report is evidence:** do not repeat his check to challenge it.
- **Fix the question.**
  - Separate the requirement from the proposed mechanism.
  - Test a claimed incompatibility or missing capability before you offer options.
  - A blocked deploy does not block local design or proof.
  - A path nobody investigated is not impossible.
- **"Drop it" is a live answer:** when the question is more investment in a low-value item, predict abandonment too.
- Sami approves these unless explicit authorization already covers them: unapproved spend or contracts, external replies, personnel, destructive actions, a shared-config change that can break other work or restart running sessions, a change that can break a production service, a design that goes past the scope he approved, and a new or changed permission boundary (who may read, change or run what).
  - Predict his answer anyway.
  - Mark `Sami's call: yes`.
- Spend that a workflow step calls for (a paid-API review, compute, a model run the task needs) is not his call.
- If a written approval policy lets agents decide a routine case, decide it, even when it costs money.
- "Simpler" does not authorize a change to a security boundary.
- A credential grant covers only its own task.
- When the design options give him different results (what a user sees, what it costs, what later work can build on), recommend one.
  - Do not choose his goal for him.
  - When the wanted experience is unknown, show the real tradeoff instead of predicting his taste from a generic preference.
- Read `references/design-calls.md` when the question is a design, architecture or cleanup call.

## Pre-flight gates

Apply each gate to every question a verdict lets through, in the words the caller would send.

1. **Prior answer, first** (`skill://dispatch-first`). As the caller:
   - run `dispatch search --query '<nouns from the question>'`;
   - read the issue tree around the work (titles, statuses, the owning issue's events);
   - give the hits to the proxy, which has only read, glob and grep.
2. **Authority.** Routine cleanup, sequencing, naming, a choice between equivalent options, and a fix the asker can make are not Sami's call.
   - When the grounds show the caller can decide, the verdict is DECIDE with `Sami's call: no`, not a pass-through with a weaker recommendation.
3. **Wording.**
   - The draft follows "Rules for the text" and "Questions" of `skill://dispatch-writing-specs`:
     - genuine options with tradeoffs and one recommendation;
     - every identifier expanded;
     - nothing he cannot see ("the message above");
     - no unmeasured number ("about a day of work");
     - each premise and each option's precondition measured or marked unverified with the measurement it lacks.
   - For a clarification, give an answer, not a glossary or an unrequested edit.
   - If the draft fails, return the fixed question.
4. **Coined phrases.** List each term the draft uses as a name: every backticked term, hyphenated compound and noun phrase of two or more words that labels a thing.
   - Count the files in `~/.dotfiles` and the asker's repository that contain each one.
   - **A single-digit count marks the phrase as coined this session:** expand it or drop it.
   - **The count misses a coinage made of common words,** so read the draft for those too.
5. **Reachability.** Before the ask calls for an act only he can do, name the source of the fact and try it.
   - **A 403, a missing session or an unfamiliar surface does not make a fact unknowable:** look in public reference tables, the repository's config, event logs, a template's documentation and cheap read-only probes, and grep the skills and records for the question written in one line.
   - Keep a privileged read for facts only the live system knows.

## Where a verdict ends

Return the verdict in the format of the `sami-proxy` agent (`~/.dotfiles/plugins/sjawhar/agents/sami-proxy.md`), which also fixes the `Ask to send` line. A verdict predicts a judgment. It approves no action and no send. No precedent overrides that.

1. **A dispatched ask,** when `Sami's call: yes`.
   - Mark it yes also when `Confidence: low` and the action cannot be undone later (a send, a deletion, spend).
   - The verdict carries that ask, ready to send verbatim, with every gate passed.
   - A description of how to fix the draft is not an ask.
   - As the caller, send it.
   - As the caller, continue all work it does not block.
2. **A recorded decision,** in every other case. As the caller:
   - act on the verdict;
   - record it, with the answer and grounds, where Sami reads your work (your issue, pull request or final report), so he can see each decision made for him and why.

A `Sami's call: yes` that leads to no ask is silent action where approval was necessary.

## When to ask

Read `references/when-to-ask.md` before you file an ask, or when the question is whether an ask can wait: what an ask costs, when to send it, and how to send several.

## Away mode

Read `references/away-mode.md` when Sami has said he is away. Silence does not start away mode.

## Routing to someone else

Read `references/routing-a-decision.md` when the decision goes to someone other than Sami, or your work holds on a person (Sami too), another agent or a check, and when asked what you wait on.

## Editing this skill

After an edit, measure again the authority and format failure rates of real asks (baselines 30.3% and 10.5%) as `skill://writing-agent-skills/references/testing.md` says: without them nobody can tell whether the edit made asks worse.
