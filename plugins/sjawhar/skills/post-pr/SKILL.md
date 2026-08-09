---
name: post-pr
description: "Use after opening a PR, after pushing new commits to an existing PR, or when a PR's CI goes green — any time PR state just changed and the work looks done. Also before telling Sami a PR is merge-ready. Sweep so the PR lands complete: accurate PR record, updated docs, review findings fixed, nothing left for a follow-up PR."
---

# Post-PR Sweep

A PR was just opened or updated. Anything discovered after it merges becomes a follow-up PR — which means it belonged in this one. Run this sweep now and fix what it finds; re-run it after each subsequent push (the checks are cheap when nothing drifted).

1. **PR record**: title and description describe what the diff does now, not what it did three pushes ago. Closing keywords (`Closes #N`) for the issues this resolves.
2. **Completeness**: compare the diff against the issue/plan it implements — everything promised is in the PR, or the gap is called out to Sami with reasoning. Silent scope shrink is how follow-up PRs get born.
3. **Outdated docs**: search the repo for references to what the diff changed — renamed commands, flags, paths, config keys, behavior — across READMEs, docs/, skills, AGENTS.md. Update them in this PR with the `updating-docs` skill; don't rely on remembering which docs exist.
4. **Self-review the diff** as a reviewer would (`jj diff --git` against the target branch): leftover debug output, TODOs you introduced, stray files, scope creep.
5. **Watch CI and review comments**: `gh pr checks --watch`, and read bot/reviewer comments (Sentry, CodeRabbit, humans) — they arrive after the push and count as CI. Fix failures by reproducing them locally; fix findings rather than "noting" them. If you disagree with one, defend it in the PR conversation from the plan record.
6. **Adjacent issues you spotted while working**: fix them in this PR, or name them to Sami with reasoning. A follow-up PR is a deferral.
7. **Re-verify if the diff moved**: if changes landed since your last end-to-end check, re-verify the affected surface (TUI → tmux, web → browser, API → curl).

Then report merge-ready with evidence: what you verified and how.
