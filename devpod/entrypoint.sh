#!/bin/bash
# Devpod startup script
# Compatible with METR devpod TUI - expects SSH keys mounted at /ssh-keys

set -e

# Copy SSH keys from mounted ConfigMap (TUI mounts keys here)
if [ -f /ssh-keys/authorized_keys ]; then
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    cp /ssh-keys/authorized_keys /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
fi

# Source dotfiles bashrc for environment setup
if [ -f /root/.dotfiles/.bashrc ]; then
    source /root/.dotfiles/.bashrc
fi

# Run sshd in foreground as PID 1
exec /usr/sbin/sshd -D
