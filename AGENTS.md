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
opencode/opencode.json       # Main OpenCode config (models, plugins, commands, permissions)
opencode/oh-my-opencode.*.json # OMO profiles (switchable via `omo` shell function)
opencode/plugins/    # OpenCode plugin scripts (jj-snapshot, etc.)
nvim/init.lua        # Neovim config (single file)
install.sh           # Main installer — runs all installers/* in order
installers/          # Per-tool install scripts (shell.sh, mise.sh, jj.sh, tmux.sh, nvim.sh, claude.sh, opencode.sh)
installers/lib.sh    # Shared helpers: ensure_link, ensure_clone, ensure_command, ensure_json
forward/             # Browser-forwarding policy plus devbox serve and laptop daemon user units
bin/                 # Standalone binaries (mise, bun, opencode, kubectl)
shims/               # PATH-priority wrappers (gh, opencode, pyright, basedpyright)
scripts/             # Utility scripts (git-identity, ephemeral-monitor, etc.)
completions.d/       # Auto-generated shell completions (jj, gh)
devpod/              # Remote dev machine provisioning: container image + cloud-init for a bare VM (dormant)
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
| `plugins/` | Custom skills, agents, and commands (`sjawhar/`) |
| `installers/` | Per-tool install scripts run by `install.sh` |
| `scripts/` | Standalone utility scripts |
| `devpod/` | Remote dev machine provisioning — container image and bare-VM cloud-init (dormant) |

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

- **All tool versions pinned** in `mise.toml` — no floating versions
- **Idempotent installers** — running `install.sh` twice is safe
- **Shell config has two zones**: non-interactive (PATH, env vars, mise) above the `[[ $- == *i* ]] || return 0` guard, interactive (aliases, completions, prompts) below it
- **Shims wrap binaries** with extra logic (e.g., the gh shim handles auth token sourcing)
- **Config files are symlinked** from this repo to their expected locations, not copied
- **Consolidate commits before pushing** — batch a session's related changes into one described commit per topic. Do not push per-step or per-file; a work session should land on main as 1-2 coherent commits, not a trail of fragments.

## Environment Facts

- **Personal vs. company boundary:** company infra repos must not reference `~/.dotfiles`, and the dotfiles install is not part of standard company machine provisioning.
- **Envoy** source/config lives in `envoy/` here. It receives external GitHub and Slack webhooks — hardening must preserve webhook delivery. Envoy tools exist only in Sami's own sessions; never instruct other users to use them.
- **Linking devbox files:** when the `forward` tunnel is deployed, link files as `http://localhost:12802/<abs-path>` (get one with `forward url <path>`) instead of `file:///` links, which the laptop resolves against the wrong filesystem.
- **systemd user lingering must stay enabled** (`loginctl enable-linger ubuntu`): the openchamber stack (and any other `systemctl --user` services) dies when the last login session ends without it. "Lingering processes" cleanup is unrelated to systemd linger; do not disable it. `installers/openchamber.sh` enables it idempotently.
- **YubiKey pcscd tunnel:** the `devbox-tunnel` SSH connection carries `RemoteForward 127.0.0.1:12799 /run/pcscd/pcscd.comm` for human-tier secret decryption. Ubuntu's polkit gates pcscd access to active logind sessions only. The tunnel MUST be created from a local, active terminal session on the laptop — never from a non-interactive SSH session (e.g. agent over SSH). If started over SSH, the process goes inactive and pcscd silently rejects all access (`IsClientAuthorized() Process is NOT authorized`), while TCP forwards keep working, making the failure confusing. If an agent created a stale tunnel, kill it with `ssh -O exit devbox-tunnel` and run `devbox` from a terminal. Running `devbox` also leaves behind a session-scoped reconnect supervisor, so sleep/Wi-Fi drops heal automatically until the laptop session ends. On secretsd 2.1+ the installer wires a far-end probe (`age-plugin-yubikey --list`) into the daemon, so a request through a dead tunnel is refused as `YUBIKEY_UNREACHABLE` instead of failing inside sops.

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
