# Dotfiles

Personal development environment configuration. Targets Linux devcontainers/devpods (bash), with some macOS support (aerospace). Push directly to main — no PRs needed.

## Project Structure

```
.bashrc              # Shell config — sourced by ~/.bashrc, has non-interactive + interactive sections
.gitconfig           # Git config — symlinked to ~/.gitconfig
.jjconfig.toml       # Shared jj config — loaded via JJ_CONFIG env var
jj-allowed-signers   # Signing keys trusted for local verification (every identity, all machines)
.tmux.conf           # Tmux config
.claude/CLAUDE.md    # User-level agent instructions — symlinked to ~/.claude/CLAUDE.md
starship.toml        # Starship prompt config
aerospace.toml       # macOS window manager (AeroSpace)
mise.toml            # Tool version manager — pinned versions for all CLI tools
omp/                 # oh-my-pi config, agents, and plugin pins (binary is mise-managed; wrapper in shims/)
nvim/init.lua        # Neovim config (single file)
install.sh           # Main installer — runs the installers it lists, in order
installers/          # Per-tool install scripts (shell.sh, mise.sh, docker.sh, jj.sh, tmux.sh, nvim.sh, claude.sh, omp.sh)
installers/lib.sh    # Shared helpers: ensure_link, ensure_clone, ensure_command, ensure_json
forward/             # Browser-forwarding policy plus devbox serve and laptop daemon user units
whatsapp/            # WhatsApp MCP daemon: wrapper + laptop user unit (holds the paired session)
chief-dashboard/     # Sami's streams dashboard (run by hand) and the dispatch-check / lead-times commands
gws/                 # gws-accounts account registry (accounts.json), linked to ~/.config/gws/
bin/                 # Standalone binaries (mise, bun, kubectl)
shims/               # PATH-priority wrappers (gh, gh-app-token, gcloud, gws, google-user-token, aws-cp, az, git, jj, knives, omp, claude, codex, tmux, systemd-run, xdg-open, pyright, basedpyright, secrets, ssh-keygen, bq, dispatch, ctx, pulumi) and the one data file person-names, which the push guard (scripts/person-branch-guard) reads
scripts/             # Utility scripts (git-identity, tmux-attention, ephemeral-monitor, etc.)
completions.d/       # Auto-generated shell completions (jj, gh)
agentbox/            # Opt-in agent box: image + home allow-list (scripts/agentbox runs sessions in it)
agent-secrets/       # Host helper of the secrets broker: root install.sh installs it where ~/.config/agent-secrets/env.local sets AGENT_SECRETS_URL, and devbox/install.sh on the devbox
devpod/              # Cloud-init for a bare VM (dormant)
plugins/             # Claude/omp plugins (sjawhar/ has all custom skills, agents, and commands)
vendor/              # Third-party vendored content
docs/                # Documentation and plans
```

## Subdirectory Guides

Each major subdirectory has its own AGENTS.md with details and conventions:

| Directory | What it's for |
|-----------|---------------|
| `envoy/` | Agent messaging/notification service (GitHub + Slack webhooks) |
| `omp/` | oh-my-pi config, agents, extensions, GitHub-pinned plugin tree |
| `plugins/` | Custom skills, agents, and commands (`sjawhar/`) |
| `installers/` | Per-tool install scripts run by `install.sh` |
| `scripts/` | Standalone utility scripts |
| `shims/` | PATH-priority wrappers: what each adds, and the rules for adding to one |
| `agentbox/` | Opt-in agent box: the Sysbox image `scripts/agentbox` runs a session in, and the home allow-list (`mounts`) |
| `agent-secrets/` | The secrets broker's host helper (AGENTC-393): its unit, wrapper and `broker.env`; how host sessions and boxes get a broker identity |
| `chief-dashboard/` | Sami's streams dashboard and its two commands; its guide is its own `README.md` |
| `devpod/` | Bare-VM cloud-init (dormant) |

## How Install Works

`install.sh` sources the installers it lists, in order, and runs `installers/agent-secrets-local.sh` as a command; a file in `installers/` that it does not list does not run. Installers use helpers from `installers/lib.sh`:
- **`ensure_link`** — symlinks config files to their expected locations; a plain file there is replaced only when identical to the dotfiles copy, and anything else (a differing file, a real directory) fails the install so an app's saved setting is reconciled, not overwritten
- **`ensure_clone`** — shallow-clones git repos (e.g., tmux plugins)
- **`ensure_command`** — installs a binary if not on PATH
- **`ensure_json`** — idempotently patches JSON config files via jq

Shell integration works by prepending a source line to `~/.bashrc` that loads `.dotfiles/.bashrc`.

## How to Add a New Tool

1. Pin the version in `mise.toml` (if it's a mise-managed tool)
2. Create `installers/<tool>.sh` if it needs setup beyond mise (symlinks, config patching)
3. Source the new installer from `install.sh`
4. Add shell aliases/functions to `.bashrc` (in the appropriate section — non-interactive vs interactive)
5. Add completions generation to `install.sh` if the tool supports it

## Key Conventions

- **All tool versions pinned** in `mise.toml` — no floating versions. My own repos must also be listed in `minimum_release_age_excludes` there, or `latest` silently refuses to resolve their releases.
- **Idempotent installers** — running `install.sh` twice is safe
- **Shell config has two zones**: non-interactive (PATH, env vars, mise) above the `[[ $- == *i* ]] || return 0` guard, interactive (aliases, completions, prompts) below it
- **Shims wrap binaries** (`shims/`; each one's behavior and the rules for changing one are in `shims/AGENTS.md`). Wrappers that launch agent harnesses (`scripts/cld`, `scripts/agentbox`, `shims/omp`) must set up the same session environment — gh-app `GIT_CONFIG_*` routing, shims-first `PATH`, the agent `GH_CONFIG_DIR` (`scripts/gh-agent-config`), the machine's Google ADC as `GOOGLE_APPLICATION_CREDENTIALS` (`box_credential_config.json`, `installers/gcloud-box.sh`; on a Google VM no file, and the metadata server instead, which a box reaches through its relay with `GCE_METADATA_*` set), the secrets-broker identity — or sessions silently act as the user on GitHub or Google Cloud. The two host launchers get all of it from one place, `scripts/agent-session`, which they exec their agent through; `scripts/agentbox` builds a box's (`agentbox/AGENTS.md`). `mise.toml` puts `shims/` first in every PATH mise builds, since a mise shim (omp's Python eval kernel runs through mise's python3) otherwise puts mise's tool dirs, and mise's own gh, ahead of it.
- **Agent boxes segregate sessions, not host access.** `scripts/agentbox new [-e NAME[=VALUE]]… [--memory SIZE] [--cpus N] [--disk SIZE] [--dir PATH]… [<repo>…] -- omp [omp args…]` runs one Sysbox container per session. Named repositories get private plain-git clones from their canonical `~/src/<repo>` origins. A named `--dir` uses the existing host directory without cloning. Box directories live under `/mnt/nvme/<host-user>/agentbox/boxes`, or `~/boxes` without that disk. The private home, `/tmp`, and clones share the sparse disk image's limit. CPU, memory, and pids limits remain. Shared home state in `agentbox/mounts`, including configuration and SSH, is writable. `~/src` (with its symlink targets and borrowed object stores) and `/mnt/durable/<host-user>` are in every box as before; any other host path, such as a tree under `/mnt/nvme/<host-user>`, reaches a box only through a named or inferred `--dir`, so agent clutter there stays visible and gets cleaned up (Sami, dispatch://INFRA-702/comment/11d4bf79-4369-4beb-8691-ec688cee53ab). Docker state is a complete host snapshot in the private home, so a later host Docker login needs a new box. Own-host GitHub and Azure configuration is shared writable. Repository GitHub App defaults, Azure machine defaults, and existing per-task login approvals are unchanged. A home path outside the shared-state list belongs to the box. The entrypoint gives the session user only private home directories, pruning exact host mountpoints. Host services retain their session-bus, tmux, metadata, Envoy, and forward relays - the tmux one through a per-box guard that refuses `kill-server` and any kill aimed at a pane the box does not own, and passes every other command, `send-keys` into any pane included, unchanged (dispatch://INFRA-1192); secretsd's socket remains directly mounted. Profile connection settings and credentials come from `envoy.json`. The identity exception is `ENVOY_MACHINE_ID`, set from the short `AGENTBOX_HOST` to group boxes under their physical host. `agentbox restart` relaunches omp in the same container; changed mounts or identity require a new box. Nothing here restarts running sessions. On exit, knives claims are released and unpushed clones are retained for their owner. `agentbox doctor` compares host access with a new box. Details and restriction authorization: `agentbox/AGENTS.md` and `agentbox/access-audit.md`. A box's own storage - its sparse disk image and its build cache, both of which die with the box - lives on the machine's LOCAL SSD where it has one: `/mnt/scratch/<user>/agentbox/disks/<box>.img` and `/mnt/scratch/<user>/box-cache/<box>` on sami-devbox, against `/mnt/nvme` where the box trees, the clone templates and everything durable stay (`AGENTBOX_DISKS`, `AGENTBOX_CACHE_ROOT`, `AGENTBOX_SCRATCH`; a machine with no such mount keeps the old paths). A local SSD is empty after a machine stop, so nothing durable may be put there. The cache is removed when docker confirms the box's container is gone; `agentbox grow <box|session-id> <size>` grows a running box's disk online; creating a box image needs 500 GiB free on its host filesystem (`agentbox/AGENTS.md`).
- **A session whose work is not a checkout gets `--dir` instead of a repo.** `agentbox new --dir ~/src/bet-grade --dir /mnt/nvme/sami/review-wt -- omp` gives the box those existing host directories, read-write at their own paths, and works in the first; `<repo>` becomes optional. On 2026-11-01, 50 of this host's 82 live sessions worked in a directory that is not a repository (a lane's scratch root, a lead's coordination root, a tree of worktrees) and could not be boxed at all. A `--dir` is the host's own directory: shared, never cloned, left as it was when the box ends, and — unlike the clones, `$HOME` and `/tmp` — outside the box's disk image and its disk limit. `agentbox/AGENTS.md` has the rest, including why such a box has no session-id fallback.
- **A session already running on the host moves into a box with `agentbox move`.** `agentbox move --plan` lists every live top-level omp on this machine with its pane, its full session id, its working directory, the box it would get and **every path that box gets with the reason it was chosen**, and separates the ones a person must decide: a headless omp with no pane, one already boxed, one in a grading sandbox, a subagent of another omp. `agentbox move --all` then asks each session over Envoy to run `agentbox move --self` at its own safe point, without stopping or delaying any work for it; that command hangs its omp up gracefully and respawns its pane into `agentbox new --dir <path>… -- omp <its own launch flags> --resume <transcript>`, so the session comes back with its id, its transcript and the same tree, uncommitted work included. Those paths are **inferred from the session's own processes** — the cwd of the omp and of every live descendant, and the directory of every file one of them holds open for writing — because the working directory is a poor witness of where a lane works, and a path a box is not given is not absent inside it: it is silently writable there and lost with the box. Each is resolved to its git working tree where it is in one, bounded at the first directory below a root that holds many sessions' trees, and left out when the box has it already (`~/src`, the dotfiles, the home allow-list — so a worktree under `~/src/<repo>/.worktrees/...` needs no `--dir`). A session adds what the inference missed with `agentbox move --self --dir PATH`. A move clones nothing — `~/src` is in every box already — so each box costs only its own sparse disk image (261 MiB at the 32 GiB default), and it refuses to start one that would leave `/mnt/nvme` under a 20 GiB reserve (10 GiB on the root disk) unless `--force-floor` says the person running the window decided otherwise; `scripts/disk-fill-watch`'s alert floors are not refusal thresholds. `agentbox/AGENTS.md` has the refusals and the measurements. `agentbox move --to <machine>` moves a session, with every session that shares a working tree with it, to another machine over Tailscale SSH and resumes each there in a box with the same id; a marker left in each session's directory makes `scripts/agent-session` and `scripts/agentbox-run.ts` refuse to resume it here, and a failed resume there puts the whole group back (`agentbox/AGENTS.md`, dispatch://INFRA-993).
- **Config files are symlinked** from this repo to their expected locations, not copied
- **Everything committed is portable.** No committed file may contain an absolute path, and committed symlinks may only point inside this repo with relative targets. Per-machine links (skill farms, checkout-backed paths) are created at install time by installers or `scripts/omp-sync-*`, never committed.
- **My own software installs from GitHub, pinned** — `omp/plugins/package.json` and `mise.toml` reference `github:sjawhar/...` at a tag or SHA. Installing from a local file path or checkout symlink is for prototyping only and never lands.
- **Placement is fixed**: `shims/` for PATH-priority wrappers and the one data file the push guard `scripts/person-branch-guard` reads (`shims/person-names`), `scripts/` for utilities, `installers/` for setup, `bin/` for standalone binaries (gitignored — nothing hand-written goes there), config dirs for config only. Read the sibling files in a directory before adding to it.
- **Consolidate commits before pushing** — batch a session's related changes into one described commit per topic. Do not push per-step or per-file; a work session should land on main as 1-2 coherent commits, not a trail of fragments.
- **`main` is shared by every live session; push only a fast-forward.** `~/.dotfiles` is a colocated jj checkout, and its git HEAD is detached by design: jj keeps HEAD at the working copy's parent and moves the local `main` to follow `origin/main`, so `main` does not hold your commit, and `git push origin main` reports "up to date" and drops it. Edit in place, commit only your own paths (`jj commit <paths>`), then publish with `scripts/dotfiles-publish <your commit>`. It fetches and tries the rebase of your commit and everything above it (the working copy included) onto origin's main on a throwaway duplicate first. If your commit conflicts, it refuses with every file on disk as it was. If only something above it conflicts (the working copy, holding other sessions' uncommitted lines next to a change main already has), it pushes a copy of your commit alone and leaves your commit and the working copy where they are. Otherwise it moves the real commits and pushes, fetching and trying again when another session pushed first. Never run that rebase yourself (`jj rebase -s <commit> -d main@origin`) here: a rebase that conflicts writes jj's conflict markers into the live tree, which every session reads (shims/gh held them at 11:59:11-11:59:31Z on 2026-11-02, about 22 failed gh calls from 7 sessions). Never `git push origin main`, `git pull`, `git rebase`, `--force`, or a `git switch`/`git checkout` that attaches HEAD to a branch here: the next `jj util snapshot`, which omp's `hooks/post/jj-snapshot.ts` runs after every tool call that changes files, detaches HEAD again. `scripts/dotfiles-sync local` moves the whole checkout onto origin's main. Read `git status` before committing and leave out anything that is not yours.
  A publish whose rebase moved the files on disk installs the pins at exit (`mise install`, then the frozen Bun install of `omp/plugins`), and so does `scripts/dotfiles-sync` after each move.
  The one rewrite is the weekly squash: `scripts/dotfiles-squash` replaces the week's commits with one per topic and tags the old tip `pre-squash-<date>` on origin, and `scripts/dotfiles-sync --old <tag>` moves every machine's checkout onto it. A checkout holding commits built on the old main moves them with `git rebase --onto origin/main pre-squash-<date>`; a plain rebase onto the new main replays the whole old week.
- **The working tree is production on sami-rocqperf; there is no staging step.** `shims/` and the config files are read straight from this checkout by every running session and by systemd user units, so an uncommitted edit is live the moment it is saved, for everyone, not at commit or push time. Measured on 2026-10-29: a cutover saved in `shims/gh-app-token` at 09:21:36Z made `ossv-mcsl-ghtoken.service` fail its 09:27:02Z run (`no token minted`); the previous file was restored at 09:27:32Z and the 09:30 run recovered. A brief whose limits are "do not commit, do not push" therefore does not stop a change reaching production. Test a change to a shim or a script from a copy outside the checkout, and save it here only when it is ready to be live.
- **Write a shim atomically — temp file plus rename — never truncate it in place.** Because the tree is production, a writer that empties a file before filling it opens a window in which every session on the box gets an **empty executable**, and an empty bash script **exits 0 and prints nothing**. On 2026-11-07, from 07:43:19Z to about 07:46Z, `shims/git`, `shims/jj`, `shims/secrets` and `shims/tmux` were 0 bytes while a worker published into the live checkout: `git commit` reported success and did not commit, `git push` reported success and did not push, and `secrets` returned nothing. No error, no warning, exit 0 — success with no effect, which every caller reads as success. One lane noticed inside two minutes only because it reads back the commit hash after every commit and the hash was not there; 83 sessions had to be told to re-verify anything they did in the window. A rename is instant and no reader ever sees a partial file, so the whole class goes away for one line of discipline in whatever writes them.
- **Leave other people's changes in the workspace** (Sami, to every agent on the box, 2026-10-26 03:01Z, Dispatch message fa8b3bac-42ab-4418-884e-4d685a55b869). The checkout always holds uncommitted work that is not yours: keys in `secrets.env`, another session's half-done edit. Never stash it, reset it, check it out, move it aside, or make a separate worktree to get around it; let it accumulate. Commit only your own paths (`git commit -- <paths>`). A pull refused because the tree is dirty is not a reason to stash or `--autostash`.

## Environment Facts

- **Personal vs. company boundary:** company infra repos must not reference `~/.dotfiles`, and the dotfiles install is not part of standard company machine provisioning.
- **Envoy** source/config lives in `envoy/` here. It receives external GitHub and Slack webhooks — hardening must preserve webhook delivery. Envoy tools exist only in Sami's own sessions; never instruct other users to use them.
- **Showing Sami devbox content:** link files with the URL `forward url <path>` prints instead of `file:///` links, which the laptop resolves against the wrong filesystem. Never hand-write `http://localhost:12802/…`: on the laptop that address is oryx's preview through `forward-tunnel@oryx`; sami-rocqperf's previews are at `127.0.0.2:12802` and sami-devbox's are at `127.0.0.3:12802`, so a hand-written link can show another machine's copy of the path. A web app or any other TCP port, on the devbox or inside an agent box, reaches his laptop's `localhost:<port>` while `forward port <ports>…` runs; the `using-forward` skill has the supervised invocation and its failure modes.
- **systemd user lingering must stay enabled** (`loginctl enable-linger ubuntu`): user services die when the last login session ends without it. "Lingering processes" cleanup is unrelated to systemd linger; do not disable it.
- **YubiKey PC/SC transport:** the devbox reaches the YubiKey through forward's pcsc channel (`forward-serve` ⇄ laptop `forward-daemon`), supervised by systemd on both ends. `forward doctor` shows `pcsc channel` / `pcsc socket` rows. The oryx tunnel is the systemd user unit `forward-tunnel@oryx`, which polkit permits to reach pcscd. Do not create an SSH tunnel from a terminal. Ubuntu's verified polkit boundary refuses `access_pcsc` to SSH-session processes on the laptop, but permits systemd --user processes; test it with `systemd-run --user`, never a raw SSH shell.
- **Attention:** a ready omp session does not ring the bell; it queues for the tmux attention cockpit instead (`scripts/tmux-attention`, run it in a pane; `C-b a` brings the next waiting session's pane into that cell and sends the current visitor back). The queue is `~/.omp/attention/queue.jsonl`.

## Commit Signing and Identity

Commits are signed with a per-machine ssh key at `~/.ssh/jj-signing`, registered on GitHub as a *signing* key for each machine. jj and git each need their own configuration — git knows nothing about jj's — so the setup is mirrored in two versioned files:

- **jj** — `.jjconfig.toml` `[signing]`: `behavior = "own"`, `key`, `allowed-signers`.
- **git** — `.gitconfig`: `gpg.format = ssh`, `allowedSignersFile`, `user.signingkey`, `commit.gpgsign`, `tag.gpgsign`.

Both run `ssh-keygen` by name, and `shims/ssh-keygen` drops `SSH_AUTH_SOCK` for its `-Y` operations: the key is a file, signing never needs the ssh agent, and a stuck agent socket otherwise hangs every signed commit. A process without `shims/` on `PATH` runs the real `ssh-keygen`, agent included.

Both are plain versioned config, symlinked into place — no install step. The key path is identical on every machine, so there is nothing machine-specific to factor out. A machine missing the key fails to commit rather than committing unsigned, in both tools; that is the desired failure, since an unsigned commit under vigilant mode looks like impersonation.

Git needs configuring even though day-to-day work is all jj: agent worktrees under `~/.cache`, scripts, and throwaway clones commit with plain `git commit`, and those were the commits showing up unsigned.

### Three identities

`sami@trajectorylabs.net` is the default for both tools. `sami@thecybermonk.com` and `sami@theorem.dev` are opt-in per repo, except where a rule applies them:

- **sjawhar/legion** uses `sami@thecybermonk.com`. `.gitconfig` includes `cybermonk-identity.gitconfig` for every clone whose remote is sjawhar/legion, and `.jjconfig.toml` scopes it to Legion's jj checkouts by path, since jj has no remote condition.
- **Every theorem-labs repository** uses `sami@theorem.dev`. `.gitconfig` includes `theorem-identity.gitconfig` for every clone whose remote is in the theorem-labs organization, and `.jjconfig.toml` scopes it to jj checkouts under `~/Code/personal/theorem`.

`jj-allowed-signers` lists every identity as a principal on every key, so a commit verifies under any of them.

Within a repo the two tools must **agree**. jj's `behavior = "own"` signs a commit only when its author email matches jj's configured `user.email`, and on mismatch it *drops* the signature when rewriting rather than preserving it. That is why the defaults are aligned rather than left to differ per tool: a git-authored commit under one identity would lose its signature the first time jj rewrote it under the other — which is what happens when an agent's commit gets rebased into a jj repo.

`scripts/git-identity` is what keeps them in sync — it sets both tools at once and re-authors the working-copy change, since config alone only affects future commits:

```bash
git-identity        # show the effective identity for both tools, flag a mismatch
git-identity tl     # sami@trajectorylabs.net for this repo
git-identity cm     # sami@thecybermonk.com for this repo
git-identity th     # sami@theorem.dev for this repo
```

### Setting up a new machine

Two manual steps, the same ones jj already needed:

1. `ssh-keygen -t ed25519 -N "" -C "jj-signing-$(hostname -s)" -f ~/.ssh/jj-signing`
2. Add the pubkey to `jj-allowed-signers` under every identity, then register it at <https://github.com/settings/ssh/new> as a **Signing Key** — the first is needed for local verification, the second for GitHub to show Verified.

Every email also has to be *verified* on the GitHub account — an unverified committer email shows Unverified even with a good signature. GitHub vigilant mode makes any unsigned commit claiming these identities visibly Unverified, which is the intended tripwire.
