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
  move allows are the `envoy-infra:host:Listener` components: this stack needs no provider create
  (its docker providers exist, and D6 keeps the command provider out), and whether a state-only
  provider create counts as a wrapper is a question Sami has not ruled on. Sami (2026-10-14): the existing deployments move first, or in parallel with
  the GCP work. This plan moves Trajectory's listeners first, because the Theorem listener is a
  `host:Listener` in the same stack.
- Every `envoy/` edit, preview, apply and `pulumi config set`, and the commits of slices 2 and 4b,
  happen in one devbox clone at `~/.worktrees/dotfiles/envoy-infra`, cut from `origin/main`. The
  laptop runs only the read halves of Task 4b.2's transfers, Task 4b.1's live check and Task 4b.3.
- One Envoy stack in the dotfiles: the Theorem listener is part of `prod`, not a project of its own
  (Sami, 2026-10-14).
- This repository is public. The Theorem hub's NATS URL is a stack secret, encrypted in
  `Pulumi.prod.yaml` like the stack's other secrets, and Theorem's tailnet name never appears in
  plaintext. Where the laptop's seed lives is the decision at the end of Task 4b.2.
- Per-machine files (`~/.omp/profiles/theorem/agent/envoy.json`,
  `~/.dotfiles/.claude-profiles/theorem/envoy.json`) are written on the machine and never
  committed.
- No credential goes in an argv or a persistent environment. Secret values travel by stdin or by
  file.
- Use plain git in the clone `~/.worktrees/dotfiles/envoy-infra`: one or two described commits per
  topic, then `git pull --rebase` and a fast-forward push to `main`.

## Rulings made while planning

- **D1. The stack's state stays where the stack keeps it today: the S3 backend `envoy/Pulumi.yaml`
  names.** That follows from one stack, as Sami decided, unless he chooses option B of Task 4b.2's
  decision, which moves it.
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
  `agent@oryx…` has probably never been created. Task 2.1's export decides. With none in state,
  previews and applies run with `ENVOY_DEPLOY_WATCHDOG` unset. With one in state, stop before
  writing the program and report it to the coordinator with the export's entry, because adopting it
  would need a `command` provider create, which the acceptance does not allow.

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
| 4b | the `prod` stack runs a Theorem listener on the laptop's port 9021, and a Theorem-profile session reads its seed | 4b.1: none. 4b.2: slice 2 done; 4b.1 done (every option reads the seed through `envoy-nats-seed --gcp-secret`); agent-infra Tasks 4.3 (`natsUrl`) and 4.4 (the laptop's seed written); Sami's seed-location decision (end of Task 4b.2); under option A, Legion's `seedFile` input released; under option B, the stack's migration to the new backend and the devbox's credential for it. 4b.3: 4b.1 and 4b.2 |

---

## Slice 2: the `prod` stack on `host:Listener`

### Task 2.0: pin the Pulumi CLI and natscli, and push

- [ ] Add `pulumi = "3.268.0"` and `"github:nats-io/natscli" = "<x.y.z>"` (its latest release, as
  an exact version) to `mise.toml`'s `[tools]`.
- [ ] Add `"sdks"` to `envoy/tsconfig.json`'s `exclude`.
- [ ] Commit and push to `main` on its own.
- [ ] Run `scripts/dotfiles-sync` on the devbox. With no host argument it syncs the devbox, the
  laptop and oryx (`dotfiles-sync:14`).
- [ ] Run `mise install` on the laptop and on the devbox. On both, `pulumi version` prints
  `v3.268.0` and `nats --version` answers.

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
    `nats-nkey-seed-gcp-<project>-<secret>`, with its `.miss` and lock files.
  - The fetch is
    `gcloud --configuration <name> secrets versions access latest --secret <secret> --project <project> --out-file <tmp>`,
    so the value is never in an argv or the environment.
  - With no source flag, the script behaves exactly as today.
- [ ] Tests, with fake `gcloud` and `aws` on `PATH` that record their argv:
  1. a fetched seed is cached and served by `--value`;
  2. a failing `gcloud` prints one stderr line naming it, and exits 1 under `--value`;
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

- [ ] Under option A only, first move the plugin pin to the Legion release that adds `seedFile`,
  as Task 2.3 moves it (attestation check, `pulumi plugin install`, `pulumi package add`), and run
  Task 2.2's preview pass condition again: the three adopted listeners must still preview `same`
  under the new plugin.
- [ ] `MachineConfig` gains `extraListeners?: {name: string; port: number; containerName: string;
  natsUrlKey: string; seedKey?: string; seedFile?: string}[]`, with exactly one of the last two,
  as the decision below sets. Under A, `seedFile` is home-relative (`.local/state/envoy-theorem/seed`)
  and `index.ts` builds the absolute path from the machine's `sshHost` user
  (`/home/<user>/<seedFile>`), since Docker needs an absolute bind source and the dotfiles commit
  no absolute path.
- [ ] For each extra listener, `index.ts` creates
  `new envoyInfra.host.Listener(extra.name, {machineId: machine.machineId, natsUrls: [cfg.requireSecret(extra.natsUrlKey)], image, port: extra.port, containerName: extra.containerName, <seed>, waitForHealthy: false}, {providers: [docker]})`
  on that machine's existing docker provider (D2), where `<seed>` is `seed: cfg.requireSecret(extra.seedKey)`
  under options B and C, and under option A `seedFile:` the absolute path built above from the
  machine's `sshHost` user and `extra.seedFile`. No watchdog: Legion's R5
  refuses one for a container other than `envoy-listener`.
- [ ] Machine `sami` gets
  `extraListeners: [{name: sami-theorem, port: 9021, containerName: envoy-listener-theorem, natsUrlKey: theoremNatsUrl, <seed field>}]`,
  with `seedKey: theoremNatsSeed` (B, C) or `seedFile: .local/state/envoy-theorem/seed` (A).
- [ ] The NATS URL is written by stdin from the laptop, which has the theorem.dev login, into the
  stack on the devbox, which has the passphrase:
  - It is first read on the laptop into a mode-0600 file under a scratch directory:
    `GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud --configuration theorem auth print-access-token) pulumi stack output natsUrl --stack prod --cwd <agent-infra clone>/envoy > <dir>/url`.
    The step stops unless the command exited 0 and the file is non-empty.
  - Then `ssh <devbox> 'cd ~/.worktrees/dotfiles/envoy-infra/envoy && secrets PULUMI_CONFIG_PASSPHRASE -- pulumi config set --secret envoy:theoremNatsUrl --stack prod' < <dir>/url`,
    and the file is removed by its literal path. `pulumi config set` with no value reads stdin.
  - The seed goes where the decision below puts it: under C, the same two steps with
    `envoy-nats-seed --gcp-secret envoy-nats-seed-sami --gcp-project theorem-agent-infra --gcloud-configuration theorem --value > <dir>/seed`
    and `envoy:theoremNatsSeed`; under B, both values are set instead in a devbox agent session that
    holds the human-tier grant, from files copied there with `scp` and removed by their literal
    paths after, since a human-tier read refuses a plain `ssh` with a file on stdin. Under A, on the
    laptop only, and nothing reaches the stack:
    `install -d -m 0700 ~/.local/state/envoy-theorem`; fetch with the same `envoy-nats-seed … --value`
    into a temporary file inside that directory; stop unless the command exited 0 and the file is
    non-empty; `chmod 0444` it and `mv` it to `~/.local/state/envoy-theorem/seed`. A rotation is the
    same steps followed by `docker restart envoy-listener-theorem`, since the listener reads its
    seed only when it builds its connection (`internal/bus/nats.go:289,323` in Legion).
  - `envoy/AGENTS.md` records the commands.
- [ ] Preview: the only non-`same` steps are the `sami-theorem` component and its image and
  container creates. If anything else shows, stop before `pulumi up` and report it to the
  coordinator with the step, as in Task 2.2. Apply with the laptop on Trajectory's tailnet.
- [ ] Check that `docker inspect envoy-listener-theorem` exists on the laptop with restart policy
  `unless-stopped`, that `envoy-listener`'s `Created` is unchanged, and that
  `docker logs envoy-listener-theorem 2>&1 | grep -c NATS_NKEY_SEED` prints `0`. The listener names
  that variable when the seed it is given is blank or not a user nkey seed
  (`internal/bus/nkey.go:45-63`), and under A names `NATS_NKEY_SEED_FILE` when the file is
  unreadable or blank (`:32-43`); a missing file fails the apply first, because a `bind` mount
  refuses the container's create. So a `0` means a well-formed seed arrived.
- [ ] Commit `envoy: Theorem's listener on the laptop` and push. Its health is observed on Theorem's
  tailnet, in agent-infra's Task 4.5.

**Decision for Sami: where the laptop's Theorem seed lives.** The seed is the laptop's NATS
credential on Theorem's hub. It works only from a machine on Theorem's tailnet.

| Option | Where the seed is | What reaches Trajectory's systems | What it costs |
| --- | --- | --- | --- |
| **A (recommended)** | a file on the laptop, `~/.local/state/envoy-theorem/seed`, mode 0444 inside a 0700 directory, so on the host only Sami's user reads it and the container's `envoy` user (`packages/envoy/docker/Dockerfile:96,121` in Legion) reads it through a read-only bind mount (checked live on the laptop's Docker 29.9.0); the listener reads it through `NATS_NKEY_SEED_FILE` | nothing of the seed in Trajectory's storage (state, repository, secretsd); the hub's URL stays a stack secret | Legion's `host:Listener` gains a `seedFile` input (a host path it bind-mounts read-only as a `bind` mount, which fails the container's create when the file is missing rather than creating a directory, and sets `NATS_NKEY_SEED_FILE`; refused together with `seed`, and with `watchdog`, whose recreate would come back without the mount), so a Legion release after 0.1.0 and the pin move above. The seed is plaintext on the laptop's disk (under LUKS), readable by Sami's user. It survives reboots; a rotation is the stop-on-failure write above and a restart of the listener. |
| **B** | a stack secret, with the whole `prod` stack moved to a backend and secrets provider Trajectory's agents cannot read (Sami's own bucket and a human-tier passphrase) | the ciphertext, in the public `Pulumi.prod.yaml`; on the devbox, the new backend's credential; the human-tier passphrase, held by the session that runs an apply for the rest of that session after one touch; the decrypted seed, during each apply | a task to migrate the whole stack (export, import into the new backend, change of secrets provider, a no-diff preview there), Trajectory's three listeners included; the devbox needs a credential for the new backend; Sami's YubiKey touch on the first preview or apply in each devbox session; the transfers above run in a devbox agent session holding the grant, since a human-tier read refuses a plain `ssh` with a file on stdin; slice 2's C1 runs on the new backend |
| **C** | a stack secret, as the stack keeps its other secrets | the ciphertext, in the public `Pulumi.prod.yaml` and in the state in Trajectory's S3 bucket; the agent-tier passphrase, which any agent session on the devbox (where Trajectory's agents run) or the laptop can read, decrypts it | nothing to build |

Under every option, the devbox's Tailscale SSH login as `sami`, which the stack drives the laptop's
Docker with, can read the seed on the laptop while a check is open: under A the file (by ssh or
`docker exec`), under B and C the container's environment (by `docker inspect`).

A keeps the seed out of Trajectory's storage without changing who can apply the stack; from the
devbox it stays readable only through that SSH login, as under B and C.

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
| C1. `prod` preview creates only the three `host:Listener` components | 2 | infrastructure | the real `prod` stack on its S3 backend (under option B, again on the new backend after the migration), run from the devbox with the branch plugin and then the released plugin | devbox instance role; the agent-tier passphrase (B, after the migration: the human-tier one) | Tasks 2.2, 2.3 |
| C2. All three listeners unchanged and delivering after `pulumi up` | 2 | infrastructure | the three hosts over Tailscale SSH; an Envoy message from the laptop to the devbox | same | Task 2.3, against Task 2.1's baseline |
| C3. The seed source reads Secret Manager, caches, and refreshes from its own source | 4b | infrastructure | real `gcloud` against `theorem-agent-infra` | Sami's theorem.dev login | Task 4b.1's tests and a live `--value` run |
| C4. The Theorem listener is deployed beside the 9020 one, which stays unchanged | 4b | infrastructure | the `prod` stack applied from the devbox (under option B, from the new backend, with Sami's touch) | devbox role; the agent-tier passphrase (B: the human-tier one) | Task 4b.2 |
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
| Read and write `prod` state | the devbox's instance role (under option B, the new backend's credential) | its IAM policy on the state bucket | AWS S3 | C1, C4 |
| Decrypt `prod` secrets, the Theorem seed among them under options B and C | under A and C, any process that reads the agent-tier secrets on the devbox or the laptop; under B, a session holding the human-tier grant | secretsd agent tier (`PULUMI_CONFIG_PASSPHRASE`); under B, its human tier | the passphrase | C1, C4 |
| Read the laptop's seed file (option A) | Sami's user on the laptop, and the Theorem listener's container through its read-only mount | the file's 0444 mode inside a 0700 directory Sami's user owns | the laptop's file permissions | C4, C5 |
| Docker over SSH on `sami` (user `sami`) and `oryx` (user `agent`) | the devbox's SSH identity | Tailscale SSH policy | Tailscale | C1, C2, C4 |
| Read the laptop's seed secret and agent-infra's `envoy` stack | sami@theorem.dev | ownership of `theorem-agent-infra`; agent-infra bootstrap's `operators` bindings (`roles/storage.objectAdmin` on the state bucket, `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key) | Secret Manager, GCS and Cloud KMS IAM | C3, C4 |
| NATS access as the laptop | the laptop's nkey user | agent-infra's `hosts` config → `gcp:NatsHub` `users` | nats-server authorization | C5 |
