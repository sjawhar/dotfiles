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
        ca-certificates curl gnupg git git-lfs openssh-client sudo jq iproute2 \
        python3 python3-jwt python3-cryptography build-essential pkg-config libssl-dev \
        tmux less procps xz-utils unzip zstd rsync file acl \
        python3-pip python3-yaml python3-venv postgresql-client sqlite3 socat wget bc bsdextrautils bubblewrap \
        inotify-tools squashfs-tools \
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
# The marker file is what a tool checks to know it is inside a box: shims/omp (which says the
# box cgroup is the memory ceiling instead of reporting the systemd scope it cannot get) and
# the disk-hygiene skill (which then sweeps the box tree alone). The home skeleton exists so
# the mounts land in ubuntu-owned parents.
RUN echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ubuntu && chmod 0440 /etc/sudoers.d/ubuntu \
    && usermod -aG docker ubuntu \
    && echo agentbox > /etc/agentbox-identity \
    && install -d -o ubuntu -g ubuntu /home/ubuntu/.cache /home/ubuntu/.config /home/ubuntu/.local /home/ubuntu/.local/share /home/ubuntu/.ssh

# bubblewrap, setuid root (2026-11-03: BET-64's leak-check tests run the real gh inside
# `bwrap --unshare-net`). Unprivileged, bwrap cannot run in a box: Sysbox refuses its loopback
# setup in a new network namespace ("loopback: Failed RTM_NEWADDR: Operation not permitted") and
# a /proc mount made from a nested user namespace ("Can't mount proc on /newroot/proc"). Setuid,
# it takes no user namespace, and `bwrap --unshare-net --ro-bind / / --dev /dev --proc /proc` runs
# as the box user. The box's root is not the host's (Sysbox maps it to an unprivileged host uid),
# and the box user has sudo already, so the bit grants nothing new. A run that also asks for a
# new user namespace (`--unshare-all`, `--unshare-user`) still fails in a box; such tests skip,
# and this pytest plugin, found by every pytest in the box through PYTHONPATH and an entry point
# (none is loaded where a caller replaces PYTHONPATH), prints a red banner after the summary that
# names how many skipped and why.
RUN chmod u+s /usr/bin/bwrap
COPY agentbox_sandbox_skips.py /opt/agentbox/python/agentbox_sandbox_skips.py
RUN install -d /opt/agentbox/python/agentbox_sandbox_skips-1.dist-info \
    && printf 'Metadata-Version: 2.1\nName: agentbox-sandbox-skips\nVersion: 1\n' \
        > /opt/agentbox/python/agentbox_sandbox_skips-1.dist-info/METADATA \
    && printf '[pytest11]\nagentbox_sandbox_skips = agentbox_sandbox_skips\n' \
        > /opt/agentbox/python/agentbox_sandbox_skips-1.dist-info/entry_points.txt
ENV PYTHONPATH=/opt/agentbox/python

# The entrypoint starts inner Docker and host-service relays, preserves host
# credential routes, then runs the session as its user. AgentBox adds no
# metadata firewall to nested workloads.
COPY box-ssh-config /usr/local/bin/box-ssh-config
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/box-ssh-config
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
