# agent-secrets

The host side of agent secrets (AGENTC-393): the user unit and wrapper for `agent-secrets-helper`, the daemon that pins each host agent session by pidfd, keeps its broker key in memory, enrolls it as `kind: host`, and signs its proofs. Agent boxes do not use it: a box has its own key under its private runtime dir (`scripts/agentbox`, `agentbox/AGENTS.md`).

- `agent-secrets-helper.service` — `ExecStart` is the wrapper; `AGENT_SECRETS_URL` names the production broker, `~/.config/agent-secrets/env.local` (optional, uncommitted) overrides it.
- `agent-secrets-helper` — `mise exec agent-secrets -- agent-secrets-helper serve`; the binary comes from `mise.toml` `[tools.agent-secrets]` (sjawhar/legion `cmd/agent-secrets-helper`).
- `installers/agent-secrets.sh` links and enables the unit, and restarts it only when the pinned version or the unit changed — never while `agent-secrets-helper sessions` lists a session, unless `AGENT_SECRETS_HELPER_RESTART_WITH_SESSIONS=1`, because a restart re-pins live sessions with fresh keys and their grants are revoked.
- Once per host: `agent-secrets-login <github-login>` writes `~/.config/agent-secrets/launcher.token` and `operator` (0600; never mounted into a box) and starts the helper.
- A host session becomes a session root when its launcher runs `agent-secrets register --exec -- <agent argv>` (`shims/omp`, `scripts/oc`, `scripts/cld`); every process under it gets proofs from the socket `$XDG_RUNTIME_DIR/agent-secrets/helper.sock` through `AGENT_SECRETS_HELPER_SOCK`; when the root exits the helper revokes the enrollment.
