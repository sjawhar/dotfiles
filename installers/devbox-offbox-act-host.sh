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
# `tailscale ip -4 <node name>`. This script never invents one, and it never prints a `from=` that
# is not a restriction.
#
# Every element of the list is PARSED, in both families, rather than compared against one spelling
# of one range: on 2026-11-05 a substring check for `100.64.0.0/10` accepted `fd7a:115c:a1e0::/48`
# (the same tailnet in IPv6), `100.64.0.0/9`, `100.64.0.0/8`, `0.0.0.0/0`, `*` and `TODO`, and
# printed each as "only <value> may use the key at all" (the INFRA lead's gate of
# agent-infra#2194). An element must therefore be one address -- a bare address, or a /32 or /128
# of it -- which is what a node has; a range is refused whatever its size, a name is refused
# because a name is not an address, and anything unparseable is refused.
from=""
case "${1-}" in
    --from) from="${2:?--from takes the addresses the key may be used from}" ;;
    --no-from | "") ;;
    *) echo "$name: usage: $name [--from <addresses> | --no-from]" >&2; exit 2 ;;
esac
if [ -n "$from" ]; then
    # Checked and normalised in one step: sshd's `from=` is a comma-separated list, and a person
    # who passes two addresses with a space between them must not get a line sshd reads as one
    # malformed pattern.
    if ! from="$(python3 - "$from" <<'EOF'
import ipaddress
import sys

bad = []
elements = [e.strip() for e in sys.argv[1].replace(",", " ").split()]
if not elements:
    bad.append(("(empty)", "no address at all"))
for element in elements:
    try:
        network = ipaddress.ip_network(element, strict=False)
    except ValueError:
        bad.append((element, "not an address; a host name is not one either, and sshd would match"
                            " it as a pattern"))
        continue
    if network.num_addresses != 1:
        bad.append((element, f"a range of {network.num_addresses} addresses, not one node's"))
for element, why in bad:
    print(f"  {element}: {why}", file=sys.stderr)
if bad:
    sys.exit(1)
print(",".join(elements))
EOF
    )"; then
        echo "$name: --from takes the job's own node addresses, one per element." >&2
        echo "  Read them once with 'tailscale ip -4 <node name>' and 'tailscale ip -6 <node name>'," >&2
        echo "  or pass --no-from. A range is refused however it is spelled: the tailnet's own" >&2
        echo "  100.64.0.0/10 and fd7a:115c:a1e0::/48, any supernet of either, and 0.0.0.0/0 all admit" >&2
        echo "  every node on the tailnet, which restricts nothing beyond 'arrived over the tailnet'." >&2
        exit 2
    fi
fi

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
# The record of what is installed, written beside the files. 0444, and written through a
# temporary name, so running this installer again replaces it instead of failing on the
# read-only file it left last time.
{
    echo "installed=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "commit=$commit"
    echo "by=${OMP_SESSION_ID:-$(id -un)@$(hostname -s)}"
    echo "source=$here/scripts"
} >"$install_dir/PINNED.new"
chmod 0444 "$install_dir/PINNED.new"
mv -f "$install_dir/PINNED.new" "$install_dir/PINNED"

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
    echo "  (no from) -- nothing restricts WHERE the key may be used from, so what is left is"
    echo "               restrict, the forced command above, and the fact that nothing but this"
    echo "               job holds this key. The tailnet policy is NOT a second boundary here:"
    echo "               tag:offbox-watch's grant is the only TAG's route to this machine's 22,"
    echo "               but every device of this machine's own owner reaches it as well"
    echo "               (autogroup:member -> autogroup:self in the policy)."
fi
echo "A restart takes its resume line from $(
    printf '%s' "${DEVBOX_OFFBOX_REGISTRY:-/mnt/durable/sami/restart-registry/sessions}")/<uuid>.entry"
echo "on this machine, never from the caller, so the key cannot ask for an arbitrary agentbox new."
