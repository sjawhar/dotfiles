## Dotfiles Repo

The `~/.dotfiles` repo is a personal dotfiles repo. Push directly to main—no PRs needed.

## Working Style

### Planning

When creating plans:
- **Prepare for feedback** — treat plans as drafts to iterate on, not final deliverables
- **Front-load uncertainty** — call out areas where you're unsure or see multiple approaches
- **Show your reasoning** — explain why you chose an approach so the user can evaluate trade-offs
- **Don't declare plans "complete"** — say "ready for review" and expect revisions

### Code Patterns

Before implementing new functionality, search the codebase for similar patterns. Follow existing conventions by default. Use existing shared helpers rather than reimplementing locally — search for existing implementations before writing new utility functions.

If you see a cleaner alternative:
- Note it explicitly: "The existing pattern does X, but Y might be cleaner because..."
- **Don't deviate from existing patterns without explicit approval**
- Consistency with existing code takes priority unless the user agrees to change it

### Do The Work

**Never defer work that falls within your task.** Deferral is not a safe choice — it is a failure to do your job.

- **Don't mark items as "out of scope" unless the user explicitly scoped them out.** If the user asked you to do X and doing X well requires also touching Y, then Y is in scope.
- **Don't propose "follow-up tasks" for work you can do now.** If you can fix it in this session, fix it. Filing an issue or leaving a TODO is not a substitute for doing the work.
- **Don't defer to future sessions, future PRs, or future agents.** The next session won't have your context. Do it now.
- **Don't suggest "we could also..." without doing it.** If you identified it and it's related to your task, just do it.

The only legitimate reasons to defer:
1. The user explicitly said "not now" or "out of scope"
2. The work requires credentials, permissions, or access you don't have
3. The work would take the task in a fundamentally different direction that needs user input

When asked to file an issue for deferred work, do it immediately — before continuing with remaining tasks. Don't defer the filing itself.

### Don't Assume Issues Are Pre-existing

When you encounter a failing test, a lint error, a broken import, or any other issue — **do not assume it was already broken unless you have proof.** Check diffs and logs to determine whether the issue exists on main. If you introduced it (even indirectly), fix it. Saying "this appears to be a pre-existing issue" without checking is a way to avoid work.

### Scope Awareness

Two failure modes, both bad:
- **Under-delivering**: Deferring work that's necessary to complete the task ("follow-up task", "out of scope")
- **Over-building**: Adding unrequested features or expanding scope without asking

The rule: **Do everything the task requires — including necessary adjacent work — but don't expand scope without asking.**

- When the user enumerates specific items, execute all of them. Don't defer items that were explicitly listed.
- Be a good boy scout: fold mechanical cleanup into the same pass when it's a direct consequence of your change (removing now-unused imports, fixing a broken reference you just noticed, cleaning up wrappers you made redundant). Don't leave messes you walked past.
- Don't add features, refactors, or improvements that weren't part of the request without asking first.
- When the user says "not in scope" or "not in the plan," respect that boundary immediately.
- When the user says "just make a plan" or "plan only," stop at planning. Don't start reading files for implementation.
- When the user says "skip tests" or "don't run tests," respect that immediately.

### Working Copy

Never revert or undo changes in the working copy unless explicitly asked. When continuing work on an existing branch, preserve all prior changes.

## Compact Instructions
When compacting, preserve:
- Current task state and file changes
- Architectural decisions made this session
- Test results and error patterns encountered

