# AskGate frozen evaluation fixtures

## Calls that pass

- `xd://dispatch_ask`: "Should we use the rollout order documented in dispatch://LEGION-204/spec? Recommendation: use it because server-first and client-first are both safe." A pointer with a `dispatch://` link: passes.
- `xd://dispatch_doc_edit`, inserting "Sami ruled B — enable the advisor everywhere — on 2026-09-20." into the spec. A decision recorded as decided: passes.

## Calls that fail

- `xd://dispatch_ask`: "See my message above. Should we ship this?" → content 7. The pointer has no link or inline quote.
- `xd://dispatch_doc_edit`, inserting "2026-09-20 14:46Z — Status: server implementation complete; client work in progress." into the spec → content 8. This is a dated progress entry, not a decision or requirement.
