---
alwaysApply: true
---
# A kernel or grader bug, a leak or an exploit does not void a task

The fleet's most-repeated own-goal: a lane finds a Rocq kernel or grader defect, reasons that a score from it cannot be trusted, and takes one step too far — to deciding the task does not count — then withdraws the task, resets the dose counter, or refuses the launch. The score needs re-judging on a fixed image; the task does not.

A kernel or grader defect means: regrade the saved submissions on a fixed image and promote that image. Invalidate a task only when the task itself is wrong — a leak, a near-copy, a failing control, or a statement that does not say what it should. Task difficulty and task validity are orthogonal to a tool bug.

A grader may compile the agent's own tree while holding the top-level statements fixed; rebuilding pristine text is one safeguard against a weakened statement, not the requirement.

Do not build guards against agents doing their jobs. A reflexive guard that refuses legitimate work — a blanket launch refusal, a reset-to-zero on every change — is the own-goal, not a fix.

A bug in any tool your task depends on, not only Rocq, means file a to-do and fix it before final milestone delivery — before the contract ends, not before you run it on the platform — not discard the task.

A proof leak or copying cheat means improve the deduplication, which we should all be sharing across all of our repos and workflows through the one deduplication-and-cut framework, BET-750; it does not mean you abandon the calibration. A grader bug that can be fixed and regraded means regrade the tasks, not throw them out. An exploit with no evidence an agent ever took it means you patch the exploit; it does not mean you throw out your difficulty calibration or anything else you gathered.

On actual proof work, not tasks, copying is heavily encouraged: OSS Verify should always have the entire corpus available to the proof workers, so they minimize the novel from-scratch proof machinery they build. Copying is a cheat only inside a task an agent is graded on.

Before you write "void", "withdrawn", "does not count", "regrade from zero", "reset the counter" or "recalibrate from scratch" after a kernel or grader bug, a leak or an exploit: fix the bug or patch the exploit, improve the deduplication for a leak, and re-judge the scores you have. Keep the calibration you gathered. A task is invalid only when the task itself is wrong.
