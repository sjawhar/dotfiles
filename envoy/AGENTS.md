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

## How changes take effect

Changes deploy via `pulumi up --stack prod` from this directory (`npm install` first). The watchdog is opt-in via `ENVOY_DEPLOY_WATCHDOG=1`.
