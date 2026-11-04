#!/bin/bash
set -euo pipefail

# Box-side ssh access to Nickolai's machcsl VMs (machcsl-gcp-1, -2, -3 in project theorem-research)
# from sami-devbox and its agent boxes, as the box identity, never with a person's key
# (dispatch://INFRA-993). SCIENCE's Nickolai extract (judge measure/progress/extract_nick.sh) and
# export_progress.sh read these VMs read-only (`sudo -n ... bash -s`). On sami-rocqperf they log in
# as metadata user `sami` with ~/.ssh/google_compute_engine; that key is never copied.
# These VMs use metadata ssh keys (enable-oslogin=FALSE), not OS Login, so unlike
# installers/grade-farm.sh the key cannot go into the box identity's OS Login profile: its public
# half must be in each VM's instance ssh-keys for user `boxid`. That is a change to another
# person's VM, so this installer never writes metadata. It reports each VM where the key is absent
# and prints the command a person approves.
# It writes, under /mnt/nvme/<user>/boxid (an agent box reaches it by `--dir /mnt/nvme/<user>/boxid`):
#   nickvm-sshkey{,.pub}             the box identity's key for these VMs, made here
#   nickvm/ssh_config                one Host block per VM: user boxid, IAP tunnel through gcloud as
#                                    the box identity (boxid/gcloud-box), host key alias compute.<id>
#   nickvm/known_hosts               filled on first connect (accept-new)
#   nickvm/machcsl-gcp-N.cmd         the ssh command line extract_nick.sh reads: point it here with
#                                    NICK_SSH_DIR=/mnt/nvme/<user>/boxid/nickvm
# Devbox role installer: devbox/install.sh runs it after gcloud-boxid.sh. Idempotent; prints each
# change. Elsewhere (not a Google VM, or no /mnt/nvme/<user>) it changes nothing.

here="$(dirname "${BASH_SOURCE[0]}")"
NVME="/mnt/nvme/$(id -un)"
BOXID="${NICKVM_BOXID:-$NVME/boxid}"
DIR="$BOXID/nickvm"
KEY="$BOXID/nickvm-sshkey"
PROJECT=theorem-research
VM_USER=boxid
VMS=("machcsl-gcp-1 us-east1-b" "machcsl-gcp-2 us-east1-b" "machcsl-gcp-3 europe-west4-c")

if ! "$here/../scripts/on-google-vm"; then
    echo "nickvm-ssh: not a Google VM; nothing changed."
    exit 0
fi
if [[ ! -d "$BOXID/gcloud-box" ]]; then
    echo "nickvm-ssh: no $BOXID/gcloud-box (installers/gcloud-boxid.sh makes it); nothing changed."
    exit 0
fi
export CLOUDSDK_CONFIG="$BOXID/gcloud-box"

mkdir -p "$DIR"
if [[ ! -f "$KEY" ]]; then
    ssh-keygen -q -t ed25519 -N "" -C "nickvm-box-identity@$(hostname -s)" -f "$KEY"
    echo "nickvm-ssh: made $KEY"
fi
blob="$(awk '{print $2}' "$KEY.pub")"

conf="$(mktemp)"
cmd="$(mktemp)"
trap 'rm -f "$conf" "$cmd"' EXIT
echo "# Written by ~/.dotfiles/installers/nickvm-ssh.sh. Nickolai's machcsl VMs, as the box identity." >"$conf"
for entry in "${VMS[@]}"; do
    read -r vm zone <<<"$entry"
    if ! desc="$(gcloud compute instances describe "$vm" --project "$PROJECT" --zone "$zone" --format=json 2>/dev/null)"; then
        echo "nickvm-ssh: $vm ($zone) not found or not readable; no Host block for it."
        continue
    fi
    id="$(jq -r .id <<<"$desc")"
    cat >>"$conf" <<EOF

Host $vm
    HostName compute.$id
    HostKeyAlias compute.$id
    User $VM_USER
    IdentityFile $KEY
    IdentitiesOnly yes
    UserKnownHostsFile $DIR/known_hosts
    StrictHostKeyChecking accept-new
    CheckHostIP no
    ServerAliveInterval 30
    ProxyCommand env CLOUDSDK_CONFIG=$BOXID/gcloud-box gcloud compute start-iap-tunnel $vm %p --listen-on-stdin --project=$PROJECT --zone=$zone --verbosity=error
    ProxyUseFdpass no
EOF
    echo "ssh -F $DIR/ssh_config $vm" >"$cmd"
    if ! cmp -s "$cmd" "$DIR/$vm.cmd"; then
        install -m 644 "$cmd" "$DIR/$vm.cmd"
        echo "nickvm-ssh: wrote $DIR/$vm.cmd"
    fi
    if ! jq -r '.metadata.items[]? | select(.key == "ssh-keys") | .value' <<<"$desc" | grep -qF "$VM_USER:ssh-ed25519 $blob"; then
        echo "nickvm-ssh: $vm lacks the key for user $VM_USER. This installer changes no VM; after the owner's yes, run as the box identity:"
        echo "  CLOUDSDK_CONFIG=$BOXID/gcloud-box gcloud compute instances describe $vm --project $PROJECT --zone $zone --format=json | jq -r '.metadata.items[]|select(.key==\"ssh-keys\").value' > /tmp/$vm.keys && echo \"$VM_USER:\$(cat $KEY.pub)\" >> /tmp/$vm.keys && CLOUDSDK_CONFIG=$BOXID/gcloud-box gcloud compute instances add-metadata $vm --project $PROJECT --zone $zone --metadata-from-file ssh-keys=/tmp/$vm.keys"
    fi
done
if ! cmp -s "$conf" "$DIR/ssh_config"; then
    install -m 644 "$conf" "$DIR/ssh_config"
    echo "nickvm-ssh: wrote $DIR/ssh_config"
fi
echo "nickvm-ssh: ssh -F $DIR/ssh_config <vm>; NICK_SSH_DIR=$DIR; key $KEY"
