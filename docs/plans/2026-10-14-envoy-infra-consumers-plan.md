# Envoy listeners from Legion's `envoy-infra` plugin: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** the dotfiles Envoy stack deploys every listener through Legion's
`envoy-infra:host:Listener` component, Trajectory's first with no diff, and then also this laptop's
Theorem listener beside the existing one.

**Architecture:** one Pulumi project and stack, `envoy/` `prod`. It keeps its machines, and it
replaces `services.ts`, `images.ts` and `watchdog.ts` with one `host.Listener` per machine. Each
listener adopts its machine's existing container, image and watchdog by name, so nothing is
recreated. A machine can also list extra listeners. The laptop's Theorem listener is one of these:
on port 9021, with its NATS URL held as a stack secret and its seed where Sami's open decision in
Task 4b.2 puts it. `scripts/envoy-nats-seed` gains a GCP Secret Manager source, so Theorem-profile
sessions read their seed the way default sessions read theirs.

**Tech stack:** TypeScript on ts-node (`envoy/tsconfig.json`), `@pulumi/pulumi`, `@pulumi/docker`
4.11.0, `@pulumi/command` (the version `envoy/package.json` pins), the `envoy-infra` plugin's
generated Node SDK, Pulumi CLI 3.268.0 (pinned in `mise.toml`), bash, Python `unittest`.

**Specs:** Legion's `docs/plans/2026-10-14-envoy-infra-design.md` and its plan
`docs/plans/2026-10-14-envoy-infra-plan.md` (sjawhar/legion#1946), which define the component's
inputs. Theorem's `theorem-labs/agent-infra` `docs/specs/envoy.md` and its plan
`docs/plans/2026-10-14-envoy-plan.md`, which define the values the Theorem listener needs.

## Global constraints

- The `prod` stack moves onto the component only when its preview creates, updates, replaces or
  deletes no cloud resource and leaves outputs unchanged (Sami, 2026-10-13). The only creates the
  move allows are the `envoy-infra:host:Listener` components and any provider that exists only in
  Pulumi state (Sami, 2026-10-14). This stack needs no provider create (its docker providers exist,
  and D6 keeps the command provider out). Sami (2026-10-14): the existing deployments move first, or in parallel with
  the GCP work. This plan moves Trajectory's listeners first, because the Theorem listener is a
  `host:Listener` in the same stack.
- Every `envoy/` edit, preview, apply and `pulumi config set`, and the commits of slices 2 and 4b,
  happen in one devbox clone at `~/.worktrees/dotfiles/envoy-infra`, cut from `origin/main`. The
  laptop runs only the read halves of Task 4b.2's transfers, Task 4b.1's live check and Task 4b.3.
- One Envoy stack in the dotfiles: the Theorem listener is part of `prod`, not a project of its own
  (Sami, 2026-10-14).
- This repository is public. The Theorem hub's NATS URL is a stack secret, encrypted in
  `Pulumi.prod.yaml` like the stack's other secrets, and Theorem's tailnet name never appears in
  plaintext. The laptop's Theorem seed is a stack secret too (Sami, 2026-10-14; Task 4b.2).
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
- **D2. The Theorem listener is created with `waitForHealthy: false`.** The stack is applied from
  the devbox, which reaches the laptop's Docker over Trajectory's tailnet. While the laptop is on
  that tailnet, the Theorem hub's name does not resolve, and the listener exits on its first failed
  connect (`packages/envoy/cmd/listener/main.go:525-530` in Legion). So it cannot be healthy at
  apply time. Docker restarts it (`unless-stopped`), and it becomes healthy once the laptop is on
  Theorem's tailnet. Legion's plan gives `host:Listener` the `waitForHealthy` input.
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

Sami keeps the laptop on one tailnet at a time. Everything in slice 2, and the slice 4b apply, runs
from the devbox with the laptop on Trajectory's tailnet. Only the Theorem listener's health check
and its sessions need Theorem's tailnet, and those belong to agent-infra's Task 4.5.

| Step | Who | When |
| --- | --- | --- |
| Opening a Tailscale SSH check URL if one is due (`envoy/README.md`, "Prerequisites"; it shows only as a Docker provider ping timeout) | Sami | before any task that dials `sami` or `oryx` |
| `gcloud --configuration theorem auth login` if the theorem.dev login has expired | Sami | before Tasks 4b.1-4b.3 |

## Slices

| Slice | Outcome someone can try | Blocked on |
| --- | --- | --- |
| 2 | `pulumi preview --stack prod` shows only the three listener components as creates; after `pulumi up`, all three listeners are unchanged and delivering | Legion slice 1 built (Task 2.2) and released (Task 2.3) |
| 4b | the `prod` stack runs a Theorem listener on the laptop's port 9021, and a Theorem-profile session reads its seed | 4b.1: none. 4b.2: slice 2 done; 4b.1 done (the seed is read through `envoy-nats-seed --gcp-secret`); agent-infra Tasks 4.3 (`natsUrl`) and 4.4 (the laptop's seed written). 4b.3: 4b.1 and 4b.2 |

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
  `envoy/machines.ts` (drop the webhook fields; add `extraListeners`, Task 4b.2), `envoy/AGENTS.md`,
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

- [ ] Once Legion slice 1 is released, remove the `plugins.providers` entry.
- [ ] Download `pulumi-resource-envoy-infra-v<v>-linux-amd64.tar.gz` from the release and check it
  with `gh attestation verify <tarball> --repo sjawhar/legion --cert-identity https://github.com/sjawhar/legion/.github/workflows/release.yaml@refs/heads/main --source-ref refs/heads/main --deny-self-hosted-runners`
  (`--repo` alone accepts an attestation from any workflow run in Legion). Then run
  `pulumi plugin install resource envoy-infra <v> --file <tarball>` and
  `pulumi package add envoy-infra@<version> --server 'https://github.com/sjawhar/legion/releases/download/envoy-infra-v${VERSION}'`.
- [ ] Repeat the preview pass condition. Then, with `ENVOY_DEPLOY_WATCHDOG` unset, run
  `secrets PULUMI_CONFIG_PASSPHRASE -- pulumi up --stack prod`.
- [ ] After the apply, on each of `sami-agents`, `sami` and `oryx`:
  - `docker inspect envoy-listener --format '{{.Created}} {{.State.Health.Status}}'` shows
    `healthy` and the creation time Task 2.1 recorded;
  - `curl -fsS http://127.0.0.1:9020/healthz` answers `"status":"healthy"`.
- [ ] An Envoy message from a laptop session to a devbox session is delivered.
- [ ] Docs: `envoy/AGENTS.md` and `README.md` say three things: the listener comes from Legion's
  plugin, how to move its pin (`pulumi package add envoy-infra@<v> …` regenerates `sdks/`), and
  that the CLI is pinned in `mise.toml`. Remove the README's `listener.webhooks` row
  (`envoy/README.md:102`) (D4), and correct `README.md:72`, which calls the passphrase a human-tier
  key: it is agent tier on the devbox and the laptop.
- [ ] Commit `envoy: deploy listeners with Legion's host:Listener` and push.

---

## Slice 4b: this laptop's Theorem listener

### Task 4b.1: a GCP Secret Manager source for `envoy-nats-seed`

**Files:** Modify `scripts/envoy-nats-seed`, `scripts/tests/test_envoy_nats_seed.py`

- [ ] Add `--gcp-secret <secret> --gcp-project <project> --gcloud-configuration <name>`.
  - Every mode takes these flags (path, `--value`, `--refresh`, `--background`), and
    `refresh_in_background` (`:111-113`) passes them to its re-exec.
  - The cache, lock, refresh and miss rules are today's, under files named for the source:
    `nats-nkey-seed-gcp-<project>.<secret>`, with its `.miss` and lock files (a `.`, since both
    names may hold `-` and neither may hold `.`, so no two sources share a file).
  - The fetch is
    `env -u GCLOUD_ACCOUNT -u CLOUDSDK_ACTIVE_CONFIG_NAME -u CLOUDSDK_CORE_ACCOUNT gcloud --configuration <name> secrets versions access latest --secret <secret> --project <project> --out-file <tmp>`,
    so the value is never in an argv or the environment, and no caller's gcloud selection can
    record a miss for every session.
  - With no source flag, the script behaves exactly as today.
- [ ] Tests, with fake `gcloud` and `aws` on `PATH` that record their argv:
  1. a fetched seed is cached and served by `--value`;
  2. a failing `gcloud` exits 1 under `--value` with nothing on stdout, and stderr holds only the
     script's own lines (today's two for a first-call miss), naming the failure; `gcloud`'s raw
     stderr never passes through;
  3. a GCP cache aged past `REFRESH_S` with `os.utime` makes `gcloud` run with the source's argv
     and replace that cache, and `aws` never runs;
  4. the mirror case: an aged default cache refreshes through `aws`, and `gcloud` never runs;
  5. the two sources never share a cache, miss or lock file.
- [ ] Run `python3 -m unittest scripts/tests/test_envoy_nats_seed.py`. Commit and push to `main`.
- [ ] Update this machine's checkout with `scripts/dotfiles-sync local`. Then
  `~/.dotfiles/scripts/envoy-nats-seed --gcp-secret envoy-nats-seed-sami --gcp-project theorem-agent-infra --gcloud-configuration theorem --value >/dev/null`
  exits 0 once agent-infra Task 4.4 has written the seed.

### Task 4b.2: the Theorem listener in the `prod` stack

**Files:** Modify `envoy/index.ts`, `envoy/machines.ts`, `envoy/Pulumi.prod.yaml`,
`envoy/AGENTS.md`, `envoy/README.md`

- [ ] `MachineConfig` gains `extraListeners?: {name: string; port: number; containerName: string;
  natsUrlKey: string; seedKey: string}[]`.
- [ ] For each extra listener, `index.ts` creates
  `new envoyInfra.host.Listener(extra.name, {machineId: machine.machineId, natsUrls: [cfg.requireSecret(extra.natsUrlKey)], image, port: extra.port, containerName: extra.containerName, seed: cfg.requireSecret(extra.seedKey), waitForHealthy: false}, {providers: [docker]})`
  on that machine's existing docker provider (D2). No watchdog: Legion's R5 refuses one for a
  container other than `envoy-listener`.
- [ ] Machine `sami` gets
  `extraListeners: [{name: sami-theorem, port: 9021, containerName: envoy-listener-theorem, natsUrlKey: theoremNatsUrl, seedKey: theoremNatsSeed}]`.
- [ ] The NATS URL is written by stdin from the laptop, which has the theorem.dev login, into the
  stack on the devbox, which has the passphrase:
  - It is first read on the laptop into a mode-0600 file under a scratch directory:
    `cd <agent-infra clone>/envoy && GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud --configuration theorem auth print-access-token) pulumi stack output natsUrl --stack prod > <dir>/url`
    (in that directory, so mise resolves agent-infra's own Pulumi pin).
    The step stops unless the command exited 0 and the file is non-empty.
  - Then `ssh <devbox> 'cd ~/.worktrees/dotfiles/envoy-infra/envoy && secrets PULUMI_CONFIG_PASSPHRASE -- pulumi config set --secret envoy:theoremNatsUrl --stack prod' < <dir>/url`,
    and the file is removed by its literal path. `pulumi config set` with no value reads stdin.
  - The seed, the same two steps with
    `envoy-nats-seed --gcp-secret envoy-nats-seed-sami --gcp-project theorem-agent-infra --gcloud-configuration theorem --value > <dir>/seed`
    and `envoy:theoremNatsSeed`. A rotation is the same two steps and an apply: the container's
    environment changes, so it is recreated with the new seed.
  - `envoy/AGENTS.md` records the commands.
- [ ] Preview: the only non-`same` steps are the `sami-theorem` component and its image and
  container creates. If anything else shows, stop before `pulumi up` and report it to the
  coordinator with the step, as in Task 2.2. Apply with the laptop on Trajectory's tailnet.
- [ ] Check that `docker inspect envoy-listener-theorem` exists on the laptop with restart policy
  `unless-stopped`, that `envoy-listener`'s `Created` is unchanged, and that
  `docker logs envoy-listener-theorem 2>&1 | grep -c NATS_NKEY_SEED` prints `0`. The listener names
  that variable when the seed it is given is blank or not a user nkey seed
  (`internal/bus/nkey.go:45-63` in Legion), so a `0` means a well-formed seed arrived.
- [ ] Commit `envoy: Theorem's listener on the laptop` and push. Its health is observed on Theorem's
  tailnet, in agent-infra's Task 4.5.

**Where the seed lives (Sami, 2026-10-14).** Sami ruled that the laptop's Theorem seed is a stack
secret, as the stack keeps its other secrets. Its ciphertext therefore sits in the public
`Pulumi.prod.yaml` and in the stack's state in Trajectory's S3 bucket, and the agent-tier
passphrase, which any agent session on the devbox or the laptop can read, decrypts it. The devbox's
Tailscale SSH login as `sami` can also read it from the running container (`docker inspect`) while
a check is open. The options he weighed were a file on the laptop the listener mounts (which needed
a `seedFile` input in Legion) and a move of the whole stack to a backend Trajectory's agents cannot
read.

### Task 4b.3: the Theorem profiles' `envoy.json`

- [ ] On this machine, write both of these files with mode 0600:
  `~/.omp/profiles/theorem/agent/envoy.json` and
  `~/.dotfiles/.claude-profiles/theorem/envoy.json`. Fill `natsUrls` from agent-infra's `envoy`
  stack output `natsUrl`, read as in Task 4b.2 and never typed:

```json
{
  "dispatch": { "enabled": false },
  "envoy": {
    "enabled": true,
    "url": "http://127.0.0.1:9021",
    "natsUrls": ["<natsUrl>"],
    "natsSeed": "!$HOME/.dotfiles/scripts/envoy-nats-seed --gcp-secret envoy-nats-seed-sami --gcp-project theorem-agent-infra --gcloud-configuration theorem --value"
  }
}
```

- [ ] Record the command that writes them in `envoy/AGENTS.md`.
- [ ] The sessions that use these files are agent-infra's Task 4.5, which also starts one
  `cld --profile theorem` session through 9021.

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
| C3. The seed source reads Secret Manager, caches, and refreshes from its own source | 4b | infrastructure | real `gcloud` against `theorem-agent-infra` | Sami's theorem.dev login | Task 4b.1's tests and a live `--value` run |
| C4. The Theorem listener is deployed beside the 9020 one, which stays unchanged | 4b | infrastructure | the `prod` stack applied from the devbox | devbox role; the agent-tier passphrase | Task 4b.2 |
| C5. The Theorem listener is healthy on 9021, and Theorem-profile omp and `cld` sessions message through the hub, invisible to the other organization's Envoy | 4b | infrastructure | real sessions on the laptop, on Theorem's tailnet | the laptop's NATS user | agent-infra Task 4.5 |

## Skill catalog

| Step | Worker | Required skills |
| --- | --- | --- |
| Tasks 2.0-2.3, 4b.1-4b.3 | `deep` | `dotfiles-conventions`, `using-docker`, `using-secrets`, `test-driven-development` (4b.1), `verification-before-completion` |
| Docs | `deep` | `updating-docs` |
| Review | `reviewer` | `review-judgment` |

## Contract change census

- `scripts/envoy-nats-seed` gains flags, and its default behaviour and output are unchanged:
  `git grep -n envoy-nats-seed` (launchers, `agentbox`, tests, docs) shows no caller passing a new
  flag. Rollout: additive.
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
| Read and write `prod` state | the devbox's instance role | its IAM policy on the state bucket | AWS S3 | C1, C4 |
| Decrypt `prod` secrets, the Theorem seed among them | any process that reads the agent-tier secrets on the devbox or the laptop | secretsd agent tier (`PULUMI_CONFIG_PASSPHRASE`) | the passphrase | C1, C4 |
| Docker over SSH on `sami` (user `sami`) and `oryx` (user `agent`) | the devbox's SSH identity | Tailscale SSH policy | Tailscale | C1, C2, C4 |
| Read the laptop's seed secret and agent-infra's `envoy` stack | sami@theorem.dev | ownership of `theorem-agent-infra`; agent-infra bootstrap's `operators` bindings (`roles/storage.objectAdmin` on the state bucket, `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key) | Secret Manager, GCS and Cloud KMS IAM | C3, C4 |
| NATS access as the laptop | the laptop's nkey user | agent-infra's `hosts` config → `gcp:NatsHub` `users` | nats-server authorization | C5 |
