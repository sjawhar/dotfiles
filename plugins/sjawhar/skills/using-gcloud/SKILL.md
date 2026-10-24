---
name: using-gcloud
description: Use when running gcloud compute ssh or scp against several VMs, or where other sessions also use gcloud. Use on the ssh error "No ED25519 host key is known for compute.<id> and you have requested strict checking" or "Host key verification failed". Use when gcloud fails with "Reauthentication failed. cannot prompt during non-interactive execution." Use when you need a new build or measurement VM.
---

# Using gcloud

## Which identity gcloud uses

On an Azure VM enrolled in the box identity (`installers/gcloud-box.sh`, run by `devbox/install.sh`), gcloud and the application default credentials (ADC) act as the service account `dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com`. Its active configuration is `box` (project `autoformalization`). It needs no sign-in and does not expire. Client libraries (Python `google-cloud-storage`) use it too.

Sami's own Google login is in the configuration `theorem`. Use it only for admin work, or for a step that fails as the box with a permission error (example: a push to `us-east1-docker.pkg.dev/gcp-taiga`):

- gcloud: `gcloud --configuration theorem …` or `GCLOUD_ACCOUNT=theorem gcloud …`.
- Client libraries: `GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/legacy_credentials/sami@theorem.dev/adc.json`.

Never change the active configuration or the ADC file to get Sami's login: every session on the machine uses them.

## Expired login ("Reauthentication failed")

Sami's login expires about once each day. As the box you do not see this error.

1. If the command does not need Sami's login, run it without `--configuration theorem` or `GCLOUD_ACCOUNT=theorem`.
2. For a build or measurement machine, do not ask Sami to log in. Use `az vm create` (resource group `sami`, location `westus2`). `az` needs no daily login.
3. When a step needs Sami's login:
   - Start the login yourself. Do not ask Sami to run it. In a terminal that stays open (`tmux`), run `gcloud auth login --no-launch-browser` with no `--configuration`, `GCLOUD_ACCOUNT` or `CLOUDSDK_ACTIVE_CONFIG_NAME`. While `box` is the active configuration, `shims/gcloud` routes this plain login to `theorem`, says so, and drops `--update-adc`, so the box's ADC stays its own identity. The login renews `legacy_credentials/sami@theorem.dev/adc.json`, the file client libraries use (above). Only a non-empty value chooses an identity, and for a flag the last occurrence wins, as in gcloud: a login whose last `--cred-file` names a file passes through untouched so the box credential can be stored, while an empty `--cred-file`, `--configuration` or variable is an ordinary login and is routed to `theorem`.
   - Do not run `gcloud auth application-default login` or `revoke` for this: the shim refuses both while the box's ADC is an `external_account` credential.
   - Put each printed link and the box error in the open Dispatch re-login ask. Do not open a new ask. If the ask has a link from another session, add only your need.
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
