# Envoy

Pulumi (TypeScript) infrastructure that deploys the on-prem `envoy-listener` container to fleet hosts. See `README.md` for the full architecture, secrets, and command reference; this file only summarizes conventions.

**Webhook delivery:** Envoy receives external GitHub and Slack webhooks (ingressed through Fargate). Any change must preserve webhook delivery.

## Key files

- **`index.ts`** — entry point; reads `envoy:` stack config, loops over `machines`, creates a listener (and opt-in watchdog) per host.
- **`machines.ts` / `services.ts` / `images.ts` / `watchdog.ts`** — provider setup, listener container, image pulls, systemd watchdog.
- **`Pulumi.yaml` / `Pulumi.prod.yaml`** — project and encrypted prod stack config.

## Conventions

- Stack config lives under the `envoy:` namespace; secrets are encrypted in `Pulumi.prod.yaml` and require `PULUMI_CONFIG_PASSPHRASE`.
- `tsconfig.json` targets `module: commonjs` for ts-node (Pulumi), not Bun. Leave the `ignoreDeprecations` setting in place.
- Do not commit decrypted secrets or `node_modules`.

## Where the secrets are

- **The Dispatch bearer token is not here.** The `envoy_*`/`dispatch_*` tools read it from `~/.config/opencode/envoy.json`, key `dispatch.token` (beside `dispatch.enabled`, `dispatch.serverUrl` and top-level `natsUrls`). The reader is the pi-envoy extension and the Dispatch server's shared config loader in the legion repo (`packages/envoy/internal/dispatch/config/config.go`), which shallow-merges that user file with `<cwd>/.opencode/envoy.json`, repo overriding. The token is a secret: never commit it, never copy it into this directory. On the devbox its value is the shared agent token (Secrets Manager `production/dispatch/agent-token`), deployed for agent sessions to use with their own `actor` identity - it is not Sami's personal credential, and reading it from that file is the sanctioned path.
- **The sessions' NATS credential is not here either.** Sami's own omp and Claude Code sessions authenticate to production NATS as the `omp-session` nkey user, whose seed lives in Secrets Manager (`production/envoy/nats-nkey-seed-omp-session`, created by agent-c's infrastructure, LEGION-279 / AGENTC-759). `scripts/envoy-nats-seed` caches it once per boot in a 0600 file under `$XDG_RUNTIME_DIR/envoy/` and the session launchers point `NATS_NKEY_SEED_FILE` at it (pi-envoy and the claude-envoy bridge read it). With no secret or no AWS the launchers export nothing and sessions connect without a credential. The listener this directory deploys is configured separately.
- **This directory holds the deployment's own secrets only**, encrypted in `Pulumi.prod.yaml`: `envoy:githubWebhookSecret`, `envoy:slackSigningSecret` and the `envoy:ghcrToken` image-pull token.

## How changes take effect

Changes deploy via `pulumi up --stack prod` from this directory (`npm install` first). The watchdog is opt-in via `ENVOY_DEPLOY_WATCHDOG=1`.
