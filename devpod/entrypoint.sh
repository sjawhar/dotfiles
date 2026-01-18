#!/bin/bash
# Devpod startup script

set -e

tailscaled --tun=userspace-networking &
sleep 2

# Start tailscale with SSH and proxy listeners
# SOCKS5 proxy on :1055, HTTP proxy on :1080
(
    tailscale up --ssh \
        --socks5-server=localhost:1055 \
        --outbound-http-proxy-listen=localhost:1080
    echo "Tailscale connected: $(tailscale ip -4)"
    echo "SOCKS5 proxy: localhost:1055"
    echo "HTTP proxy: localhost:1080"
) &

exec /usr/sbin/sshd -D
