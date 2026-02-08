#!/bin/bash
set -eu

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_MISE=false

for arg in "$@"; do
    case $arg in
        --skip-mise) SKIP_MISE=true ;;
    esac
done

echo "=== Dotfiles Install ==="

# Shell config - insert at TOP of ~/.bashrc (before Debian's interactive check)
# so PATH/env vars are available for non-interactive shells too
DOTFILES_SOURCE_LINE='[ ! -f "${HOME}/.dotfiles/.bashrc" ] || . "${HOME}/.dotfiles/.bashrc"'
if [ -f ~/.bashrc ]; then
    if ! grep -qF '.dotfiles/.bashrc' ~/.bashrc; then
        # Insert at top of file
        { echo "$DOTFILES_SOURCE_LINE"; echo ""; cat ~/.bashrc; } > ~/.bashrc.tmp
        mv ~/.bashrc.tmp ~/.bashrc
    fi
fi
. "${DOTFILES_DIR}/.bashrc"

# ------------------------------------------------------------------------------
# Mise (tool version manager)
# ------------------------------------------------------------------------------

mkdir -p ~/.config/mise
ln -sf "${DOTFILES_DIR}/mise.toml" ~/.config/mise/config.toml
mise trust ~/.config/mise/config.toml || echo "MISE INSTALL FAILED"

if ! command -v mise &>/dev/null
then
    echo "Installing mise..."
    curl -fsSL https://mise.run | MISE_INSTALL_PATH="${DOTFILES_DIR}/bin/mise" sh
    eval "$(mise activate "$DOTFILES_SHELL")"
fi

if [ "$SKIP_MISE" = true ]; then
    echo "Skipping mise install (--skip-mise)"
else
    echo "Installing tools via mise..."
    mise trust || echo "MISE TRUST FAILED"
    for i in {1..3}; do
        mise install && break || echo "Some tools failed to install (may be rate-limited). Run 'mise install' later."
        sleep 1
    done
fi

# Activate mise so tools (node, npm, jj, gh, etc.) are on PATH for the rest of the script
eval "$(mise activate bash)"

# ------------------------------------------------------------------------------
# Symlinks
# ------------------------------------------------------------------------------

echo "Creating symlinks..."
mkdir -p ~/.config/nvim
ln -sf "${DOTFILES_DIR}/.gitconfig" ~/.gitconfig
ln -sf "${DOTFILES_DIR}/.tmux.conf" ~/.tmux.conf
ln -sf "${DOTFILES_DIR}/nvim/init.lua" ~/.config/nvim/init.lua

# Clone vendor repositories (agent-skills, etc.)
if [ ! -d "${DOTFILES_DIR}/vendor/agent-skills/.git" ]; then
    echo "Cloning agent-skills vendor repo..."
    mkdir -p "${DOTFILES_DIR}/vendor"
    git clone --depth 1 https://github.com/intellectronica/agent-skills.git "${DOTFILES_DIR}/vendor/agent-skills"
fi

# Claude Code plugins (skills & agents from sjawhar plugin)
# NEW: Real directory with individual symlinks from all sources
[ -L "${DOTFILES_DIR}/.claude/skills" ] && rm "${DOTFILES_DIR}/.claude/skills"
mkdir -p "${DOTFILES_DIR}/.claude/skills"

# Symlink each sjawhar skill individually (flat, top-level)
if [ -d "${DOTFILES_DIR}/plugins/sjawhar/skills" ]; then
    for skill_dir in "${DOTFILES_DIR}/plugins/sjawhar/skills"/*/; do
        [ -d "$skill_dir" ] || continue
        skill_name=$(basename "$skill_dir")
        ln -sf "../../plugins/sjawhar/skills/${skill_name}" "${DOTFILES_DIR}/.claude/skills/${skill_name}"
    done
fi

# Symlink vendor skills (added by later tasks)
if [ -d "${DOTFILES_DIR}/vendor/agent-skills/skills/notion-api" ]; then
    ln -sf "../../vendor/agent-skills/skills/notion-api" "${DOTFILES_DIR}/.claude/skills/notion-api"
fi

[ -d "${DOTFILES_DIR}/.claude/agents" ] && ! [ -L "${DOTFILES_DIR}/.claude/agents" ] && rm -rf "${DOTFILES_DIR}/.claude/agents"
ln -sf "../plugins/sjawhar/agents" "${DOTFILES_DIR}/.claude/agents"

# Install nvim plugins
if command -v nvim &>/dev/null; then
    echo "Installing nvim plugins..."
    nvim --headless "+Lazy! sync" +qa || echo "NVIM PLUGIN INSTALL FAILED"
fi

# ------------------------------------------------------------------------------
# jj (version control system)
# ------------------------------------------------------------------------------

# Shared config (aliases, editor, etc.) - lower priority
ln -sf "${DOTFILES_DIR}/.jjconfig.toml" ~/.jjconfig.toml

# Machine-specific config (user.name, user.email) - higher priority, overrides shared
mkdir -p ~/.config/jj
if [ ! -f ~/.config/jj/config.toml ]; then
    if [ -t 0 ]; then
        read -rp "Email for jj/git commits: " USER_EMAIL
        cat > ~/.config/jj/config.toml <<EOF
[user]
name = "Sami Jawhar"
email = "${USER_EMAIL}"
EOF
    else
        echo "Skipping jj config (non-interactive). Create ~/.config/jj/config.toml manually."
    fi
fi

# ------------------------------------------------------------------------------
# TPM (Tmux Plugin Manager)
# ------------------------------------------------------------------------------

if command -v tmux &>/dev/null; then
    TPM_DIR="${HOME}/.tmux/plugins/tpm"
    if [ ! -d "${TPM_DIR}/.git" ]; then
        echo "Installing TPM..."
        mkdir -p "${HOME}/.tmux/plugins"
        rm -rf "$TPM_DIR"
        git clone --depth 1 https://github.com/tmux-plugins/tpm "$TPM_DIR"
    fi
fi

# ------------------------------------------------------------------------------
# Claude Code
# ------------------------------------------------------------------------------

if ! command -v claude &>/dev/null; then
    echo "Installing Claude Code..."
    curl -fsSL https://claude.ai/install.sh | bash
fi

# ------------------------------------------------------------------------------
# OpenCode
# ------------------------------------------------------------------------------

if ! command -v opencode &>/dev/null; then
    echo "Installing OpenCode..."
    curl -fsSL https://opencode.ai/install | bash
fi

# ------------------------------------------------------------------------------
# OpenCode Plugins
# ------------------------------------------------------------------------------

OPENCODE_DIR="${HOME}/.config/opencode"
mkdir -p "$OPENCODE_DIR"

if [ ! -d "${OPENCODE_DIR}/compound-engineering/.git" ]; then
    echo "Cloning compound-engineering plugin..."
    git clone --depth 1 https://github.com/EveryInc/compound-engineering-plugin "${OPENCODE_DIR}/compound-engineering"
fi

# Symlink compound-engineering agents into OpenCode
mkdir -p "${OPENCODE_DIR}/agents"
find "${OPENCODE_DIR}/compound-engineering/plugins/compound-engineering/agents" \
    -type f -name '*.md' -exec sh -c '
    ln -sf "$1" "'"${OPENCODE_DIR}"'/agents/$(basename "$1")"
' sh {} \;

if [ ! -d "${OPENCODE_DIR}/superpowers/.git" ]; then
    echo "Cloning superpowers plugin..."
    git clone --depth 1 https://github.com/obra/superpowers.git "${OPENCODE_DIR}/superpowers"
fi

if ! command -v oh-my-opencode &>/dev/null; then
    echo "Installing oh-my-opencode..."
    npm install -g oh-my-opencode
fi

if [ ! -f "${OPENCODE_DIR}/opencode.json" ]; then
    if [ -t 0 ]; then
        echo "Running oh-my-opencode install..."
        oh-my-opencode install
    else
        echo "Skipping oh-my-opencode install (non-interactive). Run 'oh-my-opencode install' manually."
    fi
fi

if [ -f "${OPENCODE_DIR}/opencode.json" ]; then
    if ! grep -q 'opencode-antigravity-auth' "${OPENCODE_DIR}/opencode.json"; then
        echo "Adding opencode-antigravity-auth plugin..."
        TMPFILE=$(mktemp)
        jq '.plugin += ["opencode-antigravity-auth@beta"]' "${OPENCODE_DIR}/opencode.json" > "$TMPFILE" \
            && mv "$TMPFILE" "${OPENCODE_DIR}/opencode.json"
    fi

    # Override Anthropic Opus 4.6 context limit to 1M (models.dev reports 200k but API supports 1M with beta header)
    CURRENT_CONTEXT=$(jq -r '.provider.anthropic.models["claude-opus-4-6"].limit.context // 0' "${OPENCODE_DIR}/opencode.json")
    if [ "$CURRENT_CONTEXT" != "1000000" ]; then
        echo "Setting Opus 4.6 context limit to 1M..."
        TMPFILE=$(mktemp)
        jq '.provider.anthropic.models["claude-opus-4-6"].limit = {"context": 1000000, "output": 128000}' \
            "${OPENCODE_DIR}/opencode.json" > "$TMPFILE" \
            && mv "$TMPFILE" "${OPENCODE_DIR}/opencode.json"
    fi

    # Disable auto-upgrade (we run a patched binary with the 1M context beta header fix)
    if [ "$(jq -r '.autoupdate // true' "${OPENCODE_DIR}/opencode.json")" != "false" ]; then
        echo "Disabling autoupdate..."
        TMPFILE=$(mktemp)
        jq '.autoupdate = false' "${OPENCODE_DIR}/opencode.json" > "$TMPFILE" \
            && mv "$TMPFILE" "${OPENCODE_DIR}/opencode.json"
    fi

    # Configure Streamlinear MCP
    if ! jq -e '.mcp.linear' "${OPENCODE_DIR}/opencode.json" > /dev/null 2>&1; then
        echo "Adding Streamlinear MCP config..."
        TMPFILE=$(mktemp)
        jq '.mcp.linear = {"type": "local", "command": ["npx", "-y", "github:obra/streamlinear"], "environment": {"LINEAR_API_TOKEN": "$LINEAR_API_TOKEN"}, "enabled": true}' \
            "${OPENCODE_DIR}/opencode.json" > "$TMPFILE" \
            && mv "$TMPFILE" "${OPENCODE_DIR}/opencode.json"
    fi
fi

ln -sf "${DOTFILES_DIR}/oh-my-opencode.minimal.json" "${OPENCODE_DIR}/oh-my-opencode.json"

# ------------------------------------------------------------------------------
# Completions
# ------------------------------------------------------------------------------

echo "Generating completions..."
COMPLETIONS_DIR="${DOTFILES_DIR}/completions.d"
mkdir -p "$COMPLETIONS_DIR"

# Mise-managed tools (skip if not available)
jj util completion bash > "${COMPLETIONS_DIR}/jj.bash" || true
gh completion -s bash > "${COMPLETIONS_DIR}/gh.bash" || true

echo "=== Done! Restart your shell or run: source ~/.bashrc ==="
