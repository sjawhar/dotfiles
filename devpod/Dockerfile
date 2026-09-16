# Agent box: the disposable container every personal agent session runs in.
# Started by scripts/agentbox under the Sysbox runtime, so root inside is an
# unprivileged user namespace on the host, the box runs its own dockerd, and
# `sudo apt-get` changes only this box. Tools (omp, jj, gh, bun, uv, ...) are
# not installed here: the host's mise store is mounted in, so the pins in
# mise.toml apply inside exactly as on the host. Chrome is the exception —
# omp's browser needs the system binary and its library closure, which mise
# does not manage.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git openssh-client sudo jq iproute2 \
        python3 python3-jwt python3-cryptography build-essential pkg-config libssl-dev \
        tmux less procps xz-utils unzip zstd rsync file \
        python3-pip postgresql-client sqlite3 socat wget \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# ubuntu:24.04 ships uid/gid 1000 = ubuntu with home /home/ubuntu, matching the
# host user whose directories are mounted in. Passwordless sudo is the whole
# point of the box: root here is Sysbox fake-root, unprivileged on the host.
# The marker file is what shims/omp checks to know it is inside a box. The
# world-writable lock file is the docker wrapper's serialization point for the
# lazy dockerd start: /run is root-owned and the wrapper runs as ubuntu.
RUN echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ubuntu && chmod 0440 /etc/sudoers.d/ubuntu \
    && usermod -aG docker ubuntu \
    && echo agentbox > /etc/agentbox-identity \
    && install -d -o ubuntu -g ubuntu /home/ubuntu/.cache /home/ubuntu/.config /home/ubuntu/.local /home/ubuntu/.local/share /home/ubuntu/.ssh \
    && install -m 0666 /dev/null /run/agentbox-docker.lock

# The docker and jj wrappers shadow the real binaries on the session PATH:
# docker starts the daemon lazily through the libexec, jj records workspace
# adds for the launcher's close/resume bookkeeping. The entrypoint only
# validates the environment and execs the session command.
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker /usr/local/bin/docker
COPY jj /usr/local/bin/jj
COPY agentbox-dockerd /usr/local/libexec/agentbox-dockerd
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/docker /usr/local/bin/jj /usr/local/libexec/agentbox-dockerd
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
