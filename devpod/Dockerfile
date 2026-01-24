ARG AWS_CLI_VERSION=2.32.34
ARG BUILDX_VERSION=0.30.1-desktop.2
ARG DOCKER_VERSION=29.1.5
ARG HELM_VERSION=4.0.5
ARG JJ_VERSION=0.37.0
ARG K9S_VERSION=0.50.18
ARG KUBECTL_VERSION=1.34.3
ARG NEOVIM_VERSION=0.11.5
ARG NODE_VERSION=22.21.1
ARG PYTHON_VERSION=3.13.11
ARG UV_VERSION=0.9.26

FROM amazon/aws-cli:${AWS_CLI_VERSION} AS aws-cli
FROM docker:${DOCKER_VERSION}-cli AS docker
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM node:${NODE_VERSION}-trixie AS node
FROM rancher/kubectl:v${KUBECTL_VERSION} AS kubectl

# Download stage - fetch all binaries in parallel
FROM debian:trixie-slim AS downloads
ARG TARGETARCH
ARG BUILDX_VERSION
ARG HELM_VERSION
ARG JJ_VERSION
ARG K9S_VERSION
ARG NEOVIM_VERSION

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

RUN NVIM_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "arm64" || echo "x86_64") \
 && curl -fsSL "https://github.com/neovim/neovim/releases/download/v${NEOVIM_VERSION}/nvim-linux-${NVIM_ARCH}.tar.gz" \
    | tar -xz -C /opt --strip-components=1

RUN JJ_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "aarch64" || echo "x86_64") \
 && curl -fsSL "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-${JJ_ARCH}-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin

RUN curl -fsSL "https://github.com/derailed/k9s/releases/download/v${K9S_VERSION}/k9s_Linux_${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin k9s

RUN curl -fsSL "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin --strip-components=1 linux-${TARGETARCH}/helm

RUN mkdir -p /usr/local/libexec/docker/cli-plugins \
 && curl -fsSL "https://github.com/docker/buildx-desktop/releases/download/v${BUILDX_VERSION}/buildx-v${BUILDX_VERSION}.linux-${TARGETARCH}" \
        -o /usr/local/libexec/docker/cli-plugins/docker-buildx \
 && chmod +x /usr/local/libexec/docker/cli-plugins/docker-buildx

FROM python:${PYTHON_VERSION}-trixie AS base

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8
ENV SHELL=/bin/bash

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        bash-completion \
        build-essential \
        ca-certificates \
        curl \
        dnsutils \
        fd-find \
        git \
        gnupg \
        iputils-ping \
        groff \
        less \
        locales \
        mosh \
        netcat-openbsd \
        openssh-server \
        ripgrep \
        tmux \
        unzip \
        xz-utils \
 && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
 && locale-gen \
 && echo 'LANG=en_US.UTF-8\nLC_ALL=en_US.UTF-8' > /etc/default/locale \
 && ln -sf /usr/bin/fdfind /usr/local/bin/fd

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
 && curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends tailscale \
 && mkdir -p /var/lib/tailscale

COPY --link --from=downloads /opt/ /usr/local/

RUN mkdir -p /var/run/sshd \
 && ssh-keygen -A \
 && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
 && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config

COPY --link --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY --link --from=downloads /usr/local/bin/jj /usr/local/bin/jj
COPY --link --from=downloads /usr/local/bin/k9s /usr/local/bin/k9s
COPY --link --from=downloads /usr/local/bin/helm /usr/local/bin/helm
COPY --link --from=aws-cli /usr/local/aws-cli/v2/current /usr/local
COPY --link --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --link --from=docker /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --link --from=downloads /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --link --from=uv /uv /uvx /usr/local/bin/
COPY --link --from=node /usr/local/bin /usr/local/bin
COPY --link --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN mkdir -p /etc/bash_completion.d \
 && kubectl completion bash > /etc/bash_completion.d/kubectl \
 && helm completion bash > /etc/bash_completion.d/helm \
 && k9s completion bash > /etc/bash_completion.d/k9s \
 && docker completion bash > /etc/bash_completion.d/docker \
 && uv generate-shell-completion bash > /etc/bash_completion.d/uv \
 && jj util completion bash > /etc/bash_completion.d/jj \
 && echo 'complete -C /usr/local/bin/aws_completer aws' > /etc/bash_completion.d/aws

ARG APP_USER=sami
ARG USER_ID=1000
ARG GROUP_ID=1000
RUN groupadd -g ${GROUP_ID} ${APP_USER} \
 && useradd -m -u ${USER_ID} -g ${APP_USER} -s /bin/bash ${APP_USER} \
 && mkdir -p \
    /home/${APP_USER}/.cache/mise \
    /home/${APP_USER}/.tmux/plugins \
 && chown -R ${APP_USER}:${APP_USER} /home/${APP_USER}

USER ${APP_USER}
WORKDIR /home/${APP_USER}
RUN git clone https://github.com/tmux-plugins/tpm .tmux/plugins/tpm

COPY --chown=${APP_USER}:${APP_USER} . .dotfiles
ENV PATH="/home/${APP_USER}/.local/bin:${PATH}"

RUN --mount=type=cache,target=/home/${APP_USER}/.cache/mise,uid=${USER_ID},gid=${GROUP_ID} \
    ./.dotfiles/install.sh

RUN nvim --headless "+Lazy! sync" +qa 2>/dev/null || true

USER root
RUN mkdir -p /var/run/tailscale \
 && mkdir -p /devpod \
 && ln -sf "/home/${APP_USER}/.dotfiles/devpod/entrypoint.sh" /devpod/startup.sh
 
EXPOSE 22
CMD ["/devpod/startup.sh"]
