#!/bin/bash
# The key agent boxes on this machine use to reach the host's own sshd, and the host's pinned key
# (devbox/install.sh runs this on a Google VM, sami-devbox, dispatch://INFRA-993).
#
# A box's connection to the host's own tailnet address reaches OpenSSH, not Tailscale SSH, so a
# box needs a key the host's authorized_keys accepts. This makes, for host <h> (`hostname -s`):
#   ~/.ssh/<h>, ~/.ssh/<h>.pub   an ed25519 key made here when absent; never copied from elsewhere
#   ~/.ssh/authorized_keys       one unrestricted line for this installer's key
#   ~/.ssh/known_hosts.<h>       the host's ed25519 key under its short name, tailnet name and
#                                tailnet IPv4
# agentbox/mounts shares the host SSH directory. The box generates private system defaults.
#
# Other authorized_keys lines are preserved. --unrestrict-existing changes only
# this installer's existing option prefix, without key generation or network setup.
set -euo pipefail
# shellcheck source=installers/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

host="$(hostname -s)"
ssh_dir="$HOME/.ssh"
key="$ssh_dir/$host"
auth="$ssh_dir/authorized_keys"
pinned="$ssh_dir/known_hosts.$host"

existing_only=0
fingerprint=""
while (( $# )); do
    case "$1" in
        --unrestrict-existing) existing_only=1; shift ;;
        --key-fingerprint)
            [[ "${2:-}" =~ ^SHA256:[A-Za-z0-9+/]{43}$ ]] || { echo "agentbox-host-ssh: invalid key fingerprint" >&2; exit 2; }
            fingerprint="$2"; shift 2 ;;
        *) echo "usage: agentbox-host-ssh.sh [--unrestrict-existing [--key-fingerprint SHA256:...]]" >&2; exit 2 ;;
    esac
done
[[ -z "$fingerprint" || "$existing_only" == 1 ]] || { echo "agentbox-host-ssh: fingerprint selection needs --unrestrict-existing" >&2; exit 2; }
if (( existing_only )); then
    [[ -f "$auth" && ( -n "$fingerprint" || -s "$key.pub" ) ]] || { echo "agentbox-host-ssh: existing key or authorized_keys missing; nothing changed" >&2; exit 1; }
else
    install -d -m 0700 "$ssh_dir"
    if [[ ! -e "$key" ]]; then
        ssh-keygen -q -t ed25519 -N '' -C "agentbox boxes on $host -> $host sshd" -f "$key"
        echo "agentbox-host-ssh: made $key"
    elif [[ ! -s "$key.pub" ]]; then
        ssh-keygen -y -f "$key" >"$key.pub"
    fi
fi
if [[ -n "$fingerprint" ]]; then
    # The cross-host key's public file stays on its source machine. Select its
    # existing target entry by fingerprint without copying any key or login.
    key_line="$(python3 - "$auth" "$fingerprint" <<'PY'
import base64, binascii, hashlib, pathlib, shlex, sys
matches = []
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    if line.lstrip().startswith('#'):
        continue
    try:
        fields = shlex.split(line)
    except ValueError:
        continue
    for index, field in enumerate(fields[:-1]):
        if field != 'ssh-ed25519':
            continue
        try:
            digest = hashlib.sha256(base64.b64decode(fields[index + 1], validate=True)).digest()
        except (ValueError, binascii.Error):
            continue
        fingerprint = 'SHA256:' + base64.b64encode(digest).decode().rstrip('=')
        if fingerprint == sys.argv[2]:
            matches.append((field, fields[index + 1]))
if len(matches) != 1:
    sys.exit('agentbox-host-ssh: expected exactly one existing key with this fingerprint')
print(*matches[0])
PY
    )"
    read -r key_type key_b64 <<<"$key_line"
    pub="$key_line"
else
    read -r key_type key_b64 _ <"$key.pub"
    pub="$(head -1 "$key.pub")"
fi

want="$pub"

[[ -e "$auth" ]] || install -m 0600 /dev/null "$auth"
# Strip only the exact prefix this installer wrote, on this key's own line.
auth_before="$(stat -c '%a:%u:%g:%i:%Y' "$auth") $(sha256sum "$auth")"
tmp="$(mktemp "$ssh_dir/.authorized_keys.XXXXXX")"
cp --preserve=mode,ownership "$auth" "$tmp"
awk -v type="$key_type" -v key="$key_b64" -v from_only="${fingerprint:+1}" '
    ($1 ~ /^from="[^"]+",no-agent-forwarding,no-X11-forwarding$/ || (from_only && $1 ~ /^from="[^"]+"$/)) && $2 == type && $3 == key {
        sub(/^[^[:space:]]+[[:space:]]+/, "")
    }
    { print }
' "$auth" >"$tmp"
if [[ "$(stat -c '%a:%u:%g:%i:%Y' "$auth") $(sha256sum "$auth")" != "$auth_before" ]]; then
    rm -f "$tmp"
    echo "agentbox-host-ssh: authorized_keys changed during reconciliation; nothing replaced" >&2
    exit 1
fi
if cmp -s "$auth" "$tmp"; then
    rm -f "$tmp"
else
    mv -f "$tmp" "$auth"
    echo "agentbox-host-ssh: removed this key's installer-managed options"
fi
if (( existing_only )); then exit 0; fi
# A separately configured line for the same key is not replaced or bypassed.
if ! awk -v type="$key_type" -v key="$key_b64" '
    { for (i = 1; i < NF; i++) if ($i == type && $(i + 1) == key) found = 1 }
    END { exit !found }
' "$auth"; then
    [[ ! -s "$auth" || "$(tail -c 1 "$auth")" == "" ]] || echo >>"$auth"
    printf '%s\n' "$want" >>"$auth"
    echo "agentbox-host-ssh: added this host's key"
fi

dns="$(tailscale status --self --json | jq -r '.Self.DNSName | rtrimstr(".")')"
ip4="$(tailscale ip -4 | head -1)"
[[ -n "$dns" && -n "$ip4" ]] || { echo "ERROR: agentbox-host-ssh: no tailnet name or IPv4 (tailscale up?)" >&2; exit 1; }
read -r host_type host_b64 _ </etc/ssh/ssh_host_ed25519_key.pub
pin="$host,$dns,$ip4 $host_type $host_b64"
if [[ "$(cat "$pinned" 2>/dev/null)" != "$pin" ]]; then
    printf '%s\n' "$pin" >"$pinned.new"
    chmod 644 "$pinned.new"
    mv -f "$pinned.new" "$pinned"
    echo "agentbox-host-ssh: wrote $pinned"
fi
