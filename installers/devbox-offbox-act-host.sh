#!/bin/bash
# Install the pinned acting half of the off-box watch ON the machine it acts on.
#
# Standalone installer (like devbox-offbox-watch.sh) -- not sourced by install.sh. Run it on
# sami-devbox, the machine being watched; the watch itself runs elsewhere.
#
# Why a pinned copy exists at all: the watch on sami-rocqperf ships scripts/devbox-offbox-act over
# ssh stdin, so the two halves can never be a publish apart. The watch in the dispatch cluster
# cannot do that (dispatch://INFRA-993): its key in this machine's authorized_keys carries a forced
# command, and a forced command gets no stdin of its own. So the host holds a copy, this installs
# it, and an update is a deliberate run of this installer -- never a publish of the dotfiles.
#
# It writes two files and prints the authorized_keys line for the key. It NEVER edits
# authorized_keys: who may log in to this machine is a person's decision, made by hand.
set -euo pipefail

name=devbox-offbox-act-host
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
host="${DEVBOX_OFFBOX_HOST:-sami-devbox}"
install_dir="${DEVBOX_OFFBOX_ACT_DIR:-/mnt/durable/sami/offbox-act}"

if [ "$(hostname -s)" != "$host" ]; then
    echo "$name: this is $(hostname -s), not $host. The pinned copy belongs ON the watched" >&2
    echo "  machine; the watch itself runs somewhere else (installers/devbox-offbox-watch.sh)." >&2
    echo "  Set DEVBOX_OFFBOX_HOST to watch another machine." >&2
    exit 1
fi

for file in devbox-offbox-act devbox-offbox-probe; do
    [ -r "$here/scripts/$file" ] || { echo "$name: no $here/scripts/$file" >&2; exit 1; }
done

commit="$(cd "$here" && git log -1 --format=%H -- scripts/devbox-offbox-act scripts/devbox-offbox-probe 2>/dev/null || true)"
[ -n "$commit" ] || commit="unknown (not a checkout, or the files have never been committed)"

mkdir -p "$install_dir"
for file in devbox-offbox-act devbox-offbox-probe; do
    # Written to a temporary file and renamed, so a key's forced command never reads half a file.
    install -m 0555 "$here/scripts/$file" "$install_dir/$file.new"
    mv "$install_dir/$file.new" "$install_dir/$file"
done
{
    echo "installed=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "commit=$commit"
    echo "by=${OMP_SESSION_ID:-$(id -un)@$(hostname -s)}"
    echo "source=$here/scripts"
} >"$install_dir/PINNED"
chmod 0444 "$install_dir/PINNED"

echo "$name: installed the pinned acting half in $install_dir at $commit"
echo
echo "The authorized_keys line for the watch's key, which a PERSON adds to ~/.ssh/authorized_keys"
echo "on this machine (one line; fill in the job's tailnet addresses and the public key):"
echo
echo "restrict,command=\"$install_dir/devbox-offbox-act --forced\",from=\"<the job's tailnet IPs>\" ssh-ed25519 <the public key> devbox-offbox-watch@dispatch-cluster"
echo
echo "What that line allows: probe, live <uuid>..., restart <uuid> and await <uuid>, nothing else."
echo "  restrict  -- no shell, no port forward, no agent forward, no X11, no tty (sshd refuses"
echo "               each before the command runs)"
echo "  command   -- every login runs the pinned copy above, whatever the client asks for"
echo "  from      -- only the pod's tailnet addresses may use the key at all"
echo "A restart takes its resume line from $(
    printf '%s' "${DEVBOX_OFFBOX_REGISTRY:-/mnt/durable/sami/restart-registry/sessions}")/<uuid>.entry"
echo "on this machine, never from the caller, so the key cannot ask for an arbitrary agentbox new."
