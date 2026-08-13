---
name: verify
description: Run end-to-end verification now and provide evidence before any completion claim.
disable-model-invocation: true
---

# Verify

Run verification immediately for the user's original goal.

1. Restate the original goal and success condition.
1.5. Reject excuse patterns — pre-existing, known bug, not our fault — per AGENTS.md 'Excuses don't close goals'; remediate instead.
2. Run user-observable checks for that goal (through the real surface — the same list as Goal Integrity / opening-a-pr step 4).
3. Present concrete evidence from those checks.
4. Report one of: `verified`, `failed`, `blocked`.
5. If blocked, say exactly what remains unverified and why.

If the same check fails 3 attempts or ~20 minutes without progress: stop, checkpoint, change approach.
