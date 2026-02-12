# Modular Install Script Design

## Problem

`install.sh` is 254 lines in a single file. Hard to navigate, hard to iterate on individual sections, and no way to run just one piece without running everything.

## Design

Split into `installers/` directory. Each script is independently runnable and idempotent. A thin orchestrator runs them all in order.

### Structure

```
install.sh              ~15 lines, sources each script in order
installers/
  lib.sh                shared helpers
  shell.sh              bashrc, gitconfig, completions
  mise.sh               mise install + tools
  jj.sh                 jj config
  tmux.sh               tmux + TPM
  nvim.sh               nvim + plugins
  claude.sh             Claude Code + skills/agents
  opencode.sh           OpenCode + plugins + config
```

### Shared Helpers (`lib.sh`)

Four helpers that cover the repeated patterns:

```bash
DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

ensure_link() { ln -sfn "$1" "$2"; }

ensure_clone() {
    local url="$1" dir="$2"
    [ -d "${dir}/.git" ] && return
    mkdir -p "$(dirname "$dir")"
    git clone --depth 1 "$url" "$dir"
}

ensure_command() {
    local name="$1" install_cmd="$2"
    command -v "$name" &>/dev/null && return
    echo "Installing ${name}..."
    eval "$install_cmd"
}

ensure_json() {
    local file="$1" check="$2" transform="$3" desc="${4:-}"
    jq -e "$check" "$file" > /dev/null 2>&1 && return 0
    [ -n "$desc" ] && echo "$desc"
    local tmp=$(mktemp)
    jq "$transform" "$file" > "$tmp" && mv "$tmp" "$file"
}
```

### Orchestrator (`install.sh`)

```bash
#!/bin/bash
set -eu
export DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Dotfiles Install ==="

source "${DOTFILES_DIR}/installers/shell.sh"
source "${DOTFILES_DIR}/installers/mise.sh" "$@"
source "${DOTFILES_DIR}/installers/jj.sh"
source "${DOTFILES_DIR}/installers/tmux.sh"
source "${DOTFILES_DIR}/installers/nvim.sh"
source "${DOTFILES_DIR}/installers/claude.sh"
source "${DOTFILES_DIR}/installers/opencode.sh"

echo "=== Done! Restart your shell or run: source ~/.bashrc ==="
```

### Installer Boilerplate

Every script starts with:

```bash
#!/bin/bash
set -eu
DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
source "${DOTFILES_DIR}/installers/lib.sh"
```

Works both sourced from `install.sh` (inherits `DOTFILES_DIR`) and run directly (computes from own location).

### Script Breakdown

**`shell.sh`** -- bashrc sourcing, gitconfig, completions

- Insert dotfiles source line at top of `~/.bashrc` (idempotent: checks with grep)
- Source `.bashrc` to get PATH/env for the rest of the install
- `ensure_link` for `.gitconfig`
- Generate completions for jj, gh into `completions.d/`

**`mise.sh`** -- tool version manager

- `ensure_link` mise config to `~/.config/mise/config.toml`
- `mise trust` the config
- `ensure_command mise` with curl installer
- `eval "$(mise activate)"` to get tools on PATH
- Handle `--skip-mise` flag (passed via `$@`)
- Install tools with retry loop (3 attempts for rate limits)

**`jj.sh`** -- version control config

- `ensure_link` for `.jjconfig.toml`
- Create `~/.config/jj/config.toml` with interactive email prompt (if TTY and file missing)

**`tmux.sh`** -- tmux + plugin manager

- `ensure_link` for `.tmux.conf`
- `ensure_clone` TPM repo to `~/.tmux/plugins/tpm`

**`nvim.sh`** -- neovim config + plugins

- `mkdir -p ~/.config/nvim`
- `ensure_link` for `nvim/init.lua`
- Headless Lazy plugin sync (if nvim available)

**`claude.sh`** -- Claude Code + skills/agents

- `ensure_command claude` with curl installer
- `ensure_clone` agent-skills vendor repo
- Clean up stale `.claude/skills` symlink if exists, ensure directory
- Symlink sjawhar skills (loop over `plugins/sjawhar/skills/*/`)
- Symlink vendor skills (notion-api)
- Symlink sjawhar agents directory

**`opencode.sh`** -- OpenCode + plugins + config (biggest script)

- `ensure_command opencode` with curl installer
- `ensure_clone` compound-engineering plugin, symlink its agents
- `ensure_clone` superpowers plugin
- `ensure_command oh-my-opencode` via npm
- Run `oh-my-opencode install` if `opencode.json` missing (interactive)
- Four `ensure_json` patches on `opencode.json`:
  - Add `opencode-antigravity-auth@beta` plugin
  - Set Opus 4.6 context limit to 1M
  - Disable autoupdate
  - Add Streamlinear MCP config
- `ensure_link` for `oh-my-opencode.json`
- Symlink opencode skills from `opencode-skills/`

### Dependency Assumptions

Each script assumes its prerequisites are already met. The orchestrator handles ordering:

1. `shell.sh` first (gets PATH set up via `.bashrc`)
2. `mise.sh` second (installs tools needed by later scripts: node, npm, jj, gh)
3. Everything else in any order (but current ordering is fine)

Running a script directly (e.g. `./installers/opencode.sh`) requires that mise and npm are already available.

### Decisions

- **No `--only` flag**: run sub-scripts directly instead.
- **No `links.sh`**: each script owns its own symlinks. Avoids splitting tool setup across files.
- **`ensure_link` uses `ln -sfn`**: the `-n` flag prevents creating links inside existing symlink-to-directory targets.
- **`ensure_json` normalizes jq patches**: all checks use `jq -e`, eliminating the repeated tmpfile boilerplate.
