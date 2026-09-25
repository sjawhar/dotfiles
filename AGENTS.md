# Dotfiles

Personal development environment configuration. Targets Linux devcontainers/devpods (bash), with some macOS support (aerospace). Push directly to main — no PRs needed.

## Project Structure

```
.bashrc              # Shell config — sourced by ~/.bashrc, has non-interactive + interactive sections
.gitconfig           # Git config — symlinked to ~/.gitconfig
.jjconfig.toml       # Shared jj config — loaded via JJ_CONFIG env var
jj-allowed-signers   # Signing keys trusted for local verification (both identities, all machines)
.tmux.conf           # Tmux config
.claude/CLAUDE.md    # User-level Claude/OpenCode instructions — symlinked to ~/.claude/CLAUDE.md
starship.toml        # Starship prompt config
aerospace.toml       # macOS window manager (AeroSpace)
mise.toml            # Tool version manager — pinned versions for all CLI tools
opencode/            # OpenCode config, OMO profiles, and plugins
omp/                 # oh-my-pi config, agents, and plugin pins (binary is mise-managed; wrapper in shims/)
opencode/opencode.json       # Main OpenCode config (models, plugins, commands, permissions)
opencode/oh-my-opencode.*.json # OMO profiles (switchable via `omo` shell function)
opencode/plugins/    # OpenCode plugin scripts (jj-snapshot, etc.)
nvim/init.lua        # Neovim config (single file)
install.sh           # Main installer — runs all installers/* in order
installers/          # Per-tool install scripts (shell.sh, mise.sh, docker.sh, jj.sh, tmux.sh, nvim.sh, claude.sh, opencode.sh)
installers/lib.sh    # Shared helpers: ensure_link, ensure_clone, ensure_command, ensure_json
forward/             # Browser-forwarding policy plus devbox serve and laptop daemon user units
whatsapp/            # WhatsApp MCP daemon: wrapper + laptop user unit (holds the paired session)
bin/                 # Standalone binaries (mise, bun, opencode, kubectl)
shims/               # PATH-priority wrappers (gh, gh-app-token, gcloud, gws, google-user-token, aws-cp, omp, tmux, xdg-open, pyright, basedpyright)
scripts/             # Utility scripts (git-identity, ephemeral-monitor, etc.)
completions.d/       # Auto-generated shell completions (jj, gh)
agentbox/            # Opt-in agent box: image + home allow-list (scripts/agentbox runs sessions in it)
devpod/              # Cloud-init for a bare VM (dormant)
plugins/             # OpenCode/Claude plugins (sjawhar/ has all custom skills, agents, and commands)
vendor/              # Third-party vendored content
docs/                # Documentation and plans
```

## Subdirectory Guides

Each major subdirectory has its own AGENTS.md with details and conventions:

| Directory | What it's for |
|-----------|---------------|
| `envoy/` | Agent messaging/notification service (GitHub + Slack webhooks) |
| `opencode/` | OpenCode config, OMO profiles, plugin scripts |
| `omp/` | oh-my-pi config, agents, extensions, GitHub-pinned plugin tree |
| `plugins/` | Custom skills, agents, and commands (`sjawhar/`) |
| `installers/` | Per-tool install scripts run by `install.sh` |
| `scripts/` | Standalone utility scripts |
| `agentbox/` | Opt-in agent box: the Sysbox image `scripts/agentbox` runs a session in, and the home allow-list (`mounts`) |
| `devpod/` | Bare-VM cloud-init (dormant) |

## How Install Works

`install.sh` sources each `installers/*.sh` in order. Installers use helpers from `installers/lib.sh`:
- **`ensure_link`** — symlinks config files to their expected locations
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
- **Shims wrap binaries** with extra logic (e.g., the gh shim handles auth token sourcing, and in an agent session — omp, agentbox, or anything launched with the gh-app routing include — it routes every call to the repo owner's GitHub App; an owner with no App installation (METR, can1357, every upstream we fork) gets Sami's upstream token from the secrets store (`gh-app.agent.fallback-secret`, `GH_PUBLIC_REPO_PAT`); and it refuses the user's own login, so an agent never reaches the keyring). Wrappers that launch agent harnesses (`scripts/oc`, `scripts/agentbox`, `shims/omp`) must set up the same session environment — gh-app `GIT_CONFIG_*` routing, shims-first `PATH` — or sessions silently act as the user on GitHub.
- **Agent boxes are opt-in.** `scripts/agentbox new <repo> omp [omp args…]` runs one Sysbox container per agent session from `agentbox/Dockerfile`, in a jj workspace of the canonical checkout `~/src/<repo>` at `~/boxes/<box>/<repo>`; at exit it releases the session's knives claims, snapshots, forgets the workspace and removes the directory. omp runs under `scripts/agentbox-run.ts`, so `agentbox restart <box|--all>` relaunches it in the same container on the current `mise.toml` pin and plugin tree (`/tmp` and the workspace stay): the session gets an Envoy notice and runs `agentbox restart` when it is at a safe point, or `--now` hangs it up at once. The box mounts an allow-list of the home directory (`agentbox/mounts`: checkouts, omp state, dotfiles, mise store, `~/.aws`, `~/.kube`, `~/.pulumi`, tool configs, the login state of CLIs boxes need — fleetctl, sentry, depot, pup — the jj-signing pair) — no GitHub login, ssh keys or docker login, so a session pushes only as its repo's GitHub App; a new tool is one line in that file. A home path outside the list is silently writable into the box's overlay and lost with it (`agentbox/AGENTS.md`), so state that must outlive the box goes under a listed path. Host services arrive through socat relays (`ensure_forwarders`) where Sysbox's uid mapping makes a uid-checking daemon refuse a box: the session bus (so the keyring and every tool logged into it — hawk, pup, gws — work unchanged), the EC2 metadata service, and the Envoy API. secretsd's socket is bind-mounted instead: it serves only descendants of the process that registered the session, and a forking relay makes every connection a different, short-lived peer. `agentbox doctor` checks both sides. Details: `agentbox/AGENTS.md`.
- **Config files are symlinked** from this repo to their expected locations, not copied
- **Everything committed is portable.** No committed file may contain an absolute path, and committed symlinks may only point inside this repo with relative targets. Per-machine links (skill farms, checkout-backed paths) are created at install time by installers or `scripts/omp-sync-*`, never committed.
- **My own software installs from GitHub, pinned** — `opencode.json` plugin entries, `omp/plugins/package.json`, `mise.toml` all reference `github:sjawhar/...` at a tag or SHA. Installing from a local file path or checkout symlink is for prototyping only and never lands.
- **Placement is fixed**: `shims/` for PATH-priority wrappers, `scripts/` for utilities, `installers/` for setup, `bin/` for standalone binaries (gitignored — nothing hand-written goes there), config dirs for config only. Read the sibling files in a directory before adding to it.
- **Consolidate commits before pushing** — batch a session's related changes into one described commit per topic. Do not push per-step or per-file; a work session should land on main as 1-2 coherent commits, not a trail of fragments.
- **`main` is shared by every live session; fetch before you set the bookmark.** `jj git push` moves a bookmark sideways without protest, and a `bookmark set main -r @-` from a working copy whose `main` is stale silently drops every commit another session pushed in between (2026-09-20: two such pushes from one session rewrote four commits and dropped two `omp/config.yml` changes; repaired the same hour). The sequence is `jj git fetch`, then `jj rebase -d main@origin` if your commit is not already on it, then `jj bookmark set main -r @-`, then push — and if the push output says **`move sideways`**, stop: that word means you are about to replace history, not extend it. A snapshot also sweeps up whatever else is in the working copy (another session's `secrets.human.d/*.env`, for instance); read `jj status` before `describe` and split out what is not yours.

## Environment Facts

- **Personal vs. company boundary:** company infra repos must not reference `~/.dotfiles`, and the dotfiles install is not part of standard company machine provisioning.
- **Envoy** source/config lives in `envoy/` here. It receives external GitHub and Slack webhooks — hardening must preserve webhook delivery. Envoy tools exist only in Sami's own sessions; never instruct other users to use them.
- **Showing Sami devbox content:** link files as `http://localhost:12802/<abs-path>` (get one with `forward url <path>`) instead of `file:///` links, which the laptop resolves against the wrong filesystem. A web app or any other TCP port, on the devbox or inside an agent box, reaches his laptop's `localhost:<port>` while `forward port <ports>…` runs; the `using-forward` skill has the supervised invocation and its failure modes.
- **systemd user lingering must stay enabled** (`loginctl enable-linger ubuntu`): user services die when the last login session ends without it. "Lingering processes" cleanup is unrelated to systemd linger; do not disable it.
- **YubiKey PC/SC transport:** the devbox reaches the YubiKey through forward's pcsc channel (`forward-serve` ⇄ laptop `forward-daemon`), supervised by systemd on both ends. `forward doctor` shows `pcsc channel` / `pcsc socket` rows. The old SSH-tunnel guidance applies only to oryx: create its tunnel from a local terminal. Ubuntu's verified polkit boundary refuses `access_pcsc` to SSH-session processes on the laptop, but permits systemd --user processes; test it with `systemd-run --user`, never a raw SSH shell.

## Commit Signing and Identity

Commits are signed with a per-machine ssh key at `~/.ssh/jj-signing`, registered on GitHub as a *signing* key for each machine. jj and git each need their own configuration — git knows nothing about jj's — so the setup is mirrored in two versioned files:

- **jj** — `.jjconfig.toml` `[signing]`: `behavior = "own"`, `key`, `allowed-signers`.
- **git** — `.gitconfig`: `gpg.format = ssh`, `allowedSignersFile`, `user.signingkey`, `commit.gpgsign`, `tag.gpgsign`.

Both are plain versioned config, symlinked into place — no install step. The key path is identical on every machine, so there is nothing machine-specific to factor out. A machine missing the key fails to commit rather than committing unsigned, in both tools; that is the desired failure, since an unsigned commit under vigilant mode looks like impersonation.

Git needs configuring even though day-to-day work is all jj: agent worktrees under `~/.cache`, scripts, and throwaway clones commit with plain `git commit`, and those were the commits showing up unsigned.

### Two identities

`sami@trajectorylabs.net` is the default for both tools. `sami@thecybermonk.com` is opt-in per repo. `jj-allowed-signers` lists both as principals on every key, so a commit verifies under either one.

Within a repo the two tools must **agree**. jj's `behavior = "own"` signs a commit only when its author email matches jj's configured `user.email`, and on mismatch it *drops* the signature when rewriting rather than preserving it. That is why the defaults are aligned rather than left to differ per tool: a git-authored commit under one identity would lose its signature the first time jj rewrote it under the other — which is what happens when an agent's commit gets rebased into a jj repo.

`scripts/git-identity` is what keeps them in sync — it sets both tools at once and re-authors the working-copy change, since config alone only affects future commits:

```bash
git-identity        # show the effective identity for both tools, flag a mismatch
git-identity tl     # sami@trajectorylabs.net for this repo
git-identity cm     # sami@thecybermonk.com for this repo
```

### Setting up a new machine

Two manual steps, the same ones jj already needed:

1. `ssh-keygen -t ed25519 -N "" -C "jj-signing-$(hostname -s)" -f ~/.ssh/jj-signing`
2. Add the pubkey to `jj-allowed-signers` under both identities, then register it at <https://github.com/settings/ssh/new> as a **Signing Key** — the first is needed for local verification, the second for GitHub to show Verified.

Both emails also have to be *verified* on the GitHub account — an unverified committer email shows Unverified even with a good signature. GitHub vigilant mode makes any unsigned commit claiming these identities visibly Unverified, which is the intended tripwire.
