# Routing a decision or a block

Read this when the decision goes to someone other than Sami, or when your work holds on another agent or a check.

- When your work waits on a human, file the ask, because a wait with no ask reaches nobody. When asked what you wait on, audit the holds in your ledger, not only your asks.
- When you hold a pull request on an ask, tell the gates and checks that read it (a PR comment that links the ask), because no merge process sees an ask against a pull request.
- Resolve who holds an issue's route or a role before you route a decision there: a filing is not an owner, a role that exists only in prose reaches no human, and you never claim a vacant role yourself. When you audit owners, list all open work, not only the priority items, so unowned work shows up.
- Before a send to a session id, above all a fire-and-forget one, check that the session is still listed. A publish to an unclaimed role fails with HTTP 404, but a dead session takes a send silently.
- Take decisions one at a time in a live exchange; for unattended work use Dispatch, where several independent threads can stay open. Do not duplicate another owner's asks, treat silence as consent, or turn status updates into an approval queue.
