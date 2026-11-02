#!/bin/bash
# Makes ~/.ssh/known_hosts append-only on a machine that runs agent sessions (devbox/install.sh
# runs this).
#
# Every session on the machine shares ~/.ssh/known_hosts. A program that writes over the file,
# instead of adding to it, removes the host keys of every other session, and their ssh then
# refuses each of those hosts. The kernel's append-only attribute (chattr +a) lets ssh,
# `ssh-keyscan >>` and every other appender add a key, and refuses every non-root process that
# truncates the file, renames another file over it, or removes it.
#
# The attribute also refuses ssh's UpdateHostKeys, which rewrites the file to add a host's other
# keys, and ssh then prints two error lines on each connection to such a host. So a drop-in in
# /etc/ssh/ssh_config.d turns UpdateHostKeys off for this user; it is installed first.
#
# To remove a stale key (a host rebuilt with a new key):
#   sudo chattr -a ~/.ssh/known_hosts && ssh-keygen -R <host> && sudo chattr +a ~/.ssh/known_hosts
# ssh-keygen -R puts a new file in place, which has no attribute until the last command (or a new
# run of this installer) sets it.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

KNOWN_HOSTS="${HOME}/.ssh/known_hosts"
DROPIN="${SSH_KNOWN_HOSTS_DROPIN:-/etc/ssh/ssh_config.d/50-known-hosts-append-only.conf}"

if [ "$(uname -s)" != Linux ]; then
    echo "ssh-known-hosts: not Linux, so ${KNOWN_HOSTS} is not made append-only"
    exit 0
fi
if ! sudo -n true 2>/dev/null; then
    echo "WARNING: ssh-known-hosts: no passwordless sudo, so ${KNOWN_HOSTS} is not made append-only" >&2
    exit 0
fi
if [ -L "$KNOWN_HOSTS" ]; then
    echo "ERROR: ssh-known-hosts: ${KNOWN_HOSTS} is a symlink; make it a file first" >&2
    exit 1
fi

user="$(id -un)"
want="# ${KNOWN_HOSTS} is append-only (installers/ssh-known-hosts.sh in the dotfiles), and
# UpdateHostKeys rewrites that file: each connection would print the refusal.
Match localuser ${user}
    UpdateHostKeys no"
if [ "$(cat "$DROPIN" 2>/dev/null)" != "$want" ]; then
    tmp="$(mktemp)"
    printf '%s\n' "$want" > "$tmp"
    sudo install -D -m 0644 "$tmp" "$DROPIN"
    rm -f "$tmp"
    echo "ssh-known-hosts: installed ${DROPIN} (UpdateHostKeys no for ${user})"
fi

if [ ! -e "$KNOWN_HOSTS" ]; then
    [ -d "$(dirname "$KNOWN_HOSTS")" ] || install -d -m 0700 "$(dirname "$KNOWN_HOSTS")"
    install -m 0600 /dev/null "$KNOWN_HOSTS"
fi
append_only() { [[ "$(lsattr -d "$KNOWN_HOSTS" 2>/dev/null | cut -d' ' -f1)" == *a* ]]; }
if ! append_only; then
    sudo chattr +a "$KNOWN_HOSTS"
    append_only || { echo "ERROR: ssh-known-hosts: chattr +a did not hold on ${KNOWN_HOSTS}" >&2; exit 1; }
    echo "ssh-known-hosts: ${KNOWN_HOSTS} is now append-only ($(wc -l < "$KNOWN_HOSTS") lines)"
fi
