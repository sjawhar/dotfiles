# Agent box: the disposable container every personal agent session runs in.
# Started by scripts/agentbox under the Sysbox runtime, so root inside is an
# unprivileged user namespace on the host, the box runs its own dockerd, and
# `sudo apt-get` changes only this box. Tools (omp, jj, gh, bun, uv, ...) are
# not installed here: the host's mise store is mounted in, so the pins in
# mise.toml apply inside exactly as on the host.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git openssh-client sudo jq iproute2 \
        python3 python3-jwt python3-cryptography build-essential pkg-config libssl-dev \
        tmux less procps xz-utils unzip zstd rsync file \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# ubuntu:24.04 ships uid/gid 1000 = ubuntu with home /home/ubuntu, matching the
# host user whose directories are mounted in. Passwordless sudo is the whole
# point of the box: root here is Sysbox fake-root, unprivileged on the host.
# The marker file is what shims/omp checks to know it is inside a box.
RUN echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ubuntu && chmod 0440 /etc/sudoers.d/ubuntu \
    && usermod -aG docker ubuntu \
    && echo agentbox > /etc/agentbox-identity \
    && install -d -o ubuntu -g ubuntu /home/ubuntu/.cache /home/ubuntu/.config /home/ubuntu/.local /home/ubuntu/.local/share /home/ubuntu/.ssh

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
