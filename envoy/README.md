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
- Docker on each target machine, with the SSH user in the `docker` group (the
  provider runs `docker` as that user, not via sudo)
- Tailscale on each target machine, joined to the same tailnet as `envoy-nats`
- SSH access from the deploy machine to remote hosts

## Quick Start

```bash
cd ~/.dotfiles/envoy
npm install
secrets PULUMI_CONFIG_PASSPHRASE -- pulumi up --stack prod
```

## Secrets

Pulumi encrypts secrets in `Pulumi.prod.yaml` using a passphrase it reads from
`PULUMI_CONFIG_PASSPHRASE`. The passphrase is a human-tier key in secretsd, so
run every Pulumi command through `secrets PULUMI_CONFIG_PASSPHRASE -- pulumi …`
— the first request in a session needs a YubiKey touch, the rest are free.

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
| `machineId` | yes | Passed as `ENVOY_MACHINE_ID` to the listener. Also names the listener's durable NATS consumer (`listener-<machineId>`) and gates delivery — the listener only serves sessions registered under its own machine ID. Changing it orphans the old consumer on the stream and forces live sessions to re-subscribe, so treat it as stable. |
| `sshHost` | no | SSH URI (e.g. `ssh://user@host`). Omit for the local machine — Docker uses the local socket. |
| `listener.webhooks.{github,slack,ghostwispr}` | no | Enable on-prem webhook ingress for that source. **Only set when the webhook source POSTs locally to this host** (e.g. a Ghost Wispr host whose app POSTs to its own listener). GitHub/Slack webhooks ingress through Fargate, not on-prem. |

### Adding a host

1. Confirm the host is on the tailnet and can reach the bus:
   `bash -c 'echo > /dev/tcp/envoy-nats/4222'`
2. Confirm Docker works as the SSH user (`docker info`). A fresh devbox often has
   Docker installed but the login user outside the `docker` group —
   `sudo usermod -aG docker <user>` fixes it.
3. Add a `machines:` entry in `Pulumi.prod.yaml`. For `sshHost`, use the host's
   MagicDNS FQDN (`ssh://user@host.tailb86685.ts.net`) when a `~/.ssh/config`
   entry of the same bare name carries a `ProxyCommand` — otherwise the deploy
   inherits that proxy's credential requirements (e.g. AWS SSO for SSM).
4. Run `pulumi up --stack prod`

### Removing a host that is already gone

Deleting a `machines:` entry makes Pulumi delete the container, which needs the
host to answer. For a host that no longer exists, drop the resources from state
instead so the apply never dials it:

```bash
pulumi state delete --stack prod --force '<container URN>'
pulumi state delete --stack prod --force '<remote image URN>'
pulumi state delete --stack prod --force '<docker provider URN>'
```

`pulumi stack --show-urns --stack prod` lists them.

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
