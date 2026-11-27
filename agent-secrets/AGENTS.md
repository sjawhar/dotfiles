# agent-secrets

- Never start a second helper: `agent-secrets/agent-secrets-helper` with no arguments runs `serve`, and a second `serve` takes the helper's socket and pins every live session again. `agent-secrets-helper sessions` lists the sessions.
- Never copy the machine login, `~/.config/agent-secrets/machine.json`, to another machine or into a box: a second helper that spends its refresh token ends the login, and only the operator can approve a new one.

Where this file and the code disagree, the code is right: follow the code and correct this file.
