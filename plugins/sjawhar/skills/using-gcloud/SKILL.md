---
name: using-gcloud
description: Use when running gcloud compute ssh or scp against several VMs, or where other sessions also use gcloud. Use on the ssh error "No ED25519 host key is known for compute.<id> and you have requested strict checking" or "Host key verification failed". Use when gcloud fails with "Reauthentication failed. cannot prompt during non-interactive execution." Use when you need a new build or measurement VM.
---

# Using gcloud

## Expired login ("Reauthentication failed")

On sami-rocqperf, gcloud uses Sami's Google login. It expires about once each day.

1. Retry as the box account, which does not expire: `GCLOUD_ACCOUNT=box gcloud … --project <project>`. Name the project, because `box` defaults to `theorem-agent-infra`. The box account reaches some projects and not others.
2. Scripts that set their own configuration (`CLOUDSDK_ACTIVE_CONFIG_NAME=theorem`) and client libraries (Python `google-cloud-storage`) ignore `GCLOUD_ACCOUNT`. For these, set `CLOUDSDK_CONFIG=/mnt/nvme/sami/boxid/gcloud-box` and `GOOGLE_APPLICATION_CREDENTIALS=/mnt/nvme/sami/boxid/box-cred.json`. Every configuration in `gcloud-box` uses the box account.
3. For a build or measurement machine, do not ask Sami to log in. Use `az vm create` (resource group `sami`, location `westus2`). `az` needs no daily login.
4. Only a step that needs Google and fails as `box` with a permission error waits for Sami's login (example: a push to `us-east1-docker.pkg.dev/gcp-taiga`):
   - Start the login yourself. Do not ask Sami to run it. In a terminal that stays open (`tmux`), run `gcloud auth login --no-launch-browser`. For a client library, also run `gcloud auth application-default login --no-launch-browser`.
   - Put each printed link and the `box` error in the open Dispatch re-login ask. Do not open a new ask. If the ask has a link from another session, add only your need.
   - Type the code from Sami's answer into the waiting command.

## Host keys: one gcloud ssh call at a time

Each `gcloud compute ssh` or `scp` call rewrites all of `~/.ssh/google_compute_known_hosts` with no lock. Parallel calls remove each other's keys, also for other sessions on the machine.

- Never run two such calls at the same time: no `xargs -P`, `&` or parallel loop. A serial warm-up call does not prevent the race.

## Run one command on many VMs in parallel

1. One VM at a time: `gcloud compute ssh <vm> --zone <zone> --project <project> --tunnel-through-iap --dry-run > <vm>.cmd`. This writes the host keys and saves the ssh command.
2. In parallel: `eval "$(cat <vm>.cmd) -- '<command>'" &`, then `wait`. Plain `ssh` only reads the file.

On "Permission denied (publickey)", run one real `gcloud compute ssh` to that VM to install your key, then repeat step 1.

## Strict host key checking fails

1. Run the same `gcloud compute ssh` again, alone. It writes the keys again.
2. If it fails again, compare the file's key with `gcloud compute instances get-guest-attributes <vm> --zone <zone> --query-path=hostkeys/`.

Never remove `~/.ssh/google_compute_known_hosts`: it holds every session's keys. Never use `StrictHostKeyChecking=no` or `accept-new`: they stop the check that the VM is the right one.
