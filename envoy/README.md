# Envoy Fleet Infrastructure (Pulumi)

Declarative deployment of the on-prem Envoy listener container on each fleet host,
through the `host:Listener` component of Legion's `envoy-infra` Pulumi plugin.

## Architecture

There is **one** NATS — the AWS Fargate `envoy-nats`, exposed on the tailnet by
a Tailscale sidecar managed in `~/agent-c/default/meta/infra` (see
`envoy_nats.py`). On-prem listeners connect to it as `envoy:natsUrl` names.

The project has two stacks, one per place it is applied from:

| Stack | Applied from | Machines | `envoy:natsUrl` |
|---|---|---|---|
| `prod` | the devbox | the devbox (local socket) | `nats://envoy-nats:4222`, over the host's Tailscale |
| `laptop` | the laptop | the laptop (local socket) | `nats://nats.internal.trajectorylabs.com:4222`, through the laptop's route into the VPC (`laptop/trajectory-route@.service`), so it works on either tailnet |

Each stack's machine with no `sshHost` uses the Docker socket of the machine
running Pulumi, so apply a stack only from its own machine.

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

A box that must stay off the tailnet is outside this stack: it runs Envoy on
its own, with legion's `packages/envoy/deploy/compose/single-node.compose.yml`
(a JetStream NATS and an `envoy-listener`, both on loopback; the Envoy page of
legion's docs site, "Running Envoy on one machine", has the steps), so its
sessions reach only each other and no GitHub/Slack events arrive. Its sessions
find that NATS through `"natsUrls": ["nats://127.0.0.1:4222"]` in the `envoy`
section of the machine's `envoy.json` (`AGENTS.md`, "Where the secrets are"),
where every other machine names the NATS URL its stack's `envoy:natsUrl` does. Such a machine needs
no `envoy/env.local`; delete one left from before.

## Prerequisites

- The Pulumi CLI, which `envoy/mise.toml` pins for this directory only: run
  `mise install` here
- Node.js (Pulumi uses ts-node)
- Docker on each target machine, with the SSH user in the `docker` group (the
  provider runs `docker` as that user, not via sudo)
- Tailscale on each target machine, joined to the same tailnet as `envoy-nats`
- SSH access from the deploy machine to remote hosts. Probe each `sshHost` with
  a plain `ssh <host> true` before `pulumi up`: when Tailscale SSH is due for its
  periodic check it answers with a `login.tailscale.com/a/…` URL instead of a
  shell, and the Docker provider only reports that as a 20-minute
  `context deadline exceeded` ping failure. The URL is bound to the pending
  connection: SSH's default keepalives drop it after ~90 s, and Tailscale itself
  abandons the check after ~30 min (`failed to fetch next SSH action`), either
  of which kills the URL. Hold the probe open with
  `ssh -o ServerAliveInterval=0 -o TCPKeepAlive=no <host> true`, visit the URL
  within that half hour (re-run the probe for a fresh one otherwise), then run
  the apply.

## The envoy-infra plugin

Each machine's listener is one `envoy-infra:host:Listener`, a component of
Legion's `envoy-infra` plugin (`packages/envoy/infra` in sjawhar/legion; its
README documents the inputs). The plugin creates the host's image pull, its
`envoy-listener` container and the opt-in watchdog as children of that
listener. `adopt` in `index.ts` names the root-level resources this program
created before it used the plugin (`listener-<name>`, `envoy-image-<name>`,
`watchdog-<name>`), so each listener takes over their state instead of
recreating them.

`Pulumi.yaml`'s `packages.envoy-infra` pins the plugin to one of Legion's
releases: its version, and the release URL Pulumi downloads it from.
`pulumi package add` generated the Node SDK the program imports,
`@sjawhar/envoy-infra`, under `sdks/sjawhar-envoy-infra/`. Both are committed,
since CI type-checks the program without a Pulumi CLI.

To move the pin to a release `<v>`, first check the release's archive in a
scratch directory, where `<commit>` is the tag's commit
(`gh api repos/sjawhar/legion/commits/envoy-infra-v<v> --jq .sha`):

```bash
gh release download envoy-infra-v<v> --repo sjawhar/legion \
  --pattern pulumi-resource-envoy-infra-v<v>-linux-amd64.tar.gz --pattern SHA256SUMS
grep ' pulumi-resource-envoy-infra-v<v>-linux-amd64.tar.gz$' SHA256SUMS | sha256sum --check
gh attestation verify pulumi-resource-envoy-infra-v<v>-linux-amd64.tar.gz --repo sjawhar/legion \
  --cert-identity https://github.com/sjawhar/legion/.github/workflows/release-envoy-infra.yaml@refs/heads/main \
  --source-ref refs/heads/main --deny-self-hosted-runners --source-digest <commit>
```

`--repo` alone accepts an attestation from any workflow run in Legion; the
other flags require Legion's release workflow, run from `main` on a
GitHub-hosted runner, at the tag's commit.

Then work in this directory, so `mise.toml`'s CLI runs. Delete the
`envoy-infra` entry under `packages:` in `Pulumi.yaml`: while it is there,
`package add` keeps that entry's version whatever version you name, and exits
0 having regenerated the old SDK. Install the checked archive before
`package add`, which otherwise downloads the version from the release URL with
no check. Then regenerate the SDK:

```bash
pulumi plugin install resource envoy-infra <v> --file <scratch>/pulumi-resource-envoy-infra-v<v>-linux-amd64.tar.gz
pulumi package add envoy-infra@<v> --server 'https://github.com/sjawhar/legion/releases/download/envoy-infra-v${VERSION}'
npm ci && npx tsc --noEmit
```

`package add` writes the new entry in `Pulumi.yaml`, regenerates `sdks/` and
updates `package-lock.json`; commit them together. Then preview: every step
under a `host:Listener` must be `same` before you apply.

## Quick Start

```bash
cd ~/.dotfiles/envoy
npm ci
secrets PULUMI_CONFIG_PASSPHRASE -- pulumi up --stack prod     # on the devbox
AWS_PROFILE=default secrets PULUMI_CONFIG_PASSPHRASE -- pulumi up --stack laptop   # on the laptop
```

The state is in S3 (`Pulumi.yaml`'s `backend`): the devbox reads it with its
instance role, and the laptop with Sami's AWS SSO login (`aws sso login --sso-session trajectorylabs`).

## Secrets

Pulumi encrypts secrets in `Pulumi.prod.yaml` using a passphrase it reads from
`PULUMI_CONFIG_PASSPHRASE`. The passphrase is an agent-tier key in secretsd on
the devbox and the laptop, so run every Pulumi command through
`secrets PULUMI_CONFIG_PASSPHRASE -- pulumi …`; it needs no YubiKey touch.

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
| `envoy:natsUrl` | The NATS URL each of the stack's listeners dials |
| `envoy:machines` | Array of machine definitions |

### Machine fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Logical name (used in Pulumi resource URNs) |
| `machineId` | yes | Passed as `ENVOY_MACHINE_ID` to the listener. Also names the listener's durable NATS consumer (`listener-<machineId>`) and gates delivery — the listener only serves sessions registered under its own machine ID. Changing it orphans the old consumer on the stream and forces live sessions to re-subscribe, so treat it as stable. |
| `sshHost` | no | SSH URI (e.g. `ssh://user@host`). Omit for the local machine — Docker uses the local socket. |

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
pulumi state delete --stack prod --force '<listener URN>'
pulumi state delete --stack prod --force '<docker provider URN>'
```

The container and the image are children of the machine's
`envoy-infra:host:Listener`, so they go before it. `pulumi stack --show-urns
--stack prod` lists them.

## Watchdog (opt-in)

With `ENVOY_DEPLOY_WATCHDOG=1`, `index.ts` gives each machine that has an
`sshHost` a `command-<name>` provider and the listener's `watchdog` input, which
installs a systemd timer on the host that re-creates the `envoy-listener`
container if it's removed. Container `restart: unless-stopped` already covers
process crashes; the watchdog only matters if something explicitly removes the
container.

The plugin installs it over SSH as root at the bare address of `sshHost` (its
user is dropped), with the keys of the SSH agent at `SSH_AUTH_SOCK` on the
deploy machine, which isn't always available in the deploy environment; the
plugin's README ("The watchdog") has the details. Turning it on creates a
`command` provider and a watchdog resource for each remote host:

```bash
ENVOY_DEPLOY_WATCHDOG=1 secrets PULUMI_CONFIG_PASSPHRASE -- pulumi up --stack prod
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
