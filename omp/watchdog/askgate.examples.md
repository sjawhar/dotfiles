# AskGate frozen evaluation fixtures

## Calls that pass

- `xd://dispatch_ask`: "Should we use the rollout order documented in dispatch://LEGION-204/spec? Recommendation: use it because server-first and client-first are both safe." A pointer with a `dispatch://` link: passes.
- `xd://dispatch_doc_edit`, inserting "Sami ruled B — enable the advisor everywhere — on 2026-09-20." into the spec. A decision recorded as decided: passes.
- `xd://dispatch_ask`: "The build server's cache keys moved to S3 (seen in `infra/build-cache.tf`, link in the ask). Fine to roll out to staging first?" An infra claim with a cited source: passes.

## Calls that fail

- `xd://dispatch_ask`: "See my message above. Should we ship this?" → content 7. The pointer has no link or inline quote.
- `xd://dispatch_doc_edit`, inserting "2026-09-20 14:46Z — Status: server implementation complete; client work in progress." into the spec → content 8. This is a dated progress entry, not a decision or requirement.
- `xd://dispatch_ask`: "Only you can set this repo variable; my App gets 403. Set `FOO=bar`?" with no `agent-access` output and no peer named → access 7, `blocker` (kind `action`). The claim that nothing here can do it is unverified.
- `xd://dispatch_ask`: "Should the registrar stop at tasks-out or keep both?" with no gloss anywhere for "tasks-out," and the two options are "stop at tasks-out" / "keep both" → phone-readability 3. The decision hinges on a term the ask never defines.
- `xd://dispatch_ask`: "We're on CodeCommit for this repo, so the import step needs an IAM role, not a GitHub token. Approve the role?" with no file, command, or link anywhere naming CodeCommit as the actual host → content 9. An unsourced claim about what we run today, driving the whole ask.
