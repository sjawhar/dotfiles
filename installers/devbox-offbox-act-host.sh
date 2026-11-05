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
# It pins the acting scripts and prints the restricted key line. With --public-key FILE it also
# writes its own authorized_keys, read only by the separate tailnet OpenSSH listener on 2222.
# Its key directory is created by the listener deployment, root-owned 0755 on the boot disk.
# Installing a public key needs root or passwordless sudo; the acting scripts stay user-owned.
# It never changes ~/.ssh/authorized_keys, the primary SSH service, or any tailnet setting.
set -euo pipefail

name=devbox-offbox-act-host
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
host="${DEVBOX_OFFBOX_HOST:-sami-devbox}"
install_dir="${DEVBOX_OFFBOX_ACT_DIR:-/mnt/durable/sami/offbox-act}"
authorized_keys_dir="${DEVBOX_OFFBOX_AUTHORIZED_KEYS_DIR:-/etc/ssh/offbox-watch}"

# The addresses the key may be used from, for the line this prints. A tailnet node that joins
# ephemerally has a new address every run, so there is no list to write: the cluster job's sidecar
# is therefore a persistent node, and `--from <its address>` is read once with
# `tailscale ip -4 <node name>`. This script never invents one, and it never prints a `from=` that
# is not a restriction.
#
# Every element is PARSED, in both families, rather than compared against one spelling of one
# range. On 2026-11-05 a substring check for `100.64.0.0/10` accepted `fd7a:115c:a1e0::/48` (the
# same tailnet in IPv6), `100.64.0.0/9`, `100.64.0.0/8`, `0.0.0.0/0`, `*` and `TODO`, and printed
# each as "only <value> may use the key at all" (the INFRA lead's gate of agent-infra#2194). A
# `from=` that admits every node on the tailnet is not a restriction, and a printed line claiming
# it is, is worse than no line.
#
# So an element must be ONE address -- bare, or a /32 or /128 of it -- which is what a node has:
# a range is refused whatever its size, a name is refused because a name is not an address, and
# anything unparseable is refused. Loopback is refused too, added with the 2222 listener: the
# connection arrives through Tailscale's proxy, so a receiver-side loopback address is what the
# transport looks like, not what the caller is, and pinning to it would admit anything that
# reaches the listener by that route.
from=""
public_key=""
while (( $# )); do
    case "$1" in
        --from) from="${2:?--from takes the addresses the key may be used from}"; shift 2 ;;
        --no-from) from=""; shift ;;
        --public-key) public_key="${2:?--public-key takes an ed25519 public key file}"; shift 2 ;;
        *) echo "$name: usage: $name [--from <addresses> | --no-from] [--public-key FILE]" >&2; exit 2 ;;
    esac
done
if [[ -n "$public_key" && -z "$from" ]]; then
    echo "$name: --public-key requires --from; the listener admits one node only" >&2
    exit 2
fi
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
    elif not any(network.network_address in allowed for allowed in
                 (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))):
        bad.append((element, "not a tailnet node address; a loopback proxy source is not an identity"))
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

key_data="<the public key>"
if [[ -n "$public_key" ]]; then
    mapfile -t public_lines < "$public_key"
    if (( ${#public_lines[@]} != 1 )) || ! ssh-keygen -l -f "$public_key" >/dev/null 2>&1; then
        echo "$name: --public-key requires one valid ed25519 public key" >&2
        exit 2
    fi
    read -r key_type key_data _ <<<"${public_lines[0]}"
    if [[ "$key_type" != ssh-ed25519 ]]; then
        echo "$name: --public-key requires an ed25519 key, not $key_type" >&2
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

# OpenSSH resolves the key path and checks every ancestor through /. Verify that same chain
# before installing anything; never repair a shared directory's permissions for this listener.
root=()
if [[ -n "$public_key" ]]; then
    if [[ ! -d "$authorized_keys_dir" ]]; then
        echo "$name: key directory $authorized_keys_dir is missing; deploy the listener's root-owned 0755 directory first" >&2
        exit 1
    fi
    authorized_keys_dir="$(realpath -e -- "$authorized_keys_dir")"
    directory="$authorized_keys_dir"
    while :; do
        read -r mode owner < <(stat -Lc '%a %u' -- "$directory")
        if (( (8#$mode & 0022) != 0 )); then
            printf '%s: refusing key installation: %s has mode %04o (group or other writable; StrictModes would refuse the key)\n' \
                "$name" "$directory" "$((8#$mode))" >&2
            exit 1
        fi
        if [[ "$owner" != 0 ]]; then
            printf '%s: refusing key installation: %s has owner uid %s and mode %04o; root ownership is required\n' \
                "$name" "$directory" "$owner" "$((8#$mode))" >&2
            exit 1
        fi
        [[ "$directory" == / ]] && break
        directory="$(dirname "$directory")"
    done
    if (( EUID != 0 )); then
        if ! sudo -n true; then
            echo "$name: writing $authorized_keys_dir/authorized_keys requires root or passwordless sudo (-n)" >&2
            exit 1
        fi
        root=(sudo -n)
    fi
fi

commit="$(cd "$here" && GIT_OPTIONAL_LOCKS=0 git log -1 --format=%H -- scripts/devbox-offbox-act scripts/devbox-offbox-probe 2>/dev/null || true)"
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
    key_line="restrict,command=\"$install_dir/devbox-offbox-act --forced\",from=\"$from\" ssh-ed25519 $key_data devbox-offbox-watch@dispatch-cluster"
else
    key_line="restrict,command=\"$install_dir/devbox-offbox-act --forced\" ssh-ed25519 $key_data devbox-offbox-watch@dispatch-cluster"
fi
if [[ -n "$public_key" ]]; then
    # sshd opens this public file as the login user: root-owned 0600 would be unreadable.
    # Only root can replace it in the verified directory. No listener is started here.
    printf '%s\n' "$key_line" | "${root[@]}" install -o root -g root -m 0644 \
        /dev/stdin "$authorized_keys_dir/authorized_keys.new"
    "${root[@]}" mv -f "$authorized_keys_dir/authorized_keys.new" "$authorized_keys_dir/authorized_keys"
    echo "Installed $authorized_keys_dir/authorized_keys (root:root 0644) for the OpenSSH listener on tailnet port 2222."
else
    echo "Restricted key line (use --from <addresses> --public-key FILE to install it):"
fi
echo
echo "$key_line"
echo
echo "What that line allows: probe, live <uuid>..., restart <uuid> and await <uuid>, nothing else."
echo "  restrict  -- no port forward, no agent forward, no X11, no tty (sshd refuses these)"
echo "  command   -- no shell: every login runs the pinned copy, whatever the client asks for"
if [ -n "$from" ]; then
    echo "  from      -- only $from may use the key at all"
else
    echo "  (no from) -- nothing restricts WHERE the key may be used from, so what is left is"
    echo "               restrict, the forced command above, and the fact that nothing but this"
    echo "               job holds this key. The tailnet policy is NOT a second boundary here:"
    echo "               tag:offbox-watch's grant is the only TAG's route to this machine's 2222,"
    echo "               but every device of this machine's own owner reaches it as well"
    echo "               (autogroup:member -> autogroup:self in the policy)."
fi
echo "A restart takes its resume line from $(
    printf '%s' "${DEVBOX_OFFBOX_REGISTRY:-/mnt/durable/sami/restart-registry/sessions}")/<uuid>.entry"
echo "on this machine, never from the caller, so the key cannot ask for an arbitrary agentbox new."
