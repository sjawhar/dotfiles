# The contract change census

A change tightens a contract when it adds a refusal, requires, removes or renames a field, or changes a process or package signature. The PR, and a plan under `sdd` before implementation, carries `## Contract change census` with three parts:

1. **Search commands and scope.** Trace the data, payload or file shape, and endpoint across the repository and sibling producers and consumers. Cover every language, infrastructure handlers, doc templates, skills and sealed fixtures. Name-only or importer searches miss callers that reconstruct the value.
2. **Every hit and its disposition:** updated here / exercised by `<test>` / unaffected because `<reason>`. The reviewer runs the search again. Minutes before merge, run it against main's tip. Update new hits, or hold the merge until you coordinate with the PR that added them.
3. **One rollout line**, one of:
   - `Rollout: warn-first; refuses in <follow-up PR or issue>`: an actionable warning, exit 0, a record of what it would refuse. The follow-up enforces only after those records, from real traffic, show zero would-refuse entries; cite the count in the follow-up.
   - `Rollout: immediate refusal; security: <what the old input let someone do>`: only for a vulnerability, not attribution, bookkeeping or data quality.
   - Inputs saved outside this repository always get the warn-first rollout: a complete census, updated producers or an earlier immediate refusal is not a reason to refuse them at once.
