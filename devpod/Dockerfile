ARG AWS_CLI_VERSION=2.32.34
ARG BUILDX_VERSION=0.30.1-desktop.2
ARG DOCKER_VERSION=29.1.4
ARG HELM_VERSION=4.0.4
ARG JJ_VERSION=0.37.0
ARG K9S_VERSION=0.50.18
ARG KUBECTL_VERSION=1.34.1
ARG NEOVIM_VERSION=0.11.5
ARG NODE_VERSION=22.21.1
ARG PYTHON_VERSION=3.13.9
ARG UV_VERSION=0.9.18

FROM amazon/aws-cli:${AWS_CLI_VERSION} AS aws-cli
FROM docker:${DOCKER_VERSION}-cli AS docker
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM node:${NODE_VERSION}-bookworm AS node
FROM rancher/kubectl:v${KUBECTL_VERSION} AS kubectl

FROM python:${PYTHON_VERSION}-bookworm AS base

ARG APP_USER=sami
ARG USER_ID=1000
ARG GROUP_ID=1000
ARG TARGETARCH
ARG BUILDX_VERSION
ARG HELM_VERSION
ARG JJ_VERSION
ARG K9S_VERSION
ARG NEOVIM_VERSION

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
    curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
    && curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends tailscale \
    && mkdir -p /var/lib/tailscale

RUN groupadd -g ${GROUP_ID} ${APP_USER} \
    && useradd -m -u ${USER_ID} -g ${APP_USER} -s /bin/bash ${APP_USER}

RUN NVIM_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "arm64" || echo "x86_64") \
    && curl -fsSL "https://github.com/neovim/neovim/releases/download/v${NEOVIM_VERSION}/nvim-linux-${NVIM_ARCH}.tar.gz" \
    | tar -xz -C /usr/local --strip-components=1

RUN mkdir -p /var/run/sshd \
    && ssh-keygen -A \
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config

COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl

RUN JJ_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "aarch64" || echo "x86_64") \
    && curl -fsSL "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-${JJ_ARCH}-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin

RUN curl -fsSL "https://github.com/derailed/k9s/releases/download/v${K9S_VERSION}/k9s_Linux_${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin k9s

RUN curl -fsSL "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin --strip-components=1 linux-${TARGETARCH}/helm

COPY --from=aws-cli /usr/local/aws-cli /usr/local/aws-cli
RUN ln -sf /usr/local/aws-cli/v2/current/bin/aws /usr/local/bin/aws \
    && ln -sf /usr/local/aws-cli/v2/current/bin/aws_completer /usr/local/bin/aws_completer

COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

RUN mkdir -p /usr/local/libexec/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/buildx-desktop/releases/download/v${BUILDX_VERSION}/buildx-v${BUILDX_VERSION}.linux-${TARGETARCH}" \
        -o /usr/local/libexec/docker/cli-plugins/docker-buildx \
    && chmod +x /usr/local/libexec/docker/cli-plugins/docker-buildx

COPY --from=uv /uv /uvx /usr/local/bin/

COPY --from=node /usr/local/bin /usr/local/bin
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN uv tool install pyright

RUN mkdir -p /etc/bash_completion.d \
    && kubectl completion bash > /etc/bash_completion.d/kubectl \
    && helm completion bash > /etc/bash_completion.d/helm \
    && k9s completion bash > /etc/bash_completion.d/k9s \
    && docker completion bash > /etc/bash_completion.d/docker \
    && uv generate-shell-completion bash > /etc/bash_completion.d/uv \
    && jj util completion bash > /etc/bash_completion.d/jj \
    && echo 'complete -C /usr/local/bin/aws_completer aws' > /etc/bash_completion.d/aws

RUN mkdir -p /var/run/tailscale \
    && mkdir -p /devpod

ARG APP_USER
WORKDIR /home/${APP_USER}
COPY --chown=${APP_USER}:${APP_USER} . /home/${APP_USER}/.dotfiles

RUN mkdir -p /home/${APP_USER}/.config/nvim \
    && ln -sf /home/${APP_USER}/.dotfiles/nvim/init.lua /home/${APP_USER}/.config/nvim/init.lua \
    && ln -sf /home/${APP_USER}/.dotfiles/.tmux.conf /home/${APP_USER}/.tmux.conf \
    && chown -R ${APP_USER}:${APP_USER} /home/${APP_USER}/.config

RUN git clone https://github.com/tmux-plugins/tpm /home/${APP_USER}/.tmux/plugins/tpm \
    && chown -R ${APP_USER}:${APP_USER} /home/${APP_USER}/.tmux

RUN chmod +x /home/${APP_USER}/.dotfiles/devpod/entrypoint.sh \
    && ln -sf /home/${APP_USER}/.dotfiles/devpod/entrypoint.sh /devpod/startup.sh

USER ${APP_USER}
ENV HOME=/home/${APP_USER}
ENV PATH="/home/${APP_USER}/.local/bin:${PATH}"

RUN cd /home/${APP_USER}/.dotfiles && DEVPOD_BUILD=1 ./install.sh

RUN nvim --headless "+Lazy! sync" +qa 2>/dev/null || true

USER root
RUN rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache /home/${APP_USER}/.cache

EXPOSE 22
EXPOSE 60000-60010/udp

CMD ["/devpod/startup.sh"]
