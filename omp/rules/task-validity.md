---
alwaysApply: true
---
# A kernel or grader bug does not void a task

The fleet's most-repeated own-goal: a lane finds a Rocq kernel or grader defect, reasons "a score from this cannot be trusted," and takes one step too far — to "this task does not count" — then withdraws the task, resets the dose counter, or refuses the launch. The score needs re-judging on a fixed image; the task does not. (dispatch://INFRA-1239 traced eight instances of exactly this step, re-derived from scratch each time because the rule lived only in places a session had to already be looking.)

Sami's standing rulings, in his own words:

- 2026-11-04, standup (dispatch://INFRA-460): "Bugs in rocq do not invalidate tasks" — "I've made this point in standup at least three times. STOP MAKING THIS GODDAMN MISTAKE." A kernel or grader defect means: regrade the saved submissions on a fixed image and promote that image. Invalidate a task only when the task itself is wrong — a leak, a near-copy, a failing control, or a statement that does not say what it should. Task difficulty and task validity are orthogonal to a tool bug.
- 2026-11-04, standup (dispatch://INFRA-460): "Not if you hold the top-level statements fixed." A grader may compile the agent's own tree while holding the top-level statements fixed; rebuilding pristine text is one safeguard against a weakened statement, not the requirement.
- 2026-11-01, standup (dispatch://INFRA-460): "stop building guards against agents doing their jobs." A reflexive guard that refuses legitimate work — a blanket launch refusal, a reset-to-zero on every change — is the own-goal, not a fix.
- 2026-11-05, standup (dispatch://INFRA-460): "if you find a bug in the software that is used in your task, it doesn't mean that you throw out the task. You just file the to-do to fix the bug before shipping. Shipping here doesn't mean before running it on the platform. It means before the contract ends, before final delivery of the entire milestone." The general form: a bug in ANY tool your task depends on (not only Rocq) means file a to-do and fix it before final milestone delivery — not discard the task.

Before you write "void", "withdrawn", "does not count", "regrade from zero" or "reset the counter" after a kernel or grader bug: fix the bug in the image and re-judge the scores on it. That invalidates a task only when the task itself is wrong.
