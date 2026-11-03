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
- Root in the box is an unprivileged user on the host, so `sudo apt-get install`, an inner Docker daemon and
  nested containers work without host privileges.
- Each box has its own cpu, memory, pids and disk limit. When the session ends, the box and its directory go.
  A clone with uncommitted changes, or commits that no remote has, keeps its directory, and the pane names it.

This is part of Sami's dotfiles, not a stand-alone tool. It expects a host set up by this repository's
`install.sh`. (`devbox/gce-host.sh` is for Sami's own Google Cloud devbox only: it needs its disks and must run
before `install.sh`.) The details and the reasons for each choice are in
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
| The `agent-secrets` helper with a live launcher credential, or a `gh-app.<profile>.key-secret` in `~/.config/git/config` | git in the box, and the clone `agentbox new` makes, get GitHub tokens from `shims/gh-app-token`; with neither, every github.com credential request fails, so private repositories cannot be cloned | `installers/agent-secrets.sh` (see `agent-secrets/AGENTS.md`); `shims/gh-app-token --help` for `key-secret` |
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
curl -fsSLO https://github.com/nestybox/sysbox/releases/download/v0.7.1/sysbox-ce_0.7.1.linux_amd64.deb
echo "9d6d5484f980d0a17f86c492c1262015c2afb66280bdb97215b79fde6a0261c5  sysbox-ce_0.7.1.linux_amd64.deb" | sha256sum -c -
sudo apt-get install -y ./sysbox-ce_0.7.1.linux_amd64.deb
```

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
`Dockerfile`, `entrypoint.sh` and `agentbox_sandbox_skips.py`, so an edit to any of them makes a new image on
the next launch. To build now, and list the running boxes that use an older image:

```bash
~/.dotfiles/scripts/agentbox rebuild
```

It runs `docker buildx build --builder default --load -t agentbox:<hash> ~/.dotfiles/agentbox`.

## Start and manage boxes

```bash
# A session in a fresh box with a clone of ~/src/my-repo:
~/.dotfiles/scripts/agentbox new my-repo -- omp

# Work in an existing host directory instead of a clone (shared with the host, not removed at exit):
~/.dotfiles/scripts/agentbox new --dir ~/src/some-scratch-tree -- omp

# Change limits, set variables (a bare NAME passes your value):
~/.dotfiles/scripts/agentbox new -e MY_VAR=1 --memory 32g --cpus 4 --disk 64g my-repo -- omp

~/.dotfiles/scripts/agentbox ls                       # running boxes, then box trees kept for unpushed work
~/.dotfiles/scripts/agentbox sh <box|session-id>      # a shell in a running box
~/.dotfiles/scripts/agentbox restart <box|session-id> # ask the session to relaunch omp in place at a safe point;
                                                      # add --now to relaunch at once
~/.dotfiles/scripts/agentbox move --plan              # which host sessions could move into a box
```

`scripts/agentbox` with no argument prints its usage (exit 2); the usage stops part way, so read the comment block at the top of `scripts/agentbox` and `agentbox/AGENTS.md`
for the rest (`move <session-id>… | --all`, `move --to <machine>`, `doctor --here`). Default limits are 24 GiB memory, 8 cpus and a
32 GiB disk.

## What it assumes about the host

- **Where boxes live.** `<boxes>` is `/mnt/nvme/<user>/agentbox/boxes` when `/mnt/nvme/<user>` exists, else
  `~/boxes`. Each box's disk limit is a sparse ext4 image loop-mounted at `<boxes>/<box>`; it holds the clones,
  the box's `$HOME` and its `/tmp`.
- **Mounts.** From your home directory a box gets `~/src` (every checkout), `~/.dotfiles` and the paths in
  `mounts`, each at its own path. There is no GitHub
  login, no GitHub ssh key and no docker login in a box (only the jj signing key and the Compute Engine ssh key
  are mounted from `~/.ssh`): git pushes go through the repository's GitHub App (`gh-app-routes.gitconfig`).
- **The user.** The session runs as `ubuntu` (uid 1000). To host daemons a box process is a subordinate uid,
  not yours, which is why the session bus, tmux and the metadata service reach the box through relays.
- **Metadata service.** A relay on the box network's gateway (port 8169) reaches `169.254.169.254`. On a Google
  Cloud VM (`scripts/on-google-vm`) it is `scripts/agentbox-gce-metadata-relay`, which rewrites the Host header,
  and the box gets `GCE_METADATA_HOST`, `GCE_METADATA_ROOT` and `GCE_METADATA_IP`, so gcloud and Google client
  libraries act as the VM's service account. Elsewhere it is a plain relay for the AWS metadata service
  (`AWS_EC2_METADATA_SERVICE_ENDPOINT`). Containers started inside the box cannot reach it.
- **Envoy.** The host's Envoy API on `127.0.0.1:9020` is relayed into the box.
- **Sandbox tests do not run in a box.** Sysbox handles `/proc` mounts itself, so bubblewrap cannot start.
  Such tests skip, and pytest prints a red banner naming them.
