# sami-devbox: provision and drill (dispatch://INFRA-993, cutover step 2)

sami-devbox is the Google Cloud VM (us-west4-a, `c4-highmem-288-lssd`) that replaces sami-rocqperf
and sami-leanperf. agent-infra declares it (`bootstrap/samidevbox.go`) and its outside responder
(`monitoring/responder.go`); these files make it a machine a session can move to, and prove it.
Step 2 runs with no session on the machine. It never copies session data (step 3) and never
moves a session (step 5).

| File | Runs where | What |
| --- | --- | --- |
| `devbox/gce-disks`, `gce-disks.service`, `gce-disks-ready.conf` | sami-devbox, root, every boot | local-SSD RAID 0 at `/mnt/scratch` with a 256 GiB swap file; state disk at `/mnt/nvme`; cold disk at `/mnt/durable`; `/home`, `/theorem`, `/theorem-ext4` and `/theorem-xfs` bound from `/mnt/nvme/<name>`; Docker, containerd and Sysbox data on the state disk; `/tmp` and `/var/tmp` bound from the scratch at boot only ("The boot disk holds the OS only"). Ordered before `user@1000`, ssh, Docker. |
| `devbox/gce-host.sh` | sami-devbox, sudo, once | packages, the disk units, journald's cap (`devbox/journald-cap.conf`), Docker, Sysbox (`installers/sysbox.sh`), Tailscale (`installers/tailscale.sh`), linger, sysstat |
| `devbox/sami-devbox-provision` | sami-rocqperf | the whole provisioning run, step by step (its header lists the steps) |
| `devbox/sami-devbox-model-routes` | sami-devbox, by `sami-devbox-provision` step install | omp's OpenAI agents and roles (openai-codex/ and openai/) go to the gateway's openai/ group as `ossv-gateway/openai/<id>`, since this machine holds no ChatGPT login; edits only its own `omp/models.local.yml` and `omp/config.local.yml` |
| `devbox/sami-devbox-check` | sami-devbox | A2, and the disks half of A5; `--mark-ready` writes `/mnt/nvme/sami/cutover/target-ready`, which `agentbox move --to sami-devbox` requires |
| `devbox/sami-devbox-drill` | sami-rocqperf | A5 and A6: `restart`, then `silence` |

## Before the run

- agent-infra `bootstrap/` and `monitoring/` applied with sami-devbox in us-west4-a (#1341,
  #1351, #1386): the VM, its disks, its tailnet identity, the dev-box@ token grant, the CronJob
  `box-monitor` and the monitor `box-monitor-silent-sami-devbox`.
- `TAILSCALE_CLIENT_ID`: bootstrap/'s output `samiDevboxTailnetClientId`. dev-sjawhar@ cannot
  read the state bucket, so the INFRA lead gives the value.
- For the az check of A2: agent-infra #1146 (the `agent-azure` identity) applied, and its client
  id in `~/.config/agent-azure/client-id` on sami-devbox.

## The run

```sh
TAILSCALE_CLIENT_ID=<id> ~/.dotfiles/devbox/sami-devbox-provision   # on sami-rocqperf
~/.dotfiles/devbox/sami-devbox-drill restart
~/.dotfiles/devbox/sami-devbox-drill silence
```

`--from STEP` resumes the provisioning run at a step; every step can run again. One step needs a
person:

- `broker`: `agent-secrets-login` prints a code that Sami types at Dispatch's
  `/credentials/machine` page. Until then the machine has no GitHub App token, so the first
  `install.sh` pass (step `tools`) stops at `installers/omp.sh`, whose private marketplaces clone
  through that token. The `install` step runs `install.sh` again after the login.

The drill's `silence` suspends the CronJob itself: dev-sjawhar@ may get and patch
`cronjobs/box-monitor` in namespace `box-monitor` and nothing else there (agent-infra
cluster-grants, theorem-labs/agent-infra#1418). Check it with the plain form,
`kubectl auth can-i patch cronjobs/box-monitor -n box-monitor`; the `cronjobs.batch/<name>` form
answers yes for any name. The drill puts a Datadog downtime on the monitor first, so its alert
does not post to `#fleet-alerts`, and resumes the CronJob after 35 minutes. Run it as
`sami-devbox-drill silence --handle @slack-Theorem-fleet-alerts`: the notification proof is a
twin monitor whose title starts with `DRILL`, which posts there for real. A pass needs the
twin's Alert, its Datadog event (status error, `_dd.has_notification` true), and its post in
the channel. Before agent-infra #1464 a silent responder showed No Data, not Alert, and paged
nobody (the drills of 2026-11-02 17:37Z and 19:16Z).

## The secret files (step 2's copy)

`sami-devbox-provision`'s `SECRET_FILES` lists them, each with why. They go over Tailscale SSH,
never into a repository. No person's login is among them: the Google, Azure and GitHub sign-ins
of the spec's exclusion list stay on sami-rocqperf, and so does `~/.config/gcloud` as a whole
(its `credentials.db` holds Sami's refresh token).

`.dotfiles/secrets.env` is the one of them git tracks, and its copy differs from its commit on
purpose. `scripts/dotfiles-follow`, which fast-forwards `~/.dotfiles` here every 10 minutes,
keeps every `SECRET_FILES` path that git tracks (read from this list, not a second one) and
refuses on any other change; it also refuses when main changes such a file
(`scripts/dotfiles-follow.md`).

The public-text leak check that `shims/gh` requires comes from a tag `leak-check-vN` of
theorem-labs/agent-infra's `leak-check/`, installed as that tag's `leak-check/INSTALL.md` says.
`devbox/leak-check-tag.env` names the tag, its commit and the sha256 of the three files this machine
holds. The `leakcheck` step reads them from sami-rocqperf's agent-infra clone with `git show`,
checks their sha256, requires that the shared path already holds them, installs them in
`~/.local/share/public-leak-check` here, points this machine's pointer at it, and appends its
TAKEN, INSTALLED and RELEASED lines to the live install record; `agentbox/mounts` gives boxes that
directory. `sami-devbox-check` compares the three files with the sha256 in
`devbox/leak-check-tag.env` on every run and FAILs when they differ; then run
`sami-devbox-provision --only leakcheck`. To install a new tag, change `devbox/leak-check-tag.env`
as its header says, then run that step.

## A2: each check, its command, what passes

Host half (`sami-devbox-check`, on sami-devbox as sami):

| Check | Command | Passes when |
| --- | --- | --- |
| host | `hostname -s`, `id -u` | `sami-devbox`, `1000` |
| tailnet | `tailscale status --self --json` | HostName `sami-devbox`, Tags `tag:sami-devbox` |
| attached account | metadata `service-accounts/default/email` | `dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com` |
| no credential file | `~/.config/gcloud/box_credential_config.json`, `$GOOGLE_APPLICATION_CREDENTIALS` | neither exists |
| disks | `findmnt`, `stat` | `/mnt/nvme` = `google-state` xfs, `/mnt/durable` = `google-cold` xfs, `/home`, `/theorem`, `/theorem-ext4`, `/theorem-xfs` = `/mnt/nvme/<name>`, `/mnt/scratch` on `/dev/md*`, swap `/mnt/scratch/swapfile`; `/tmp`, `/var/tmp` = `/mnt/scratch/tmp`, `/mnt/scratch/var-tmp` (a WAIT until the next planned reset) |
| nofile | `systemctl show tailscaled ssh -p LimitNOFILESoft`; `grep 'open files' /proc/$(pgrep -o -x tailscaled)/limits` | both units `65536`; the running tailscaled's soft limit 65536 (a WAIT until tailscaled restarts, at the next planned reset) |
| root disk (`--root` runs only this) | `df /`; `sudo timeout 120 du -x /` | `/` uses at most 40 GiB; no directory on it outside `/usr`, `/var/lib/apt`, `/var/cache/apt`, `/boot`, `/snap`, `/var/lib/snapd`, `/opt`, `/var/log` holds over 1 GiB of its own; `/var/log` at most 2 GiB |
| no login files | `sudo find / /mnt/nvme /mnt/durable /mnt/scratch -xdev` | no `application_default_credentials.json`, `box_credential_config.json`, `credentialed_accounts.db`, `.config/gh/hosts.yml`; no `legacy_credentials` unless every file in it is a keyless `external_account` `adc.json` (no `private_key`, no `refresh_token`) or a `.boto` that only names one (a note: moves carry sessions' own gcloud directories holding dev-sjawhar@'s keyless external_account). A `service_account` key fails, as a user login does. The spec's A2 names the directory by name alone; dispatch://INFRA-993 records the change |
| no gcloud login | every `credentials.db` | no row of type `authorized_user` |
| no Azure login | every `azureProfile.json` | no subscription whose user type is `user` |
| dotfiles follow main | `systemctl --user is-active dotfiles-follow.timer`, `systemctl --user show dotfiles-follow.service`, its state file | timer active; last run exited 0; that run, at most 30 min ago, left HEAD on origin/main with nothing open and the tools installed |
| mise pins | `mise ls --missing` (`sami-devbox-check --pins` adds the box half's `knives --version` line; `dotfiles-follow` runs it on the host after each fast-forward) | nothing missing |
| openai routes | `yq` of `~/.dotfiles/omp/config.local.yml` (`modelRoles.slow`, `.deep`, `task.agentModelOverrides.astra`, `["sami-proxy"]`) and of `~/.omp/agent/models.yml` | each names `ossv-gateway/openai/<id>`; ossv-gateway declares 4 or more `openai/` models |
| GitHub, plain shell | `git ls-remote https://github.com/theorem-labs/{machcsl-riscv,oss-verify-proofs-internal} HEAD` with no agent marker and no routing include, as `agentbox move --to` runs over ssh | a sha for each (the App's token, through `installers/gh-app-host.sh`) |
| ssh to sami-rocqperf | `ssh -o BatchMode=yes -o ConnectTimeout=15 sami@sami-rocqperf true` (movers run scripts there after the move) | exit 0; a time-out is a WAIT while the tailnet rule tag:sami-devbox -> sami-rocqperf tcp:22 is pending (dispatch://INFRA-734) |
| N6 tools follow ops | `systemctl --user is-active ossv-tree-follow.timer`, `systemctl --user show ossv-tree-follow.service`, `git -C ~/src/oss-verifier rev-parse HEAD refs/remotes/origin/ops` | timer active; last run succeeded at most 15 min ago; HEAD = origin/ops (`devbox/ossv-tree-follow`) |
| N6 tools are rocqperf's | `sha256sum` of `bin/ossv-{n6-farm,n6-shards,n6-pod,job,job-farm}` and `notes/{ossv-n6-peaks.tsv,ossv-n6.md,ossv-job.md}` here and on sami-rocqperf over ssh | equal, file by file |
| N6 clones on the state disk | `stat -c %d` of `~/src/oss-verify-proofs-internal`, `~/src/oss-verifier` and `/mnt/nvme` | one device |
| N6 farm, host | the box half's N6 lines below, run on the host | as below |

Box half (`agentbox new --dir /mnt/nvme/sami/cutover --dir /mnt/nvme/sami/boxid -- sami-devbox-check --in-box`):

| Check | Command | Passes when |
| --- | --- | --- |
| Google Cloud | `gcloud auth list --filter=status:ACTIVE`; tokeninfo of `gcloud auth print-access-token` | both name dev-sjawhar@ |
| build pool | `kubectl --context gke_theorem-agent-infra_us-west1_dispatch -n builds auth can-i create jobs` | `yes` |
| boxid paths | `CLOUDSDK_CONFIG=/mnt/nvme/sami/boxid/gcloud-box gcloud auth print-access-token` to tokeninfo; `kubectl --kubeconfig /mnt/nvme/sami/boxid/infra-kubeconfig get namespaces` (`installers/gcloud-boxid.sh`; the paths sami-rocqperf's scripts name) | dev-sjawhar@; `namespace/builds` listed |
| secrets broker | `$AGENT_SECRETS_KEY_DIR/enrollment`; `https://secrets.tail12ec1c.ts.net/healthz` | an enrollment id; 200 |
| secrets, agent tier | `secrets CERAMIC_API_KEY -- …` | a non-empty value |
| Envoy | `envoy ps --json`; tcp `envoy-hub.tail12ec1c.ts.net:4222` | answers; connects |
| Dispatch | `dispatch read --issue INFRA-993` | `Title: sami-devbox…` |
| MLflow | `http://mlflow.tail12ec1c.ts.net/health` | `OK` |
| model gateway | the `email` of `$XDG_RUNTIME_DIR/ossv-box/gateway-token`; `/v1/models` with it | dev-box@; 200 |
| openai through the gateway | `omp -p "say ok" --model @slow`, and `--model ossv-gateway/openai/gpt-6-astra:xhigh` | each answers `ok` (a fail prints `Use /login` or an HTTP 4xx) |
| Azure | `az account show` | user type `servicePrincipal` |
| GitHub | the same two `git ls-remote` with the box's routing include | a sha for each |
| N6 tools | `sha256sum` of each of the 8 files above in `~/src/oss-verifier` against `git show HEAD:<file>` | equal |
| N6 repository | `git -C ~/src/oss-verify-proofs-internal rev-parse --is-shallow-repository`, `config remote.origin.promisor`, `extensions.partialclone`; `git archive HEAD \| tar -t \| head -100` | `false`, unset, unset; 100 entries |
| N6 signers | for `build-io-signer@` and `ossv-runs-signer@`: `gcloud auth print-access-token --impersonate-service-account=<sa>`; `gcloud iam service-accounts sign-blob --iam-account=<sa>` | a token; a signature |
| N6 buckets | `gcloud storage cat -r 0-63` of one object under the newest `out/ossv/` prefix of `gs://theorem-build-io` and of `gs://theorem-ossv-runs` | bytes from each |
| N6 farm namespace | `kubectl --context gke_theorem-agent-infra_us-west1_dispatch -n builds-ossv auth can-i create jobs` | `yes` |
| N6 release asset | `gh release download --repo theorem-labs/oss-verify-proofs-internal <newest published tag> -p <its smallest asset>` through `shims/gh` as an agent call | the asset's size |
| N6 disk-gate | `disk-gate status`; `disk-gate reserve 1 <name>`, `status`, `release <name>` | a floor of at least 2.25% of `/mnt/nvme` (369 GiB of 16,383); granted, listed, released |
| N6 box paths (box only) | `findmnt --target` of `/mnt/nvme/sami/ossv-n6` and `~/.cache/ossv-n6-runs` | each a bind from the host (`scripts/agentbox`, `agentbox/mounts`) |

## A5 and A6 (the drill)

| Check | How | Passes when |
| --- | --- | --- |
| A5 | a marker on `/home`, `/mnt/nvme`, `/mnt/durable` and `/mnt/scratch`; `gcloud compute instances stop`; the responder starts the VM | the three markers are unchanged, the scratch one is gone, `/mnt/scratch` is on the RAID, the swap file is on, `gce-disks.service` is active and `user@1000` started after it |
| A6, start | seconds from TERMINATED to the next status | at most 600 |
| A6, silence | the CronJob suspended 35 minutes | the monitor `Box-monitor silent for sami-devbox` goes to Alert, and back to OK after the resume |

## The boot disk holds the OS only

The 500 GiB boot disk holds the OS and nothing that grows with work (spec "Shape and disks";
sami-rocqperf's root filled up). `devbox/gce-disks` makes each bind below at every boot, before
`user@1000`, ssh and Docker start. A2's "root disk" line (`sami-devbox-check --root`) FAILs when
something grows on `/` again.

| Path | Bound from | In effect |
| --- | --- | --- |
| `/home` | `/mnt/nvme/home` | since provisioning |
| `/var/lib/docker`, `/var/lib/containerd`, `/var/lib/sysbox` | `/mnt/nvme/<name>` | since provisioning |
| `/theorem`, `/theorem-ext4`, `/theorem-xfs` | `/mnt/nvme/<name>` | since 2026-11-03 17:21Z |
| `/tmp`, `/var/tmp` | `/mnt/scratch/tmp`, `/mnt/scratch/var-tmp` (mode 1777) | from the next planned reset |

journald stays in `/var/log/journal`, at most 2 GiB, and keeps 50 GiB of `/` free
(`devbox/journald-cap.conf`, installed by `devbox/gce-host.sh`).

### The lane paths

On 2026-11-03 12:16-12:23Z a lane's move from sami-leanperf wrote 10 GiB of build logs into
`/theorem` and `/theorem-xfs` on the boot disk. At 17:18-17:21Z they moved to the state disk under
`cgroup-guard.sh`: a file only on the boot disk was copied; a file on both disks with other bytes
kept the lane's newer copy at its path and the bulk copy's beside it as `<file>.leanperf-copy`.
Then the binds were made on the live system. The lane had agreed, and had stopped its writer.

An agent box binds its `--dir` paths when it starts, so a bind made later on the host does not
reach a running box: its writes still go to the boot-disk directory under the bind. A session
that writes there stops its writers, waits for the host bind, and starts a new box with
`agentbox move --self`. The boot-disk directory under a bind must then be emptied before the next
boot: at boot `gce-disks` refuses to bind over a target that holds data when the state disk's
copy holds data too, and the machine is then not ready.

### /tmp and /var/tmp

`gce-disks` binds `/tmp` and `/var/tmp` only while the machine boots, before
`multi-user.target`, and never over a live `/tmp`: that would hide every file, socket and lock
the running sessions hold there. So the bind takes effect at the next planned reset, one where
every resident session is at a safe point and synced, as on the morning of 2026-11-03. Until then
disk-fill-watch's 50 GiB floor on `/` covers them, A2 shows a WAIT line for each, and a
`systemctl restart gce-disks` (gce-host.sh runs one) leaves them as they are and says so.

At that boot:

- `systemd-tmpfiles-setup` empties the boot disk's `/tmp`, as at every boot now. What a session
  keeps in `/tmp` is lost at a reset with or without the bind: before the reset, copy what must
  stay to `/mnt/nvme/<user>/`.
- `gce-disks` empties `/mnt/scratch/tmp`, the same cleanup for the new `/tmp`, and removes earlier
  boots' `systemd-private-*` directories from `/mnt/scratch/var-tmp`. It moves each entry of the
  boot disk's `/var/tmp` that the scratch one does not have (except `systemd-private-*`), so
  `/var/tmp` keeps its files across the reset as before. Then it binds both and runs
  `systemd-tmpfiles --create --boot` for them (the X11 directories).
- The units that started before `gce-disks` (systemd-resolved, chrony) keep their PrivateTmp
  directories on the boot disk until the next boot.
- A stop of the VM empties both, with the rest of `/mnt/scratch`.

## Next planned reset

What takes effect only at the next planned reset, and what to do after it:

- `/tmp` and `/var/tmp` are bound from `/mnt/scratch` (above); A2's two scratch WAIT lines become
  ok lines.
- tailscaled restarts with the nofile drop-in (`devbox/nofile.conf`, installed by gce-host.sh as
  `/etc/systemd/system/tailscaled.service.d/60-nofile.conf`). Then remove MoveShepherd's
  `ulimit -n 65536` line at the top of sami-devbox's `~/.bashrc` (keep the backup
  `~/.bashrc.bak-20261103T1900Z`), and show that a fresh ssh gets `ulimit -Sn` = 65536:

  ```sh
  # from another machine, after the reset (or after `sudo systemctl restart tailscaled`):
  ssh sami@sami-devbox 'bash --norc --noprofile -c "ulimit -Sn"'   # must print 65536
  ssh sami@sami-devbox 'grep "open files" /proc/$(pgrep -o -x tailscaled)/limits'  # soft 65536
  ssh sami@sami-devbox "sed -i '/^# MoveShepherd 2026-11-03: git receive-pack/d; /^ulimit -n 65536 2>\/dev\/null\$/d' ~/.bashrc"
  ssh sami@sami-devbox 'bash --norc --noprofile -c "ulimit -Sn"; bash -ic "ulimit -Sn"'  # 65536 twice
  ```

  A2's tailscaled nofile line then reads ok instead of WAIT.

## Starting a session

Every session on sami-devbox runs in an agent box (spec R2). `scripts/agent-new` boxes by default
on a Google VM; run each line on sami-devbox, or after `ssh sami@sami-devbox` from another machine:

```sh
agent-new --repo <repo> --name <N> @<brief>   # start: worktree ~/src/<repo>-<N>, a box, tmux window agents:<N>
tmux capture-pane -p -t agents:<N>; agentbox ls; envoy ps | grep '<repo>-<N>'   # see
tmux kill-window -t agents:<N>; agent-workspace rm ~/src/<repo>-<N>   # stop: the box closes, its knives claims go; then the tree
```

The brief must be where the box sees it (`~/src`, `/mnt/durable/sami`) or in a directory of its own,
which the box gets as a `--dir`. The worktree is a host directory, so it keeps its work after the box
closes; `agent-workspace rm` refuses a tree with unpushed work.

## Step 3's first action

`sami-devbox-provision --only speed` measures ssh over Tailscale from sami-rocqperf to sami-devbox
(one stream and four), the path the bulk copy takes. No host in us-west4 that this work may use
existed before sami-devbox (2026-11-02: the only running us-west4 VM was another person's).

## Step 3: bulk copy

The first pass goes through a GCS relay, `gs://sami-devbox-copy-usw4` (us-west4, Standard,
objects deleted after 7 days). The old box writes tar shards of about 6 GiB; sami-devbox
extracts each one and deletes the object. Later passes are `rsync` over Tailscale, into the
same targets, until a pass copies under 50 GiB.

### Where each tree goes

| From | To |
| --- | --- |
| rocqperf `/mnt/durable` (`sami/` first, then the rest) | `/mnt/durable`, same paths |
| rocqperf `/mnt/nvme/sami` | `/mnt/nvme/sami` |
| rocqperf `/home/sami` | stage: `/mnt/nvme/cutover-stage/sami-rocqperf/home/sami` |
| leanperf `/theorem-ext4`, `/theorem-xfs` (first), `/theorem` | `/mnt/nvme/theorem-ext4`, `/mnt/nvme/theorem-xfs`, `/mnt/nvme/theorem` |
| leanperf `/home/sami` | stage: `/mnt/nvme/cutover-stage/sami-leanperf/home/sami` |

### The stage

A home never extracts over the live `/home/sami`: its gcloud config, its hand-made user units,
its `~/.omp` (global omp state copies only at step 5) and its dotfiles working copy would
replace the new machine's. Each old box has one directory under `/mnt/nvme/cutover-stage/`,
readable by its owners, with a `README.md` that names what is in it and the copy date.

No unit file and no `.wants` link goes into a live `~/.config/systemd/user`. A service moves
at step 4, when its owner moves it, from the stage, and only after the old box's copy is
stopped and disabled.

### The old leanperf paths

Lanes have `/theorem`, `/theorem-ext4` and `/theorem-xfs` in their scripts. `devbox/gce-disks`
binds `/mnt/nvme/<name>` on each at every boot; see "The lane paths" below.

### What the copy leaves out

- Logins: `~/.config/gcloud/{application_default_credentials.json,legacy_credentials,credentialed_accounts.db,credentials.db,access_tokens.db}`, `~/.azure`, `~/.config/gh/hosts.yml`.
- `/mnt/nvme/sami/boxid` (an Azure federation credential; it breaks gcloud on Google; `installers/gcloud-boxid.sh` makes its `gcloud-box` and `infra-kubeconfig` here as the VM's account) and `/mnt/nvme/sami/session-tmp`.
- Caches that installers rebuild: `/mnt/nvme/sami/{gocache,cache,gomodcache,uvcache,git-objects,agentbox,elan}`, `~/.cache`, `~/.mise`.
- rocqperf `/mnt/durable/lost+found` and `/mnt/durable/swapfile-overflow`.

A file that changes during the first pass is fine: the rsync passes bring the deltas. Hard
links between shards arrive as copies; the last rsync pass (`-H`) restores them.

### Load guards

Every process on an old box runs under `nice -n 19 ionice -c3`. rocqperf's `/mnt/durable`
(`nvme0n2`, an Azure P50 capped near 7,500 IOPS and 250 MB/s) has the `none` scheduler, so
ionice does nothing there: the uploader runs in a system scope with cgroup `io.max` on
`nvme0n2`, about a quarter of the disk's read IOPS, raised only while IO pressure stays flat.
`monitor.sh` samples the source disk and `/proc/pressure/io` every minute. A disk floor
(rocqperf `/` 18 GiB, `/mnt/nvme` 84 GB; sami-devbox `/` 50 GiB, `/mnt/nvme` 400 GiB,
`/mnt/scratch` 500 GiB, `/mnt/durable` 200 GiB) or a disk-fill-watch incident freezes the
upload units at once; they thaw when it clears. IO pressure above the limit for three minutes
stops new shards.

### Scripts and logs

The scripts are in `devbox/bulk-copy/`: `mkshards.py` (shard lists from a `find` listing),
`upload.sh` (old box), `pull.sh` (sami-devbox), `monitor.sh` (old box). All restart from their
logs and skip shards already done.

| What | Where |
| --- | --- |
| shard lists, upload log, monitor log, EXCLUDE | rocqperf `/mnt/durable/sami/devbox-copy/` (nothing on its `/mnt/nvme` or `/`); leanperf `~/devbox-copy/` |
| extract log (shard, bytes, times, result) | sami-devbox `/mnt/nvme/sami/cutover/bulk-copy.log` |

### Every mass write runs under cgroup-guard; never stop the puller without the drain

A cgroup must never end holding millions of the inodes it wrote. When it does, this kernel
(7.0.0-1014-gcp, before torvalds f6988c90671e) moves them to the parent at 1-200 inodes/s,
slowing as it goes, and every umount and `sync` hangs behind it until a reset (2026-11-02, and
2026-11-03 12:05Z: `devbox-copy-snappar`, 15 rsyncs with `MemoryHigh=16G`, ended by itself
holding ~15 M inodes; 16G of slab is ~13 M XFS inodes, so `MemoryHigh` does not bound them, and
the reclaim before stop ran only for `pull-unit.sh stop`).

- **Any job that writes or chowns many files** (rsync, tar, cp, `chown -R`, `apply-owners.py`)
  runs as `cgroup-guard.sh run -- CMD`, once at the top of its cgroup. The guard keeps the
  cgroup's slab under 1 GiB (~800k inodes) while CMD runs and drains it to under 256 MiB when CMD
  ends, by itself or on INT/TERM. `snapshot-copy.sh copy` and `pull-unit.sh start` do this. A
  mass write in an ssh session's scope dies with the session: run it inside a guarded unit.
- **The puller**: one persistent unit, `devbox-copy-pull`, runs both pull loops; start it with
  `pull-unit.sh start`. **Never stop it except with `pull-unit.sh stop`**, which drains the
  cgroup first (`GUARD_CG=… cgroup-guard.sh drain`) and only then stops it. To pause the copy,
  stop the uploads on the old boxes, not the puller.
- Test of 2026-11-03 (198,279 files, 11 GiB): with the reclaim first, one `inode_switch_wbs`
  event in the 60 s after the stop and no hung umount. The guard's own tests:
  `scripts/tests/test_bulk_copy_cgroup_guard.py`.

Data a lane owns may be marked `.no-offbox` or listed in `EXCLUDE`; `upload.sh` refuses to start
without `EXCLUDE`, `exclude-filter.py`, `offbox-gate.py` and `no-offbox`, and checks each shard
with `offbox-gate.py`, which runs `no-offbox --root ROOT --from LIST`. One exception, listed in
`OFFBOX-EXCEPT`: inside `review-evidence`, an **empty** `.no-offbox` is a test fixture
(SendGateReview12's replays) and copies as data; a marker with text still refuses and goes to
LANE-C (LANE-C, 2026-11-03).

## Known issues

**A box close can wait many minutes in `umount`.** Seen 2026-11-02 from 19:40Z: after a box
exited, its launcher's `sudo umount <boxes>/<box>` stayed in D state for over 15 minutes
(kernel 7.0.0-1014-gcp; stack `cgroup_writeback_umount` → `__flush_workqueue`; dmesg `task
umount:… blocked for more than 368 seconds`). Two kworkers held the CPU in
`cleanup_offline_cgwbs_workfn` / `inode_prepare_wbs_switch` and in `inode_switch_wbs_work_fn` on
a spin lock: the kernel moves every inode a dead cgroup wrote to its parent's writeback, and each
umount on the machine waits for that one global queue (upstream:
<https://lkml.iu.edu/2605.2/01175.html>). The container, its broker key and its knives claims are
already released by then (`close_box` order); only the box's disk image and tree wait. The likely
trigger is millions of files written by a process whose cgroup then ended (a bulk-copy extract,
an ssh session's scope). Mitigation: run every long writer in a persistent systemd unit, so its
cgroup stays alive and its inodes never need moving.
