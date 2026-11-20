# agent-secrets

The host side of the secrets broker: the systemd user unit and the wrapper for `agent-secrets-helper`, whose binary `mise.toml` pins (`[tools.agent-secrets]`). The helper holds the machine's broker login, registers each host agent session and each agent box, and signs their proofs. Sami's own shells do not use it: they read secretsd (`scripts/secret-run`).

- `broker.env` holds the production broker's URL. Every consumer loads it through `broker-url.sh`, whose header has the rule, the machine-local override included.
- `installers/agent-secrets.sh` installs the helper and restarts it after a change. `devbox/install.sh` runs it; the root `install.sh` runs it, through `installers/agent-secrets-local.sh`, only where `env.local` sets `AGENT_SECRETS_URL`. It does not restart a helper with registered sessions unless `AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS=1`; its header lists what a restart costs each live session.
- Through the wrapper, `agent-secrets-helper` with no arguments runs `serve`. Never start a second `serve`: it takes the socket and re-pins every live session. `agent-secrets-helper sessions` lists them.
- A `pkill -f "agent-secrets-helper serve"` aimed at a test rig's helper stops this one too; systemd restarts it (`Restart=always`) and it resumes its login.

## Machine login

- Once per machine, and again only after the login lapses or is revoked: `agent-secrets-login <email>`, with the email the operator signs in to Dispatch with. The operator approves the printed code on Dispatch's `/credentials/machine` page; an agent cannot.
- The login is kept in `~/.config/agent-secrets/machine.json` (0600). Never copy it to another machine or into a box: a second helper that spends its refresh token ends the login.
- `agent-secrets machine login-status` is the probe with no side effects: it prints the state and exits 0 only on `issued`.

## Sessions and boxes

- `shims/omp` and `scripts/cld` exec the agent through `scripts/agent-session`, then `scripts/agent-secrets-session`, which registers it with the helper; its header says why registering before the session has a credential is safe. A helper that does not answer means no registration, not a failed launch.
- A session names itself to the helper once it has an id (`omp/extensions/session-env.ts`; for Claude Code the SessionStart hook `scripts/agent-secrets-name-session`), so a helper restart keeps its enrollment.
- `scripts/agentbox new` enrolls a box only while the helper holds a live credential; otherwise the box's agents use secretsd. `agentbox/AGENTS.md` has the box side.
- A machine without the helper has no broker identity, so its sessions get GitHub App tokens from secretsd: `shims/gh-app-token`'s header has the setup.
