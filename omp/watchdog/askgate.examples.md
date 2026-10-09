# AskGate frozen evaluation fixtures

## Calls that pass

- `dispatch ask --issue X-1 --question 'Should we use the rollout order documented in dispatch://LEGION-204/spec? Recommendation: use it because server-first and client-first are both safe.'` A pointer with a `dispatch://` link: passes.
- `dispatch doc-edit --issue X-1 --artifact spec --ops-json '[{"op":"insert","markdown":"Sami ruled B — enable the advisor everywhere — on 2026-09-20."}]'` A decision recorded as decided: passes.
- `dispatch ask --issue X-1 --question 'The build server cache keys moved to S3 (seen in infra/build-cache.tf, link in the ask). Fine to roll out to staging first?'` An infra claim with a cited source: passes.

## Calls that fail

- `dispatch ask --issue X-1 --question 'See my message above. Should we ship this?'` → content 7. The pointer has no link or inline quote.
- `dispatch doc-edit --issue X-1 --artifact spec --ops-json '[{"op":"insert","markdown":"2026-09-20 14:46Z — Status: server implementation complete; client work in progress."}]'` → content 8. This is a dated progress entry, not a decision or requirement.
- `dispatch ask --issue X-1 --question 'Only you can set this repo variable; my App gets 403. Set FOO=bar?' --option Done --option "Can't"` with no `agent-access` output and no peer named → access 7, `blocker` (an action ask). The claim that nothing here can do it is unverified.
- `dispatch ask --issue X-1 --question 'Should the registrar stop at tasks-out or keep both?' --option 'Stop at tasks-out' --option 'Keep both'` with no gloss anywhere for "tasks-out" → phone-readability 3. The decision hinges on a term the ask never defines.
- `dispatch ask --issue X-1 --question 'We are on CodeCommit for this repo, so the import step needs an IAM role, not a GitHub token. Approve the role?'` with no file, command, or link anywhere naming CodeCommit as the actual host → content 9. An unsourced claim about what we run today, driving the whole ask.
