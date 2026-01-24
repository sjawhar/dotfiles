ARG BUILDX_VERSION=0.30.1-desktop.2
ARG DOCKER_VERSION=29.1.5
ARG HELM_VERSION=4.0.5
ARG K9S_VERSION=0.50.18
ARG KUBECTL_VERSION=1.34.3
ARG NEOVIM_VERSION=0.11.5
ARG PYTHON_VERSION=3.13.11
ARG UV_VERSION=0.9.26

# Source images for binary copies
FROM docker:${DOCKER_VERSION}-cli AS docker
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM rancher/kubectl:v${KUBECTL_VERSION} AS kubectl

# ------------------------------------------------------------------------------
# Base layer - shared packages and user setup
# ------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-trixie AS base
ARG DEVPOD_USER=sami
ARG DEVPOD_UID=1000
ARG DEVPOD_GID=1000

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8
ENV SHELL=/bin/bash

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        locales \
 && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
 && locale-gen

RUN groupadd -g ${DEVPOD_GID} ${DEVPOD_USER} \
 && useradd -m -u ${DEVPOD_UID} -g ${DEVPOD_USER} -s /bin/bash ${DEVPOD_USER}

# ------------------------------------------------------------------------------
# Builder: Download binaries not managed by mise
# ------------------------------------------------------------------------------
FROM base AS builder-downloads
ARG TARGETARCH
ARG BUILDX_VERSION
ARG HELM_VERSION
ARG K9S_VERSION
ARG NEOVIM_VERSION

RUN NVIM_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "arm64" || echo "x86_64") \
 && mkdir -p /build/nvim \
 && curl -fsSL "https://github.com/neovim/neovim/releases/download/v${NEOVIM_VERSION}/nvim-linux-${NVIM_ARCH}.tar.gz" \
    | tar -xz -C /build/nvim --strip-components=1

RUN curl -fsSL "https://github.com/derailed/k9s/releases/download/v${K9S_VERSION}/k9s_Linux_${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin k9s

RUN curl -fsSL "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin --strip-components=1 linux-${TARGETARCH}/helm

RUN mkdir -p /usr/local/libexec/docker/cli-plugins \
 && curl -fsSL "https://github.com/docker/buildx-desktop/releases/download/v${BUILDX_VERSION}/buildx-v${BUILDX_VERSION}.linux-${TARGETARCH}" \
        -o /usr/local/libexec/docker/cli-plugins/docker-buildx \
 && chmod +x /usr/local/libexec/docker/cli-plugins/docker-buildx

# ------------------------------------------------------------------------------
# Builder: Install tools via mise
# ------------------------------------------------------------------------------
FROM base AS builder-mise

RUN curl -fsSL https://mise.run | MISE_INSTALL_PATH=/usr/local/bin/mise sh

USER ${DEVPOD_USER}
WORKDIR /home/${DEVPOD_USER}
ENV MISE_DATA_DIR=/home/${DEVPOD_USER}/.mise

COPY --chown=${DEVPOD_USER}:${DEVPOD_USER} mise.toml .config/mise/config.toml
RUN --mount=type=cache,target=/home/${DEVPOD_USER}/.cache/mise,uid=${DEVPOD_UID},gid=${DEVPOD_GID} \
    mise trust \
  && mise install

# ------------------------------------------------------------------------------
# Final image
# ------------------------------------------------------------------------------
FROM base

# Additional packages for final image
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        bash-completion \
        dnsutils \
        fd-find \
        gnupg \
        groff \
        iputils-ping \
        less \
        man-db \
        mosh \
        netcat-openbsd \
        openssh-server \
        ripgrep \
        tmux \
        unzip \
        xz-utils \
 && ln -sf /usr/bin/fdfind /usr/local/bin/fd

# Tailscale
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
 && curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends tailscale \
 && mkdir -p /var/lib/tailscale /var/run/tailscale

# SSH server
RUN mkdir -p /var/run/sshd \
 && ssh-keygen -A \
 && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
 && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config

# Copy binaries from builders and source images
COPY --link --from=builder-downloads /build/nvim/ /usr/local/
COPY --link --from=builder-downloads /usr/local/bin/helm /usr/local/bin/helm
COPY --link --from=builder-downloads /usr/local/bin/k9s /usr/local/bin/k9s
COPY --link --from=builder-downloads /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --link --from=builder-mise /usr/local/bin/mise /usr/local/bin/mise
COPY --link --from=builder-mise --chown=${DEVPOD_UID}:${DEVPOD_GID} /home/${DEVPOD_USER}/.mise /home/${DEVPOD_USER}/.mise
COPY --link --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --link --from=docker /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --link --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY --link --from=uv /uv /uvx /usr/local/bin/

USER ${DEVPOD_USER}
WORKDIR /home/${DEVPOD_USER}
ENV MISE_DATA_DIR=/home/${DEVPOD_USER}/.mise
ENV PATH="/home/${DEVPOD_USER}/.local/bin:${PATH}"

COPY --chown=${DEVPOD_USER}:${DEVPOD_USER} . .dotfiles

RUN ./.dotfiles/install.sh --skip-mise

# Final setup
USER root
RUN mkdir -p /etc/bash_completion.d \
 && docker completion bash > /etc/bash_completion.d/docker \
 && helm completion bash > /etc/bash_completion.d/helm \
 && k9s completion bash > /etc/bash_completion.d/k9s \
 && kubectl completion bash > /etc/bash_completion.d/kubectl \
 && uv generate-shell-completion bash > /etc/bash_completion.d/uv

RUN mkdir -p /devpod \
 && ln -sf "/home/${DEVPOD_USER}/.dotfiles/devpod/entrypoint.sh" /devpod/startup.sh

EXPOSE 22
CMD ["/devpod/startup.sh"]
