# Envoy Fleet Infrastructure (Pulumi)

Declarative deployment of the on-prem Envoy listener container on each fleet host.

## Architecture

There is **one** NATS — the AWS Fargate `envoy-nats`, exposed on the tailnet by
a Tailscale sidecar managed in `~/agent-c/default/meta/infra` (see
`envoy_nats.py`). All on-prem listeners connect to it as
`nats://envoy-nats:4222` over the host's existing Tailscale.

```
GitHub/Slack webhook
        │
        ▼
   ALB ─► Fargate envoy-listener ─► Fargate envoy-nats ◄─── tailnet ───┐
                                                                       │
                                                  (every host)         │
                                                  envoy-listener ──────┘
                                                  ▼
                                           local OpenCode sessions
                                           on 127.0.0.1:9020
```

Agent-to-agent messages, GitHub events, and Slack events all flow through the
single Fargate NATS. On-prem hosts do **not** run their own NATS and listeners
are **not** their own Tailscale devices — they ride the host's Tailscale.

This makes nodes effectively transient: a fresh host that joins the tailnet
just needs `envoy-listener` running. No NATS cluster to join, no auth key to
provision, no per-listener identity.

## Prerequisites

- [Pulumi CLI](https://www.pulumi.com/docs/install/)
- Node.js (Pulumi uses ts-node)
- Docker on each target machine
- Tailscale on each target machine, joined to the same tailnet as `envoy-nats`
- SSH access from the deploy machine to remote hosts

## Quick Start

```bash
cd ~/.dotfiles/envoy
npm install
export PULUMI_CONFIG_PASSPHRASE="<your-passphrase>"
pulumi up --stack prod
```

## Secrets

Pulumi encrypts secrets in `Pulumi.prod.yaml` using a passphrase that must be
exported as `PULUMI_CONFIG_PASSPHRASE` for any Pulumi command. Persist it via
SOPS-encrypted dotfiles (`~/.dotfiles/secrets.env`).

| Config key | Purpose |
|---|---|
| `envoy:githubWebhookSecret` | Validates GitHub webhook payloads (used by Fargate listener; carried here for completeness) |
| `envoy:slackSigningSecret` | Validates Slack event payloads (same) |
| `envoy:ghcrToken` | Pulls container images from GHCR |

To rotate:
```bash
pulumi config set --secret envoy:githubWebhookSecret "<value>" --stack prod
```

## Stack Configuration

| Key | Description |
|---|---|
| `envoy:registry` | Container image registry (e.g. `ghcr.io/sjawhar/legion`) |
| `envoy:imageTag` | Envoy image tag to deploy |
| `envoy:machines` | Array of machine definitions |

### Machine fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Logical name (used in Pulumi resource URNs) |
| `machineId` | yes | Passed as `ENVOY_MACHINE_ID` to the listener |
| `sshHost` | no | SSH URI (e.g. `ssh://user@host`). Omit for the local machine — Docker uses the local socket. |
| `listener.webhooks.{github,slack,ghostwispr}` | no | Enable on-prem webhook ingress for that source. **Only set when the webhook source POSTs locally to this host** (e.g. ghost-wispr's app POSTs to its own listener). GitHub/Slack webhooks ingress through Fargate, not on-prem. |

### Adding a transient host

1. Make sure the host is on the tailnet and Docker is installed
2. Add SSH access from the deploy machine
3. Add a `machines:` entry in `Pulumi.prod.yaml`
4. Run `pulumi up --stack prod`

## Watchdog (opt-in)

`deployWatchdog` in `watchdog.ts` installs a systemd timer on the host that
re-creates the listener container if it's removed. Container `restart:
unless-stopped` already covers process crashes; the watchdog only matters if
something explicitly removes the container.

It's gated behind `ENVOY_DEPLOY_WATCHDOG=1` because deploying it requires SSH
agent forwarding with the right keys, which isn't always available in the
deploy environment:

```bash
ENVOY_DEPLOY_WATCHDOG=1 pulumi up --stack prod
```

## Commands

```bash
pulumi preview --stack prod        # Dry-run
pulumi up --stack prod             # Deploy
pulumi refresh --stack prod        # Reconcile state with reality
pulumi destroy --stack prod        # Tear down listeners (does not touch Fargate)
```

## TypeScript notes

`tsconfig.json` uses `module: commonjs` and `moduleResolution: node` because
Pulumi uses ts-node, not Bun. `ignoreDeprecations: "6.0"` suppresses a
TypeScript 5.x deprecation warning that's required for ts-node compatibility.
