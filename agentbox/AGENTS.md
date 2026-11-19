# agentbox

One disposable Sysbox container (a box) per agent session. Here: the image (`Dockerfile`, `entrypoint.sh`, `box-ssh-config`) and the home allow-list (`mounts`). `scripts/agentbox` starts and manages boxes; `scripts/agentbox-run.ts` runs omp in one. Read [README.md](README.md) first for host setup and what a box gets and loses; this guide adds the traps.

## Commands

- `scripts/agentbox new [-e NAME[=VALUE]]… [--dir PATH]… [<repo>…] -- omp [args…]`: a box with a git clone of each `~/src/<repo>`, or the host's own `--dir` paths (shared, not cloned). `--memory`, `--cpus`, `--pids`, `--disk` (0: no image) set limits (defaults: `BOX_*` atop the script). `--no-host-writes`: only `--dir` is writable.
- `agentbox ls`; `agentbox sh <box|session-id> [cmd…]`; `agentbox grow <box|session-id> SIZE` (online, to 512g).
- `agentbox restart <box|session-id>… | --all [--now]`: relaunch omp in the same container (in a box: no arguments).
- `agentbox finish [--reason TEXT]`: in a box, end it and turn off the session's auto-restart.
- `agentbox move --plan | --self | <session-id>… | --to <machine> | --go`: move a host session into a box, or a session to another machine. `agentbox move --help` lists every form.
- `agentbox doctor` compares the host with a throwaway box; `agentbox doctor --here` checks this box.
- `agentbox rebuild` builds the image and lists boxes on older tags. Each `new` starts `agentbox collect`, which deletes boxes whose launcher died (`~/.cache/omp/agentbox/collect.log`).

## Test

- `uvx --with pytest pytest scripts/tests/test_agentbox_move.py`; the rest are `scripts/tests/test_agentbox_*.py`.
- `bun test scripts/agentbox-run.test.ts` (runner).
- A real box from your clone: `DOTFILES_DIR=<clone> <clone>/scripts/agentbox doctor` (or `new`).
- In a box, tests that need an unprivileged user namespace with its own `/proc` skip and pytest ends green; `agentbox_sandbox_skips.py` prints a red banner. Run them on the host. `bwrap --unshare-net` works.

## What a box gets of the host

- `~/src` and each symlink target in it (a new symlink widens every box), `~/.dotfiles`, `/mnt/durable/<user>`, the `mounts` entries, each named or inferred `--dir`, and a few `/mnt/nvme/<user>` paths tools need (`mount_args`, `cmd_new`). No other `/mnt/nvme`.
- An environment built from scratch (`session_env`); a caller's variable arrives only through `-e`.
- Through the relayed session bus `systemctl --user` acts on host units, while `journalctl`, `pgrep` and `/sys/fs/cgroup` answer about the box. Host loopback services in `LOOPBACK_SERVICE_PORTS` are relayed too.
- The tmux relay's guard (`scripts/agentbox-tmux-guard`) refuses `kill-server` and kills of panes the box does not own (`$RUN_BASE/<box>/tmux-guard.log`).
- Its own `~/.omp/run` (brokers are per namespace); the rest of `~/.omp` is shared.
- A shared mount breaks a tool that tidies what it cannot see in use: `git worktree prune`, `playwright install`, a sweeper testing pids in its own `/proc`.
- On the host a box's processes sit in `docker-<container id>.scope`, not the box name (`docker inspect <box>`).
- Access history: [access-audit.md](access-audit.md).

## What dies with a box

- At exit the launcher also releases the session's knives claims and removes its build cache and disk image.
- `findmnt -T <path>` naming a loop device or `overlay` means the path is the box's and dies with it.
- A lasting package goes in the `Dockerfile`, not `sudo apt`.
- Each ended box adds a line to `/mnt/durable/<user>/agentbox-exits.tsv`; the runner logs to `~/.cache/omp/agentbox/<box>.log`.

## Where box storage lives

- Trees: `/mnt/nvme/<user>/agentbox/boxes`, else `~/boxes` (`AGENTBOX_BOXES`), with the clone templates (`AGENTBOX_CLONE_TEMPLATES`) on the same filesystem (clones hardlink objects).
- Images (`<box>.img`, record `<box>.info`) and build caches: the local SSD where `/mnt/scratch` is a mount (`AGENTBOX_SCRATCH`, `AGENTBOX_DISKS`, `AGENTBOX_CACHE_ROOT`). Empty after a machine stop.
- `scripts/disk-fill-watch` resolves the same defaults: change both. `$RUN_BASE` is `/run/user/<uid>/agentbox`.

## Moves

- A move refuses while the session has a child the hang-up would end: start it with `setsid <cmd> </dev/null >>log 2>&1 &`, or pass `--accept-orphans`.
- `move --to` copies `.no-offbox` trees; only `backup-shared-state` and `omp-teleport --host` refuse them (`scripts/no-offbox`).

## Changing the box

- A box keeps its container, mounts, environment, runner and launcher copy (`AGENTBOX_SELF_COPY`); `restart` re-execs omp only. Image, `mounts`, `session_env` or runner changes need a new box (`agentbox move --self`).
- `new` runs from a private copy, since `dotfiles-publish` rewrites the live file.
- The host reads a box's session only from `agentbox-session` in its runtime dir (`omp/extensions/session-env.ts`); with none (`--no-session`, a `.no-shared-setup` profile) restart and lookup by session id fail.
- `tmux-omp-ids` and `tmux-resurrect-omp` rebuild a resume from `new`'s arguments before `--`.
- The launcher enrolls the box with the broker under its session (key dir and `renew.log`: `$RUN_BASE/<box>/agent-secrets`) and re-enrolls it after a revoke: to end its access, close the box or revoke the machine login ([agent-secrets/AGENTS.md](../agent-secrets/AGENTS.md)).
- Checkouts are plain git (jj fails on them); a repo in `CLONE_SPARSE` is sparse (`git sparse-checkout add`).
- `devbox-prune` runs by hand; a host that links its units from `~/.dotfiles/agentbox` loses the timer if they are renamed.
