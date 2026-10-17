# Envoy listeners from Legion's `envoy-infra` plugin: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** the dotfiles Envoy stack deploys every listener through Legion's
`envoy-infra:host:Listener` component, with no diff to the running listeners. This laptop's
Theorem-profile sessions use Theorem's hub directly, so the stack runs no Theorem listener.

**Architecture:** one Pulumi project and stack, `envoy/` `prod`. It keeps its machines, and it
replaces `services.ts`, `images.ts` and `watchdog.ts` with one `host.Listener` per machine. Each
listener adopts its machine's existing container, image and watchdog by name, so nothing is
recreated.

**Tech stack:** TypeScript on ts-node (`envoy/tsconfig.json`), `@pulumi/pulumi`, `@pulumi/docker`
4.11.0, `@pulumi/command` (the version `envoy/package.json` pins), the `envoy-infra` plugin's
generated Node SDK, Pulumi CLI 3.268.0 (pinned in `mise.toml`), bash, Python `unittest`.

**Specs:** Legion's `docs/plans/2026-10-14-envoy-infra-design.md` and its plan
`docs/plans/2026-10-14-envoy-infra-plan.md` (sjawhar/legion#1946), which define the component's
inputs. Theorem's `theorem-labs/agent-infra` `docs/specs/envoy.md` and its plan
`docs/plans/2026-10-14-envoy-plan.md`, which run the hub that Theorem-profile sessions use.

## Global constraints

- The `prod` stack moves onto the component only when its preview creates, updates, replaces or
  deletes no cloud resource and leaves outputs unchanged (Sami, 2026-10-13). The only creates the
  move allows are the `envoy-infra:host:Listener` components and any provider that exists only in
  Pulumi state (Sami, 2026-10-14). This stack needs no provider create (its docker providers exist,
  and D6 keeps the command provider out). Sami (2026-10-14): the existing deployments move first, or in parallel with
  the GCP work.
- Every `envoy/` edit, preview and apply of slice 2 happens in one devbox clone at
  `~/.worktrees/dotfiles/envoy-infra`, cut from `origin/main`.
- One Envoy stack in the dotfiles (Sami, 2026-10-14).
- This repository is public: Theorem's tailnet name and its hub's addresses never appear in it.
- Per-machine files (`~/.omp/profiles/theorem/agent/envoy.json`,
  `~/.dotfiles/.claude-profiles/theorem/envoy.json`) are written on the machine and never
  committed.
- No credential goes in an argv or a persistent environment. Secret values travel by stdin or by
  file.
- Use plain git in the clone `~/.worktrees/dotfiles/envoy-infra`: one or two described commits per
  topic, then `git pull --rebase` and a fast-forward push to `main`.

## Rulings made while planning

- **D1. The stack's state stays where the stack keeps it today: the S3 backend `envoy/Pulumi.yaml`
  names.** That follows from one stack, as Sami decided.
- **D2. Theorem-profile sessions on this laptop talk to Theorem's hub directly** (decided
  2026-10-17, once Sami had put the laptop on Theorem's tailnet). The hub runs Envoy's listener and
  admits sessions that hold no credential (sjawhar/legion#1974), so this stack runs no Theorem
  listener and `envoy-nats-seed` keeps one source.
- **D3. The per-machine `envoy.json` files are written by one documented command**, the way
  `~/.config/legion/envoy.json` was written, not by an installer.
- **D4. The `prod` program drops webhook support.** No machine in `Pulumi.prod.yaml` sets webhooks
  (`:14-26`), webhook ingress runs elsewhere (the stack's own comment), and Legion's component
  offers none in this version.
- **D5. The Pulumi CLI is pinned at 3.268.0 in `mise.toml`**, the version Legion's CI uses, with
  natscli beside it. Both are pushed on their own (Task 2.0), because agent-infra needs them before
  slice 2 is done.
- **D6. The watchdog branch is not exercised by this migration.** pulumi-command dials
  `<host>:22` as `root` (v1.2.1 `remote/connection.go:57,196`), so a watchdog for `sami@sami` or
  `agent@oryx…` has probably never been created. Task 2.1's export confirmed none is in state on
  2026-10-14, so previews and applies run with `ENVOY_DEPLOY_WATCHDOG` unset.

## Order and the human steps

Slice 2 runs from the devbox, which reaches the laptop's Docker over Trajectory's tailnet.

| Step | Who | When |
| --- | --- | --- |
| Opening a Tailscale SSH check URL if one is due (`envoy/README.md`, "Prerequisites"; it shows only as a Docker provider ping timeout) | Sami | before any task that dials `sami` or `oryx` |

## Slices

| Slice | Outcome someone can try | Blocked on |
| --- | --- | --- |
| 2 | `pulumi preview --stack prod` shows only the three listener components as creates; after `pulumi up`, all three listeners are unchanged and delivering | Legion slice 1 built (Task 2.2) and released (Task 2.3) |
| 4b | Theorem-profile omp and `cld` sessions on this laptop message through Theorem's hub, with nothing deployed for them here | done 2026-10-17 |

---

## Slice 2: the `prod` stack on `host:Listener`

### Task 2.0: pin the Pulumi CLI and natscli, and push

- [ ] Add `pulumi = "3.268.0"` to a new `envoy/mise.toml`'s `[tools]`, so the pin applies only
  where the program runs: a global pin would put mise's Pulumi ahead of whatever the devbox's
  other deployments run today. Add `"github:nats-io/natscli" = "<x.y.z>"` (its latest release, as
  an exact version) to the root `mise.toml`'s `[tools]`; no `nats` binary exists on the laptop.
  Check that mise trusts `envoy/mise.toml` both in `~/.dotfiles` and in a clone under
  `~/.worktrees/dotfiles/`, the way it trusts the root file.
- [ ] Add `"sdks"` to `envoy/tsconfig.json`'s `exclude`.
- [ ] Commit and push to `main` on its own.
- [ ] Run `scripts/dotfiles-sync` on the devbox. With no host argument it syncs the devbox, the
  laptop and oryx (`dotfiles-sync:14`).
- [ ] Run `mise install` on the laptop and on the devbox. On both, `pulumi version` run in
  `envoy/` prints `v3.268.0`, `pulumi` outside `envoy/` resolves as it did before, and
  `nats --version` answers.

### Task 2.1: read the stack's inventory

- [ ] Work in the devbox clone (Global constraints). The `devbox-sami` machine has no `sshHost`
  (`envoy/Pulumi.prod.yaml:14-16`), so the program runs where its Docker socket is. Export the
  state:
  `cd envoy && npm ci && secrets PULUMI_CONFIG_PASSPHRASE -- pulumi stack export --stack prod --file <tmpdir>/prod-export.json`.
- [ ] Record every resource's `type`, URN name, `parent` and `provider`, and whether any
  `command:remote:Command watchdog-*` is in state.
- [ ] Before any apply, record
  `docker inspect envoy-listener --format '{{.Created}} {{.State.Health.Status}}'` on `sami-agents`,
  `sami` and `oryx`.
- [ ] Delete the export by its literal path.

### Task 2.2: the program on `host:Listener`, against the branch's plugin

**Files:**
- Modify: `envoy/index.ts`, `envoy/package.json`, `envoy/package-lock.json`, `envoy/Pulumi.yaml`,
  `envoy/machines.ts` (drop the webhook fields), `envoy/AGENTS.md`,
  `envoy/README.md`
- Create: `envoy/sdks/envoy-infra/` (generated, and committed: CI has no Pulumi CLI)
- Delete: `envoy/services.ts`, `envoy/images.ts`, `envoy/watchdog.ts`, and
  `envoy/__tests__/services.test.ts` (it imports `services.ts`, and `index.ts` cannot be imported
  without running the program)

- [ ] Build the plugin from Legion's `envoy-infra` branch:
  `cd <legion clone>/packages/envoy/infra && go build -o <dir>/pulumi-resource-envoy-infra ./cmd/pulumi-resource-envoy-infra`.
- [ ] Add `plugins: {providers: [{name: envoy-infra, path: <dir>}]}` to `envoy/Pulumi.yaml`. This
  entry stays local and is removed in Task 2.3.
- [ ] Run `cd envoy && pulumi package add <dir>/pulumi-resource-envoy-infra`.
- [ ] Rewrite `index.ts`. For each machine:
  - keep `createProvider` (`docker-<name>`) unchanged;
  - when the machine has an `sshHost` and `ENVOY_DEPLOY_WATCHDOG=1`, add
    `command.Provider("command-<name>", {})` (D6);
  - create the listener:

    ```ts
    new envoyInfra.host.Listener(machine.name, {
      machineId,
      natsUrls: [ENVOY_NATS_URL],
      image: `${registry}/envoy:${imageTag}`,
      hostBridge: "127.0.0.1",
      watchdog: <{host: sshHost.replace(/^ssh:\/\/([^@]*@)?/, "")} or undefined>,
      adopt: {
        container: `listener-${machine.name}`,
        image: `envoy-image-${machine.name}`,
        watchdog: `watchdog-${machine.name}`,
      },
    }, {providers: [docker, <command provider when watchdog>]})
    ```

    The watchdog `host` is the address only: Legion's component refuses `@` and `://`, and
    pulumi-command logs in as root.
  - `ENVOY_NATS_URL` (`nats://envoy-nats:4222`) moves from `services.ts` into `index.ts`.
- [ ] Run `npx tsc --noEmit` in `envoy/`.
- [ ] Preview from the devbox, with `ENVOY_DEPLOY_WATCHDOG` unset (D6):
  `secrets PULUMI_CONFIG_PASSPHRASE -- pulumi preview --stack prod --json --show-sames > <tmp>/preview.json`.
  A short script checks the pass condition, and its output goes in the report:
  - every step whose URN is under an `envoy-infra:host:Listener` is `same`;
  - the only other non-`same` steps are the three component `create`s;
  - no `docker:` or `command:` resource has a `delete`, `replace`, `update` or `create`.
- [ ] If anything else shows, stop before any `pulumi up` and report it to the coordinator with the
  step. This is Legion's scenario E4. sjawhar/legion#1946 gets only the operation counts by
  resource type, with no URNs, because the URNs carry machine names.

### Task 2.3: pin the release and apply

- [x] Once Legion's plugin is released, remove the `plugins.providers` entry.
- [x] Download `pulumi-resource-envoy-infra-v<v>-linux-amd64.tar.gz` from the release and check it
  with `gh attestation verify <tarball> --repo sjawhar/legion --cert-identity https://github.com/sjawhar/legion/.github/workflows/release-envoy-infra.yaml@refs/heads/main --source-ref refs/heads/main --deny-self-hosted-runners --source-digest <the tag's commit>`
  (`--repo` alone accepts an attestation from any workflow run in Legion). Then run
  `pulumi plugin install resource envoy-infra <v> --file <tarball>`, delete `Pulumi.yaml`'s
  `packages.envoy-infra` entry if one exists (with it in place, `package add` fails or keeps the old
  version), and run
  `pulumi package add envoy-infra@<version> --server 'https://github.com/sjawhar/legion/releases/download/envoy-infra-v${VERSION}'`.
- [x] `index.ts` passes `adopt.watchdog` only when it passes `watchdog`: `host:Listener` refuses an
  `adopt.watchdog` with no `watchdog` to take it over (Legion's `host/listener.go`, every release).
- [x] Repeat the preview pass condition. Then, with `ENVOY_DEPLOY_WATCHDOG` unset, run
  `secrets PULUMI_CONFIG_PASSPHRASE -- pulumi up --stack prod`. The provider and the `pulumi`
  default provider that the apply adds exist only in state; `pulumi preview --json` lists no step
  for them.
- [x] After the apply, on each of `sami-agents`, `sami` and `oryx`:
  - `docker inspect envoy-listener --format '{{.Created}} {{.State.Health.Status}}'` shows
    `healthy` and the creation time Task 2.1 recorded;
  - `curl -fsS http://127.0.0.1:9020/healthz` answers `"status":"healthy"`.
- [x] An Envoy message from a laptop session to a devbox session is delivered.
- [x] Docs: `envoy/AGENTS.md` and `README.md` say three things: the listener comes from Legion's
  plugin, how to move its pin (`pulumi package add envoy-infra@<v> …` regenerates `sdks/`), and
  that the CLI is pinned in `mise.toml`. Remove the README's `listener.webhooks` row (D4), and
  correct the README line that called the passphrase a human-tier key: it is agent tier on the
  devbox and the laptop.
- [x] Commit and push to `main`.

Applied 2026-10-15 18:57Z with `envoy-infra` v0.3.1: the preview planned `create: 3` (the three
`host:Listener` components) and `same: 10`, and the update matched it. All three listeners kept
their container ids and Task 2.1's creation times and answer `healthy`; the laptop's separate
Theorem listener was untouched. A laptop session's message reached the Legion PO's session on the
devbox, and its reply came back.

---

## Slice 4b: this laptop's Theorem-profile sessions

Done 2026-10-17. The laptop is on Theorem's tailnet, and Theorem's hub serves Envoy's listener to
sessions that hold no credential, so this laptop runs no Theorem listener and holds no Theorem
seed.

- [x] Both Theorem profiles' `envoy.json` files are written on the laptop with mode 0600 and never
  committed: `~/.omp/profiles/theorem/agent/envoy.json` and
  `~/.dotfiles/.claude-profiles/theorem/envoy.json`. Each has `dispatch` disabled, and an `envoy`
  section whose `url` is the hub listener's tailnet address on port 9020 and whose `natsUrls` is its
  NATS address on port 4222, with no `natsSeed` and no `token`.
- [x] A Theorem-profile omp session started on the laptop with these files lists the sessions on
  Theorem's hub, and a message it sent reached a session on another machine.
- [x] The temporary Theorem listener the laptop ran on port 9021 (`envoy-listener-theorem-stopgap`)
  is removed. A running session picks up the new file when it restarts.

---

## Hardening ledger

- The `envoy-infra` plugin is pinned by version from a GitHub release. This is repaid in Task 2.3,
  which verifies the release tarball's provenance attestation before installing it. The pre-merge
  run in Task 2.2 uses a branch build made locally, which has no attestation.

## End-to-end verification plan

| Scenario | Slice | Class | Surface and transport | Identity and grant | Driver |
| --- | --- | --- | --- | --- | --- |
| C1. `prod` preview creates only the three `host:Listener` components | 2 | infrastructure | the real `prod` stack on its S3 backend, run from the devbox with the branch plugin and then the released plugin | devbox instance role; the agent-tier passphrase | Tasks 2.2, 2.3 |
| C2. All three listeners unchanged and delivering after `pulumi up` | 2 | infrastructure | the three hosts over Tailscale SSH; an Envoy message from the laptop to the devbox | same | Task 2.3, against Task 2.1's baseline |
| C3. Theorem-profile sessions on the laptop message through Theorem's hub, invisible to the other organization's Envoy | 4b | infrastructure | a real Theorem-profile omp session on the laptop, on Theorem's tailnet | the hub's credential-free session user | Slice 4b |

## Skill catalog

| Step | Worker | Required skills |
| --- | --- | --- |
| Tasks 2.0-2.3 | `deep` | `dotfiles-conventions`, `using-docker`, `using-secrets`, `verification-before-completion` |
| Docs | `deep` | `updating-docs` |
| Review | `reviewer` | `review-judgment` |

## Contract change census

- `envoy/services.ts` (`ENVOY_NATS_URL`, `createListener`), `images.ts` and `watchdog.ts` are
  removed. `git grep -n -e '/services"' -e '/images"' -e '/watchdog"' -- envoy` lists `index.ts`
  and `__tests__/services.test.ts`; the first is rewritten and the second deleted.
- Webhook support leaves the program (D4). At `7c283d6`, `git grep -n -i webhook -- envoy` lists:
  - `index.ts:16-23`, which reads the three webhook secrets (removed);
  - `machines.ts:4,7`, the fields (removed);
  - `services.ts` (deleted);
  - `README.md:102`, the row (removed).

  The stack's `envoy:githubWebhookSecret` and its siblings stay in `Pulumi.prod.yaml`, together
  with their README rows (`:78,84`) and `AGENTS.md:5,23`. They are carried for the webhook
  listener that runs elsewhere, and the program exports nothing, so outputs are unchanged.

## Permission source census

| Permission | Holder | Granted by | Enforced by | Test |
| --- | --- | --- | --- | --- |
| Read and write `prod` state | the devbox's instance role | its IAM policy on the state bucket | AWS S3 | C1, C2 |
| Decrypt `prod` secrets | any process that reads the agent-tier secrets on the devbox or the laptop | secretsd agent tier (`PULUMI_CONFIG_PASSPHRASE`) | the passphrase | C1, C2 |
| Docker over SSH on `sami` (user `sami`) and `oryx` (user `agent`) | the devbox's SSH identity | Tailscale SSH policy | Tailscale | C1, C2 |
| NATS access as a Theorem-profile session | any client on Theorem's tailnet that reaches the hub | agent-infra's hub, `gcp:NatsHub` `sessionUser` | nats-server authorization, as `no_auth_user` | C3 |
