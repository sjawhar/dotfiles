# The plan, spec and PR also carry

- **A contract change census**, when the change tightens a contract: it adds a refusal, requires, removes or renames a field, or changes a process or package signature. The PR, and a plan under `sdd` before implementation, carries `## Contract change census` with three parts:
  1. The search commands and scope.
     - Trace the data, payload or file shape and the endpoint across this repository and every other repository that produces or consumes it: search its owner with `gh search code '<field>' --owner <owner>`.
     - Add each consumer you know under another owner.
     - Cover every language, infrastructure handlers, doc templates, skills and recorded test fixtures (saved payloads and golden files).
     - Name-only or importer searches miss callers that rebuild the value.
  2. Every hit and its disposition: updated here / exercised by `<test>` / unaffected because `<reason>`.
     - The reviewer reruns the search.
     - Minutes before merge, rerun it against main's tip. Update new hits, or hold the merge until you coordinate with the PR that added them.
  3. One rollout line:
     - `Rollout: warn-first; refuses in <follow-up PR or issue>`: an actionable warning, exit 0, and a record of what it would refuse. The follow-up enforces only after those records, from real traffic, show zero would-refuse entries, and cites the count.
     - `Rollout: immediate refusal; security: <what the old input let someone do>`: only for a vulnerability, not attribution, bookkeeping or data quality.
     - Inputs saved outside this repository always get warn-first, whatever the census found.
- **The downstream delta** of a change to a producer's schema, publish path or nullability. This needs no approval from Sami.
  - Name each downstream surface and what it will show differently.
  - Have the consumer's owner review that delta.
  - With no consumer found, paste the search you ran and its empty output into the census.
- **The audience of a requested render**: when a named person asked for a render, record that person and the channel where they will see it live. You need no reply.
