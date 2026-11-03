# sami-devbox: provision and drill (dispatch://INFRA-993, cutover step 2)

sami-devbox is the Google Cloud VM (us-west4-a, `c4-highmem-288-lssd`) that replaces sami-rocqperf
and sami-leanperf. agent-infra declares it (`bootstrap/samidevbox.go`) and its outside responder
(`monitoring/responder.go`); these files make it a machine a session can move to, and prove it.
Step 2 runs with no session on the machine. It never copies session data (step 3) and never
moves a session (step 5).

| File | Runs where | What |
| --- | --- | --- |
| `devbox/gce-disks`, `gce-disks.service`, `gce-disks-ready.conf` | sami-devbox, root, every boot | local-SSD RAID 0 at `/mnt/scratch` with a 256 GiB swap file; state disk at `/mnt/nvme`; cold disk at `/mnt/durable`; `/home` bound from `/mnt/nvme/home`; Docker, containerd and Sysbox data on the state disk. Ordered before `user@1000`, ssh, Docker. |
| `devbox/gce-host.sh` | sami-devbox, sudo, once | packages, the disk units, Docker, Sysbox (`installers/sysbox.sh`), Tailscale (`installers/tailscale.sh`), linger, sysstat |
| `devbox/sami-devbox-provision` | sami-rocqperf | the whole provisioning run, step by step (its header lists the steps) |
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

## A2: each check, its command, what passes

Host half (`sami-devbox-check`, on sami-devbox as sami):

| Check | Command | Passes when |
| --- | --- | --- |
| host | `hostname -s`, `id -u` | `sami-devbox`, `1000` |
| tailnet | `tailscale status --self --json` | HostName `sami-devbox`, Tags `tag:sami-devbox` |
| attached account | metadata `service-accounts/default/email` | `dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com` |
| no credential file | `~/.config/gcloud/box_credential_config.json`, `$GOOGLE_APPLICATION_CREDENTIALS` | neither exists |
| disks | `findmnt` | `/mnt/nvme` = `google-state` xfs, `/mnt/durable` = `google-cold` xfs, `/home` = `/mnt/nvme/home`, `/mnt/scratch` on `/dev/md*`, swap `/mnt/scratch/swapfile` |
| no login files | `sudo find / /mnt/nvme /mnt/durable /mnt/scratch -xdev` | no `application_default_credentials.json`, `box_credential_config.json`, `legacy_credentials`, `credentialed_accounts.db`, `.config/gh/hosts.yml` |
| no gcloud login | every `credentials.db` | no row of type `authorized_user` |
| no Azure login | every `azureProfile.json` | no subscription whose user type is `user` |
| dotfiles follow main | `systemctl --user is-active dotfiles-follow.timer`, `systemctl --user show dotfiles-follow.service`, its state file | timer active; last run exited 0; that run, at most 30 min ago, left HEAD on origin/main with nothing open and the tools installed |
| mise pins | `mise ls --missing` (`sami-devbox-check --pins` adds the box half's `knives --version` line; `dotfiles-follow` runs it on the host after each fast-forward) | nothing missing |
| GitHub, plain shell | `git ls-remote https://github.com/theorem-labs/{machcsl-riscv,oss-verify-proofs-internal} HEAD` with no agent marker and no routing include, as `agentbox move --to` runs over ssh | a sha for each (the App's token, through `installers/gh-app-host.sh`) |

Box half (`agentbox new --dir /mnt/nvme/sami/cutover -- sami-devbox-check --in-box`):

| Check | Command | Passes when |
| --- | --- | --- |
| Google Cloud | `gcloud auth list --filter=status:ACTIVE`; tokeninfo of `gcloud auth print-access-token` | both name dev-sjawhar@ |
| build pool | `kubectl --context gke_theorem-agent-infra_us-west1_dispatch -n builds auth can-i create jobs` | `yes` |
| secrets broker | `$AGENT_SECRETS_KEY_DIR/enrollment`; `https://secrets.tail12ec1c.ts.net/healthz` | an enrollment id; 200 |
| secrets, agent tier | `secrets CERAMIC_API_KEY -- …` | a non-empty value |
| Envoy | `envoy ps --json`; tcp `envoy-hub.tail12ec1c.ts.net:4222` | answers; connects |
| Dispatch | `dispatch read --issue INFRA-993` | `Title: sami-devbox…` |
| MLflow | `http://mlflow.tail12ec1c.ts.net/health` | `OK` |
| model gateway | the `email` of `$XDG_RUNTIME_DIR/ossv-box/gateway-token`; `/v1/models` with it | dev-box@; 200 |
| Azure | `az account show` | user type `servicePrincipal` |
| GitHub | the same two `git ls-remote` with the box's routing include | a sha for each |

## A5 and A6 (the drill)

| Check | How | Passes when |
| --- | --- | --- |
| A5 | a marker on `/home`, `/mnt/nvme`, `/mnt/durable` and `/mnt/scratch`; `gcloud compute instances stop`; the responder starts the VM | the three markers are unchanged, the scratch one is gone, `/mnt/scratch` is on the RAID, the swap file is on, `gce-disks.service` is active and `user@1000` started after it |
| A6, start | seconds from TERMINATED to the next status | at most 600 |
| A6, silence | the CronJob suspended 35 minutes | the monitor `Box-monitor silent for sami-devbox` goes to Alert, and back to OK after the resume |

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

Lanes have `/theorem`, `/theorem-ext4` and `/theorem-xfs` in their scripts. When leanperf's
lanes move (step 4), three bind mounts give those paths: `/mnt/nvme/theorem` on `/theorem`,
`/mnt/nvme/theorem-ext4` on `/theorem-ext4`, `/mnt/nvme/theorem-xfs` on `/theorem-xfs`, as
systemd mount units ordered after `gce-disks.service` and before `user@1000.service`.

### What the copy leaves out

- Logins: `~/.config/gcloud/{application_default_credentials.json,legacy_credentials,credentialed_accounts.db,credentials.db,access_tokens.db}`, `~/.azure`, `~/.config/gh/hosts.yml`.
- `/mnt/nvme/sami/boxid` (an Azure federation credential; it breaks gcloud on Google) and `/mnt/nvme/sami/session-tmp`.
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

### The puller on sami-devbox: never stop it without the reclaim

One persistent unit, `devbox-copy-pull`, runs both pull loops; start it with
`pull-unit.sh start` (it sets `MemoryHigh=16G`, so reclaim keeps its page cache and the inodes
charged to it bounded while it runs). **Never stop this unit except with `pull-unit.sh stop`**,
which writes the cgroup's own size to its `memory.reclaim` until it holds under 256 MiB, and only
then stops it. To pause the copy, stop the uploads on the old boxes, not the puller (see "Known
issues": a plain stop after about 7 TiB of extracts hung `sync` and `umount` until a reboot).
Test of 2026-11-03 (198,279 files, 11 GiB): with the reclaim first, one `inode_switch_wbs` event
in the 60 s after the stop and no hung umount.

Data a lane owns may be marked `.no-offbox` or listed in `EXCLUDE`; `upload.sh` refuses to start
without `EXCLUDE`, `exclude-filter.py` and `no-offbox`, and checks each shard with
`no-offbox --root ROOT --from LIST`.

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
