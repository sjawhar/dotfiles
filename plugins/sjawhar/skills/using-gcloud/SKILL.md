---
name: using-gcloud
description: Use when running gcloud compute ssh or scp against several VMs at once, or where other sessions also use gcloud. Also use when ssh to a GCE VM fails with "No ED25519 host key is known for compute.<id> and you have requested strict checking". Also use on "Host key verification failed". Also use when gcloud fails with "Reauthentication failed. cannot prompt during non-interactive execution." Also use when you need a new build or measurement VM.
---

# Using gcloud

This machine has no vendor gcloud skill. This skill covers an expired login and one gcloud defect that parallel agents hit.

## Expired login: the box account first, then an Azure VM

On sami-rocqperf, gcloud uses Sami's own Google login. That login expires about once each day. Then every `gcloud` command fails with "Reauthentication failed. cannot prompt during non-interactive execution."

- First run the failed command again as the box's own account, which does not expire: `GCLOUD_ACCOUNT=box gcloud … --project <project>`. Name the project: the `box` configuration defaults to `theorem-agent-infra`. The box account reaches some projects and not others, and a project it cannot reach fails with a permission error.
- `GCLOUD_ACCOUNT=box` has no effect when a script sets its own configuration, for example `export CLOUDSDK_ACTIVE_CONFIG_NAME=theorem`. It also has no effect on client libraries, such as the Python `google-cloud-storage` library. For these, set `CLOUDSDK_CONFIG=/mnt/nvme/sami/boxid/gcloud-box` and `GOOGLE_APPLICATION_CREDENTIALS=/mnt/nvme/sami/boxid/box-cred.json`. All configurations in `gcloud-box` use the box account.
- For a build or measurement machine, do not ask Sami to log in again. Create the machine as an Azure VM: `az vm create` (run `az vm create --help` for the options). `az` works on this machine and does not need the daily login. Use resource group `sami` and location `westus2`.
- A step that fails as `box` with a permission error and needs Google itself waits for his login. Example: a push to Taiga's registry `us-east1-docker.pkg.dev/gcp-taiga`.
- For that wait, start the login yourself. Do not ask Sami to run a login command. Run `gcloud auth login --no-launch-browser` in a terminal that stays open, for example a `tmux` session. When a client library needs the login, also run `gcloud auth application-default login --no-launch-browser`. Each command prints a link and then waits for a code.
- Search Dispatch for the open re-login ask, and put the link and the `box` command's error in that ask. Do not open a new ask. When the ask already has a link from another session, add only your need. Sami opens the link and answers the ask with the code. Type that code into the command that waits for it.
- The permanent solution is a keyless identity: dispatch://INFRA-23.

## Host keys: one gcloud ssh call at a time

Each `gcloud compute ssh` or `gcloud compute scp` call reads the full `~/.ssh/google_compute_known_hosts` file. It then adds the VM's host keys and writes the full file back, with no lock. The call does this write even when the keys are already in the file. When two calls run at the same time, the second write removes the keys that the first call added. Other sessions on the same machine share this file. Your parallel calls can therefore break their ssh connections, and their calls can break yours.

- Do not run two `gcloud compute ssh` or `gcloud compute scp` calls at the same time.
- Do not use `xargs -P`, `&`, or a parallel loop around these commands.
- A serial warm-up call does not prevent the race. The race happens on every call.

## Run one command on many VMs in parallel

`--dry-run` writes the host keys and prints the full ssh command, and it does not connect. Plain `ssh` reads the known-hosts file but does not write it.

1. Run `gcloud compute ssh <vm> --zone <zone> --project <project> --tunnel-through-iap --dry-run > <vm>.cmd` for each VM, one VM at a time.
2. Run the saved commands in parallel. Add the remote command at the end: `eval "$(cat <vm>.cmd) -- '<command>'" &`, then `wait`.

`--dry-run` does not install your SSH key on the VM. If plain `ssh` fails with "Permission denied (publickey)", run one real `gcloud compute ssh` call to that VM, then repeat step 1 for it.

## When ssh fails strict host key checking

This error usually means that a parallel gcloud call removed the key.

1. Run the same `gcloud compute ssh` command again, alone. gcloud gets the host keys from the VM and writes them again.
2. If it fails again, compare the key in the file with the key the VM publishes: `gcloud compute instances get-guest-attributes <vm> --zone <zone> --query-path=hostkeys/`.

| Do not | Why |
| --- | --- |
| Remove `~/.ssh/google_compute_known_hosts` | It removes the keys of every other session on the machine. |
| Use `StrictHostKeyChecking=no` or `accept-new` | It stops the check that the VM is the correct VM. |
