---
description: Opening a Dispatch ask, which reaches the issue's assignee and nobody else
condition:
  - '\bdispatch\s+ask\b'
scope: "tool:bash, tool:eval"
interruptMode: never
---
# A Dispatch ask reaches the issue's assignee, whoever its text names

An ask is delivered to the assignee of the issue it sits on. Its text is not a routing instruction: an ask that opens "Aengus: ..." on an issue assigned to Sami reaches **Sami**, and an ask meant for another agent reaches a person as a decision for them. Agents file issues assigned to `sami@theorem.dev` by default, so most issues are his.

Before an ask, read who it will actually reach:

```sh
dispatch read --issue <KEY> | grep -E '^(Assignee|Claimed)'
```

- **Meant for an agent?** Do not use an ask. Message the session over Envoy, or comment on the issue and let its owner read it. Asks are for things only a person can give: a decision, an approval, a credential.
- **Meant for a person who is not the assignee?** The ask will not reach them. Say the name in a comment on the issue instead, or open the ask on an issue that person is assigned.
- **Meant for the assignee?** Go ahead.

The defect is INFRA-1576, P1, where an ask will name its own recipient. Until that lands, this check is the only thing between an agent's ask and a person's inbox.
