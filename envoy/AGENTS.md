# Envoy

The Pulumi (TypeScript) program that deploys the `envoy-listener` container to each fleet host, through Legion's `envoy-infra` plugin. `README.md` has the architecture, the two stacks, the secrets and every command. Envoy receives GitHub and Slack webhooks: a change must keep their delivery working.

## Commands, from this directory

- Setup: `mise install` (the Pulumi CLI `mise.toml` pins here), then `npm ci`.
- Type check: `npx tsc --noEmit`. CI runs it (`.github/workflows/envoy-typecheck.yml`).
- Preview, then deploy: `secrets PULUMI_CONFIG_PASSPHRASE -- pulumi preview --stack prod`, then `pulumi up` the same way. Read `README.md`, "Architecture", first: it says which machine applies which stack.

## Conventions

- Before you move the plugin pin, or touch `sdks/` (generated) or the `adopt` names in `index.ts`, read `README.md`, "The envoy-infra plugin".
- Never commit decrypted secrets or `node_modules`. The deployment's own secrets are encrypted in `Pulumi.prod.yaml`.

## Where the secrets are

- A session's Dispatch and Envoy credentials are not here. Every Dispatch and Envoy client in a session (the pi-envoy tools, the claude-envoy bridge, the `dispatch` command, `scripts/envoy`) reads one `envoy.json`: a `dispatch` section (`enabled`, `serverUrl`, `token`) and an `envoy` section (`enabled`, `url`, `natsUrls`, `token`, `natsSeed`). A `token` or `natsSeed` that starts with `!` is a command whose output is the value.
- Each named omp profile, Claude Code `CLAUDE_CONFIG_DIR` and OpenCode `OPENCODE_CONFIG_DIR` reads its own `envoy.json`, and has no Dispatch or Envoy without one; a link to the neutral file, `${XDG_CONFIG_HOME:-~/.config}/legion/envoy.json`, serves. `scripts/envoy`'s header has the full lookup order.
- `DISPATCH_URL`, `DISPATCH_TOKEN(_FILE)`, `ENVOY_URL`, `ENVOY_NATS_URL`, `ENVOY_TOKEN(_FILE)` and `NATS_NKEY_SEED(_FILE)` each override one field. No launcher here (`shims/omp`, `scripts/cld`, `scripts/agentbox`) sets them: one would override every profile's file and turn Envoy on for a profile that has none. A box reads the host's files (`agentbox/AGENTS.md`).
- On the devbox the Dispatch token is the shared token for agent sessions, not Sami's personal credential; reading it from `envoy.json` is the intended path. Never commit it or copy it here.
- The sessions' NATS seed comes from `"natsSeed": "!$HOME/.dotfiles/scripts/envoy-nats-seed --value"` in the `envoy` section; the script's header says how it caches and refreshes the seed.
