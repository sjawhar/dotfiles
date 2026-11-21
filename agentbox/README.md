# agentbox

agentbox runs each agent session in its own disposable [Sysbox](https://github.com/nestybox/sysbox) container (a
"box"). One command, `scripts/agentbox new`, starts the box and the session in it.

- The box gets a git clone of its own of each repository you name, and works in the first one. The clone is
  made over the network from the `origin` URL of `~/src/<repo>`, so commits you have not pushed are not in it.
- It mounts an allow-list of your home directory (`agentbox/mounts`): omp state, the mise tool store, cloud
  configs and similar. A home path that is not on the list is writable in the box but is lost with it.
- Host services come in through relays: the session bus (so the GNOME keyring, and every tool that keeps its
  login there, work unchanged), the cloud metadata service, the Envoy API, the host's tmux server, and the
  [forward](../forward/) channels, which carry the laptop's YubiKey (PC/SC) and browser. The secretsd socket is
  bind-mounted.
  Through the relayed bus, `systemctl --user` in a box acts on the host's units, while `journalctl`, `pgrep` and
  `/sys/fs/cgroup` answer about the box: a unit `systemctl --user` reports active can show no journal and no process
  in the box.
- Root in the box is an unprivileged user on the host, so `sudo apt-get install`, an inner Docker daemon and
  nested containers work without host privileges.
- A box's `/proc` lists only the box's processes. A tool in a box that sweeps scratch by testing pids in its own
  `/proc` reads every host run as dead: never point an in-box tool's `TMPDIR` at a host run's, or the reverse.
- Each box has its own cpu, memory, pids and disk limit. When the session ends, everything in the box is deleted:
  its clones (uncommitted changes and commits no remote has too), its home, `/tmp` and its inner Docker store.
  Push what you want kept before the session ends. A box whose launcher died is deleted later by the collector
  that every `agentbox new` starts.
  `scripts/path-outlives-box <path>…` says whether a write there outlives the box.
- A box ends when its container stops, not when the launcher's Docker client ends. When dockerd restarts (an
  upgrade, a crash) with `live-restore` on, the container runs on, and the launcher attaches to it again in the
  same pane. When Docker does not answer for 5 minutes (`AGENTBOX_REATTACH_WAIT`), the launcher leaves the box
  running, removes nothing, and prints the commands that attach to it or stop it. A launcher keeps the code it
  started with: `agentbox adopt <box>` puts a box started by an older launcher under this one in its own pane,
  stopping nothing in the box.
- The box gets no route to omp's session database, so a box resumes a session only from its transcript file.

This is part of Sami's dotfiles, not a stand-alone tool. It expects a host set up by this repository's
`install.sh`. (`devbox/gce-host.sh` is for Sami's own Google Cloud devbox only: it needs its disks and must run
before `install.sh`.) The commands that test a change to a box, and the rules for changing one, are in
[AGENTS.md](AGENTS.md).

## What the host must have

| Requirement | Why | Set up by |
| --- | --- | --- |
| Ubuntu 24.04, x86-64, kernel 6.8 or later | Sysbox 0.7.1 supports it | the OS |
| Host user with uid 1000 | the session in the box runs as uid 1000 (`ubuntu`), and bind mounts keep the host's owners, so another uid likely cannot write its mounted home paths (not tested) | the OS |
| Passwordless `sudo` for `mount`/`umount` | each box's disk image is loop-mounted with `sudo -n mount -o loop`; without it, start boxes with `--disk 0` (no disk limit), and run `doctor` with `AGENTBOX_DISK=0` | your sudoers |
| Tailscale, joined to your tailnet | boxes resolve names through `100.100.100.100` first (then `1.1.1.1`) | Tailscale's own install; `installers/tailscale.sh` works only on Sami's devbox |
| Docker Engine, and your user in the `docker` group | runs the boxes | `installers/docker.sh`; log in again after it adds you to the group |
| Sysbox CE 0.7.1, registered as the Docker runtime `sysbox-runc` | every box runs under it | `installers/sysbox.sh` |
| The dotfiles at `~/.dotfiles` (boxes always mount that path) | the launcher reads the image source, the mounts list and `agent-secrets/` from there | `git clone https://github.com/sjawhar/dotfiles ~/.dotfiles`, then `~/.dotfiles/install.sh` |
| Repository checkouts under `~/src` (or `AGENTBOX_SRC`) | `agentbox new <repo>` reads the `origin` URL of `~/src/<repo>` and clones that remote; a checkout with no `origin` is refused | you |
| A systemd user manager, with lingering on | the relays run as transient user units (`systemd-run --user`) | `loginctl enable-linger $USER` |
| A session bus at `/run/user/$UID/bus` | relayed into the box for the keyring | a login session or lingering |
| secretsd listening on `/run/user/$UID/secretsd.sock` | `agentbox new` stops without it | `installers/secretsd.sh` (it skips when mise has no `secretsd` binary) |
| `git`, `socat`, `jq`, `python3`, `ss` (iproute2) | clones, relays and launcher | `sudo apt-get install -y git socat jq python3 iproute2` |
| The `agent-secrets` helper with a live launcher credential, or a `gh-app.<profile>.key-secret` in `~/.config/git/config` | git in the box, and the clone `agentbox new` makes, get GitHub tokens from `shims/gh-app-token`; with neither, every github.com credential request fails, so private repositories cannot be cloned | `installers/agent-secrets.sh`, then `scripts/agent-secrets-login <email>` once per machine; `shims/gh-app-token --help` for `key-secret` |
| Optional: `forward serve` running | YubiKey and browser access from the box | `forward/` |

### Install Sysbox

Sysbox's own guide is [Sysbox CE install for Ubuntu](https://github.com/nestybox/sysbox/blob/master/docs/user-guide/install-package.md).
Install Docker first: the package registers its runtime in `/etc/docker/daemon.json` and restarts Docker.
That restart stops running containers. On a machine with this repository, run:

```bash
sudo bash ~/.dotfiles/installers/sysbox.sh
```

On any other machine, the same steps by hand (version and sha256 from the
[v0.7.1 release](https://github.com/nestybox/sysbox/releases/tag/v0.7.1)):

```bash
curl -fsSLO https://github.com/nestybox/sysbox/releases/download/v0.7.1/sysbox-ce_0.7.1.linux_amd64.deb &&
echo "9d6d5484f980d0a17f86c492c1262015c2afb66280bdb97215b79fde6a0261c5  sysbox-ce_0.7.1.linux_amd64.deb" | sha256sum -c - &&
if sysbox_state="$(dpkg-query -W -f='${db:Status-Want} ${db:Status-Status}' sysbox-ce 2>/dev/null)" &&
   { [ "${sysbox_state##* }" != not-installed ] || [ "${sysbox_state%% *}" = hold ]; }; then
  echo "dpkg already knows sysbox-ce: $(dpkg-query -W -f='${Status} ${Version}' sysbox-ce | sed 's/ $//'). These steps are for a machine where dpkg does not know sysbox-ce and nothing holds it; run 'sudo bash ~/.dotfiles/installers/sysbox.sh' from a dotfiles checkout instead." >&2
  false
fi &&
sudo apt-get satisfy -y --no-install-recommends "$(dpkg-deb --field sysbox-ce_0.7.1.linux_amd64.deb Pre-Depends)" "$(dpkg-deb --field sysbox-ce_0.7.1.linux_amd64.deb Depends)" &&
sudo dpkg -i ./sysbox-ce_0.7.1.linux_amd64.deb
```

Both ways install the package's dependencies with apt and the checked file itself with `dpkg -i`:
apt given the `.deb` file can install a configured source's copy of the same version instead
([Debian bug #1058934](https://bugs.debian.org/1058934)). The 0.7.1 file has both a `Pre-Depends`
and a `Depends` field; for a file that lacks one, leave that argument out, because `apt-get satisfy`
refuses an empty one. These steps are for a machine where dpkg does not know sysbox-ce and nothing
holds it. The block after the `sha256sum` check reads dpkg's want and status in one `dpkg-query`
call, and prints them and stops, installing nothing, in two cases: dpkg knows sysbox-ce in a state
other than `not-installed` (installed, unpacked, half-configured, or configuration files only), or
the package is on hold, which includes a hold with nothing installed (`hold ok not-installed`). Run
`installers/sysbox.sh` there, which handles those states.

You do not edit Docker's configuration by hand, and `sysbox-runc` need not be the default runtime:
`scripts/agentbox` passes `--runtime sysbox-runc` itself. Check it:

```bash
sysbox-runc --version
docker info --format '{{json .Runtimes}}' | grep -q sysbox-runc && echo "sysbox-runc registered"
```

### Check the host

```bash
lsb_release -ds; uname -r                       # Ubuntu 24.04, kernel 6.8 or later
docker version --format '{{.Server.Version}}'
test -S /run/user/$(id -u)/bus && echo "session bus ok"
test -S /run/user/$(id -u)/secretsd.sock && echo "secretsd ok"
systemctl --user is-system-running
~/.dotfiles/scripts/agentbox doctor             # every check, host side and box side
```

`agentbox doctor` checks the runtime, the image, `~/src`, the session bus, the box network, the relays and
the secrets helper, then starts a throwaway box and checks it from inside. Run it on the host, not in a box.

## Build the image

You need not build by hand: `agentbox new` builds the image the first time. The tag is a hash of
`Dockerfile`, `entrypoint.sh`, `box-ssh-config`, and `agentbox_sandbox_skips.py`, so an edit makes a new image on
the next launch. To build now, and list the running boxes that use an older image:

```bash
~/.dotfiles/scripts/agentbox rebuild
```

It runs `docker buildx build --builder default --load -t agentbox:<hash> ~/.dotfiles/agentbox`.

The image includes `/usr/bin/mksquashfs` from `squashfs-tools`.
Internal Docker readiness checks use the inner Unix socket without changing the selected user context.

`scripts/docker-registry-auth-check REGISTRY configured` checks the effective credential route.
Use `retrieve` instead to check native `get` or inline credentials. Neither mode logs in, writes Docker config,
or prints credential values.

## Start and manage boxes

```bash
# A session in a fresh box with a clone of ~/src/my-repo:
~/.dotfiles/scripts/agentbox new my-repo -- omp

# Work in an existing host directory instead of a clone (shared with the host, not removed at exit):
~/.dotfiles/scripts/agentbox new --dir ~/src/some-scratch-tree -- omp

# Change limits, set variables (a bare NAME passes your value):
~/.dotfiles/scripts/agentbox new -e MY_VAR=1 --memory 32g --cpus 4 --disk 64g my-repo -- omp

~/.dotfiles/scripts/agentbox ls                       # running boxes, then box trees with no box running
~/.dotfiles/scripts/agentbox sh <box|session-id>      # a shell in a running box
~/.dotfiles/scripts/agentbox grow <box|session-id> 256g # grow the home filesystem online, without restarting
~/.dotfiles/scripts/agentbox restart <box|session-id> # ask the session to relaunch omp in place at a safe point;
                                                      # add --now to relaunch at once
~/.dotfiles/scripts/agentbox move --plan              # which host sessions could move into a box
```

`scripts/agentbox` with no argument prints its usage (exit 2); the usage stops part way, so read the comment block at the top of `scripts/agentbox`
and `scripts/agentbox move --help` for the rest (`move <session-id>… | --all`, `move --to <machine>`, `doctor --here`).
Memory defaults to one twentieth of host memory, with a 24 GiB minimum. CPU defaults to 8 cores.
The sparse disk defaults to 128 GiB on sami-devbox and 32 GiB on other hosts.
Creating an image requires at least 500 GiB free on its host filesystem. Sparse capacity reserves no space.
`grow` runs on the host. It extends the image, refreshes its loop device, and resizes ext4.
It accepts sizes up to 512 GiB and refuses to shrink a filesystem.
Under the CPU quota, wall time is not cost: work that wants more cores waits out the rest of each period.
Before you call work in a box slow, read its CPU time and `/sys/fs/cgroup/cpu.stat` (`nr_throttled`, `throttled_usec`).
Before you ask for more CPU, list your own processes, finished subagents' leftovers included (`ps` twice: supervisors respawn), and compare 30-second `cpu.stat` deltas; stop a runner before its children, and check that each one stopped.
Then raise one box (`agentbox new --cpus N`), or run the parallel work on the host, as a transient scope over ssh.

## What it assumes about the host

- **Where boxes live.** `<boxes>` is `/mnt/nvme/<user>/agentbox/boxes` when `/mnt/nvme/<user>` exists, else
  `~/boxes`. Each box's disk limit is a sparse ext4 image loop-mounted at `<boxes>/<box>`; it holds the clones,
  the box's `$HOME` and its `/tmp`.
- **Build caches.** Each box uses `/mnt/nvme/<user>/box-cache/<box>` on hosts with that disk.
  Other hosts use `~/.cache/box-cache/<box>`. `AGENTBOX_CACHE_ROOT` changes the parent on the launcher.
  The launcher creates and binds the directory read-write before starting the box,
  and removes it once docker confirms the box's container is gone.
  `XDG_CACHE_HOME`, `GOCACHE`, `GOMODCACHE`, `npm_config_cache`, `BUN_INSTALL_CACHE_DIR`, `UV_CACHE_DIR`,
  and `PIP_CACHE_DIR` point there. Cargo's registry and git downloads use nested mounts from that directory.
  Cargo binaries and configuration keep their usual home. Project-local `target/` directories stay in place.
  The launcher does not set a shared `CARGO_TARGET_DIR`.
- **Disk alerts.** The existing `disk-fill-watch` timer checks each running box's loop filesystem.
  At 90% used, it alerts the box's session and the SRE, with the largest home directories and `/tmp`.
  It alerts once per crossing and rearms below 85% used. Failed deliveries are retried on the next timer run.
  Each reading includes allocated image bytes, box-cache bytes, and their total. Sparse capacity is not counted.
  Custom `AGENTBOX_DISKS` or `AGENTBOX_CACHE_ROOT` values must also reach the watch to measure those directories.
  `disk-fill-watch --dry-run --print-alert` prints alerts without sending them.
- **Mounts.** A box gets `~/src`, `~/.dotfiles`, and the shared home paths in `mounts`, all at their own paths.
  Shared configuration and SSH state are writable. `/mnt/durable/<host-user>` is bound when present;
  other host paths (trees under `/mnt/nvme/<host-user>`) only through a named or inferred `--dir`. The box keeps its own home and `/tmp`.
  Docker state is a complete host snapshot in that private home. A later host Docker login needs a new box.
  A shared home path that is a link into `~/.dotfiles` (`~/.gitconfig`) is the same link in the box, so the box
  reads the live file: each move of the checkout and each `git config --global` replaces that file.
  Own-host GitHub and Azure configuration is visible. App/machine defaults and existing login approvals stay unchanged.
- **The user.** The session runs as `ubuntu` (uid 1000). To host daemons a box process is a subordinate uid,
  not yours, which is why the session bus, tmux and the metadata service reach the box through relays.
- **Metadata service.** A relay on the box network's gateway (port 8169) reaches `169.254.169.254`. On a Google
  Cloud VM (`scripts/on-google-vm`) it is `scripts/agentbox-gce-metadata-relay`, which rewrites the Host header,
  and the box gets `GCE_METADATA_HOST`, `GCE_METADATA_ROOT` and `GCE_METADATA_IP`, so gcloud and Google client
  libraries act as the VM's service account, or as the account `~/.config/agent-identity/account` names, whose
  tokens the relay mints (`installers/agent-identity.sh`). Elsewhere it is a plain relay for the AWS metadata
  service (`AWS_EC2_METADATA_SERVICE_ENDPOINT`). AgentBox adds no metadata firewall to nested workloads.
- **Envoy.** The host API is relayed into the box. `ENVOY_MACHINE_ID` is the physical host's short name,
  not the container name. Profile connection settings still come from `envoy.json` without launcher overrides.
  See [the access audit](access-audit.md) for the restriction history and authorization.
- **Sandbox namespaces.** A setuid network-namespace sandbox runs in a box. A sandbox that needs another user
  namespace with its own `/proc` cannot run under this Sysbox version. Such tests report their skips.

Running boxes are not restarted for these changes. Their cache environment and mounts change at their next re-box.
`agentbox restart` restarts only omp and does not replace the container's environment or mounts.
