# The contract change census

Read this from `opening-a-pr` when a change tightens a contract: it adds a refusal, requires,
removes or renames a field, or changes a process or package signature. The PR carries a
`## Contract change census` section with these three parts; a plan under `sdd` carries the same
section before implementation.

1. **Search commands and scope.** Trace the data, payload/file shape and endpoint across the
   repository and sibling producers/consumers, including every language, infrastructure handlers,
   documentation templates, skills and sealed fixtures; literal-name or importer searches alone
   miss callers that reconstruct the value.
2. **Every hit and disposition.** Record updated here / exercised by <test> / unaffected because
   <reason>; the reviewer reruns the search rather than trusting the plan's list. In the minutes
   before merging, rerun these commands against main's current tip and update new hits or hold
   the merge until coordinated with the PR that added them.
3. **One rollout line.** `Rollout: warn-first; refuses in <follow-up PR or issue>` ships an
   actionable warning, exits 0 and records would-be refusals; the named follow-up enforces only
   after real traffic shows no hits. Otherwise use `Rollout: immediate refusal; security: <what
   someone could do with the old input that this refusal stops>` for a vulnerability, not
   attribution, bookkeeping or data quality. A complete census, updated producers or an earlier
   immediate refusal cannot waive warnings for saved inputs outside the repository.
