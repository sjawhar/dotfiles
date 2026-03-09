---
name: verify
description: Run end-to-end verification now and provide evidence before any completion claim.
disable-model-invocation: true
---

# Verify

Run verification immediately for the user's original goal.

1. Restate the original goal and success condition.
2. Run user-observable checks for that goal (not only unit tests/mocks).
3. Present concrete evidence from those checks.
4. Report one of: `verified`, `failed`, `blocked`.
5. If blocked, say exactly what remains unverified and why.
