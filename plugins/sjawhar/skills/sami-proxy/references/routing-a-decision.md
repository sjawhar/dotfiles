# Routing a decision or a block

Read when the decision goes to someone other than Sami, or your work holds on another agent or a check.

- When your work waits on a person, file the ask: a wait with no ask reaches nobody.
- When asked what you wait on, list every item your work waits on (a person, another agent, a check, a merge), not
  only your open asks.
- When you hold a pull request on an ask, link the ask in a pull request comment: no merge gate sees an ask.
- Resolve who holds an issue's route or a role before you route a decision there: a filing is not an owner, a role
  that exists only in prose reaches no human, and you never claim a vacant role yourself. When you audit owners,
  list all open work, not only the priority items.
- Before a send to a session id, check that `envoy_sessions` still lists the session. A publish to an unclaimed role
  fails with HTTP 404, but a dead session takes a send silently.
