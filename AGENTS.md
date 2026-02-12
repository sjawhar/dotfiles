# Dotfiles

Personal development environment configuration. Targets Linux devcontainers/devpods (bash), with some macOS support (aerospace). Push directly to main — no PRs needed.

## Project Structure

```
.bashrc              # Shell config — sourced by ~/.bashrc, has non-interactive + interactive sections
.gitconfig           # Git config — symlinked to ~/.gitconfig
.jjconfig.toml       # Shared jj config — loaded via JJ_CONFIG env var
.tmux.conf           # Tmux config
.claude/CLAUDE.md    # User-level Claude/OpenCode instructions — symlinked to ~/.claude/CLAUDE.md
starship.toml        # Starship prompt config
aerospace.toml       # macOS window manager (AeroSpace)
mise.toml            # Tool version manager — pinned versions for all CLI tools
opencode.json        # OpenCode config (models, plugins, commands, permissions)
oh-my-opencode.*.json # OpenCode plugin profiles (switchable via `omo` shell function)
nvim/init.lua        # Neovim config (single file)
install.sh           # Main installer — runs all installers/* in order
installers/          # Per-tool install scripts (shell.sh, mise.sh, jj.sh, tmux.sh, nvim.sh, claude.sh, opencode.sh)
installers/lib.sh    # Shared helpers: ensure_link, ensure_clone, ensure_command, ensure_json
bin/                 # Standalone binaries (mise, bun, opencode, kubectl)
shims/               # PATH-priority wrappers (gh, opencode, pyright, basedpyright)
scripts/             # Utility scripts (devenv-backup, git-credential-gh, ephemeral-monitor, etc.)
completions.d/       # Auto-generated shell completions (jj, gh)
devpod/              # DevPod container setup (Dockerfile, entrypoint, proxy config)
plugins/             # OpenCode/Claude plugins (sjawhar/ has all custom skills, agents, and commands)
vendor/              # Third-party vendored content
docs/                # Documentation and plans
```

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
