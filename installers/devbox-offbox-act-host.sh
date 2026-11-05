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

# The addresses the key may be used from, for the line this prints. A tailnet node that joins
# ephemerally has a new address every run, so there is no list to write: the cluster job's sidecar
# is therefore a persistent node, and `--from <its address>` is read once with
# `tailscale ip -4 <node name>`. This script never invents one. `--no-from` prints the line
# without `from=` and says plainly what then bounds the key: the single tailnet grant, `restrict`
# and the forced command (the INFRA lead's call on agent-infra#2194, 2026-11-05). A `from=` over
# the whole 100.64.0.0/10 range is not an option -- it restricts nothing beyond "arrived over the
# tailnet" -- so a placeholder is refused rather than printed.
from=""
case "${1-}" in
    --from) from="${2:?--from takes the addresses the key may be used from}" ;;
    --no-from | "") ;;
    *) echo "$name: usage: $name [--from <addresses> | --no-from]" >&2; exit 2 ;;
esac
case "$from" in
    *100.64.0.0/10* | *"<"*)
        echo "$name: --from $from is not a restriction: give the job's own node address, read once" >&2
        echo "  with 'tailscale ip -4 <node name>', or --no-from to rest on the tailnet grant." >&2
        exit 2
        ;;
esac

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
if [ -n "$from" ]; then
    key_line="restrict,command=\"$install_dir/devbox-offbox-act --forced\",from=\"$from\" ssh-ed25519 <the public key> devbox-offbox-watch@dispatch-cluster"
else
    key_line="restrict,command=\"$install_dir/devbox-offbox-act --forced\" ssh-ed25519 <the public key> devbox-offbox-watch@dispatch-cluster"
fi
echo "The authorized_keys line for the watch's key, which a PERSON adds to ~/.ssh/authorized_keys"
echo "on this machine (one line; fill in the public key):"
echo
echo "$key_line"
echo
echo "What that line allows: probe, live <uuid>..., restart <uuid> and await <uuid>, nothing else."
echo "  restrict  -- no shell, no port forward, no agent forward, no X11, no tty (sshd refuses"
echo "               each before the command runs)"
echo "  command   -- every login runs the pinned copy above, whatever the client asks for"
if [ -n "$from" ]; then
    echo "  from      -- only $from may use the key at all"
else
    echo "  (no from) -- the job's node is the one thing the tailnet policy lets reach this"
    echo "               machine's TCP 22 (tag:offbox-watch's single grant), and that grant plus"
    echo "               restrict and the forced command are the whole boundary. --from was not"
    echo "               given, so no address list is claimed here."
fi
echo "A restart takes its resume line from $(
    printf '%s' "${DEVBOX_OFFBOX_REGISTRY:-/mnt/durable/sami/restart-registry/sessions}")/<uuid>.entry"
echo "on this machine, never from the caller, so the key cannot ask for an arbitrary agentbox new."
