# Host keys: one gcloud ssh call at a time

Each `gcloud compute ssh` or `scp` rewrites all of `~/.ssh/google_compute_known_hosts` with no lock, so parallel calls (`xargs -P`, `&`, a parallel loop) delete each other's keys, other sessions' included. A serial warm-up call does not prevent this.

To run one command on many VMs:

1. **One VM at a time:** `gcloud compute ssh <vm> --zone <zone> --project <project> --tunnel-through-iap --dry-run > <vm>.cmd`. This writes the host key and saves the ssh command.
2. **In parallel:** `eval "$(cat <vm>.cmd) -- '<command>'" &`, then `wait`. Plain `ssh` only reads the file.

On "Permission denied (publickey)", run one real `gcloud compute ssh` to that VM to install your key, then repeat step 1.

On "Host key verification failed" or "No ED25519 host key is known":

1. **Alone:** run the same `gcloud compute ssh` again; it writes the keys again.
2. **If it fails again:** compare the file's key with `gcloud compute instances get-guest-attributes <vm> --zone <zone> --query-path=hostkeys/`.
   - If they differ, stop and send the user the VM name and both keys.

Never delete `~/.ssh/google_compute_known_hosts`: it holds every session's keys. Never use `StrictHostKeyChecking=no` or `accept-new`.
