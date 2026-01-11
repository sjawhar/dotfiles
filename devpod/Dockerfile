# Devpod: Terminal-based development environment
# Works identically in local dev containers and remote K8s pods
#
# Build from the dotfiles root directory:
#   docker build -t devpod:test -f devpod/Dockerfile .

FROM debian:bookworm-slim

# Avoid interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive

# Install base system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Essential tools
    ca-certificates \
    curl \
    git \
    xz-utils \
    unzip \
    # Search tools
    ripgrep \
    fd-find \
    # Build essentials (for Python packages with native extensions)
    build-essential \
    # SSH server for remote access
    openssh-server \
    # Misc
    locales \
    && rm -rf /var/lib/apt/lists/* \
    # Create fd symlink (Debian names it fdfind)
    && ln -sf /usr/bin/fdfind /usr/local/bin/fd

# Set up locale (needed for proper terminal behavior)
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Install uv (Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install Python 3.13 via uv and symlink to /usr/local/bin
RUN uv python install 3.13 && \
    PYTHON_BIN=$(uv python find 3.13) && \
    ln -sf "$PYTHON_BIN" /usr/local/bin/python3 && \
    ln -sf "$PYTHON_BIN" /usr/local/bin/python

# Install pyright (Python LSP) via uv tool
RUN uv tool install pyright

# Install zellij (terminal multiplexer) - detect architecture
ENV ZELLIJ_VERSION=0.42.2
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "aarch64" ]; then ZELLIJ_ARCH="aarch64"; else ZELLIJ_ARCH="x86_64"; fi && \
    curl -fsSL "https://github.com/zellij-org/zellij/releases/download/v${ZELLIJ_VERSION}/zellij-${ZELLIJ_ARCH}-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin

# Install Neovim from Debian sid (bookworm only has 0.7.2, too old for lazy.nvim)
# Pin just neovim from sid to get 0.11.x while keeping system on bookworm
RUN echo "deb http://deb.debian.org/debian sid main" > /etc/apt/sources.list.d/sid.list && \
    echo 'Package: *\nPin: release a=unstable\nPin-Priority: 100' > /etc/apt/preferences.d/sid && \
    echo 'Package: neovim\nPin: release a=unstable\nPin-Priority: 900' >> /etc/apt/preferences.d/sid && \
    apt-get update && apt-get install -y --no-install-recommends neovim \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /root

# Copy entire dotfiles repo (build context is the dotfiles root)
COPY . /root/.dotfiles

# Run dotfiles install script (installs jj, starship, node, claude, etc.)
# DEVPOD_BUILD=1 skips interactive prompts and jj init
ENV DEVPOD_BUILD=1
RUN cd /root/.dotfiles && ./install.sh

# Set up config symlinks
RUN mkdir -p /root/.config/nvim /root/.config/zellij && \
    ln -sf /root/.dotfiles/devpod/nvim/init.lua /root/.config/nvim/init.lua && \
    [ ! -f /root/.dotfiles/devpod/zellij/config.kdl ] || \
    ln -sf /root/.dotfiles/devpod/zellij/config.kdl /root/.config/zellij/config.kdl

# Pre-install Neovim plugins (so first launch is fast)
# This may fail if lazy.nvim can't clone repos, which is ok during build
RUN nvim --headless "+Lazy! sync" +qa 2>/dev/null || true

# Configure SSH server
RUN mkdir -p /var/run/sshd /root/.ssh \
    && chmod 700 /root/.ssh \
    # Generate SSH host keys (required for sshd to start)
    && ssh-keygen -A \
    # Configure sshd for root login with keys only
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config

# Expose SSH port
EXPOSE 22

# Default shell
ENV SHELL=/bin/bash

# Set up startup script at /devpod/startup.sh (required by METR devpod TUI)
# SSH keys are mounted at /ssh-keys by the TUI
RUN mkdir -p /devpod \
    && chmod +x /root/.dotfiles/devpod/entrypoint.sh \
    && ln -sf /root/.dotfiles/devpod/entrypoint.sh /devpod/startup.sh

# Default command runs the startup script (starts sshd)
CMD ["/devpod/startup.sh"]
