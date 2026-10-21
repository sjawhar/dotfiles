---
name: verify
description: Run end-to-end verification of the user's original goal now and report evidence before any completion claim.
disable-model-invocation: true
---

# Verify

Run verification now, against the user's original goal:

1. Restate the goal and its success condition.
2. Verify what the user observes, through the real surface. TUI: tmux. Web: a real browser. API: curl. Library: a driver script. See also `opening-a-pr` step 4. Passing tests and green CI are groundwork, not evidence. Verify on this machine.
3. Show the concrete evidence from that run.
4. Report `verified`, `failed`, or `blocked`. If blocked, say what stays unverified and why. Blocked means not done: raise it as a pre-merge decision, never as remaining work on a merge-ready report.

- Re-run after every diff change.
- "Pre-existing", "known bug" or "not our fault" does not close the goal: fix it.
- The same verification fails 3 times, or about 20 minutes pass with no progress: stop, write a checkpoint, change approach.
