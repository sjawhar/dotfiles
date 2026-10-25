---
name: using-gcloud
description: Use when running gcloud compute ssh or scp against several VMs, or where other sessions also use gcloud. Use on the ssh error "No ED25519 host key is known for compute.<id> and you have requested strict checking" or "Host key verification failed". Use when gcloud fails with "Reauthentication failed. cannot prompt during non-interactive execution." Use when you need a new build or measurement VM.
---

# Using gcloud

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

## Which identity gcloud uses

On an Azure VM enrolled in the box identity (`~/.dotfiles/installers/gcloud-box.sh`), gcloud, the application default credentials (ADC) and client libraries all act as the service account `dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com`, in the active configuration `box` (project `autoformalization`). It needs no sign-in and does not expire.

Sami's own Google login is the configuration `theorem`, for admin work only. When a step fails as the box with a permission error, name the missing permission and propose widening the box account's grant; do not fall back to his login. For admin work:

- gcloud: `gcloud --configuration theorem …` or `GCLOUD_ACCOUNT=theorem gcloud …`.
- Client libraries: `GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/legacy_credentials/sami@theorem.dev/adc.json`.

Never change the active configuration or the ADC file to get Sami's login: every session on the machine uses them.

## Expired login ("Reauthentication failed")

Sami's login expires about once each day. As the box you do not see this error.

1. If the command does not need Sami's login, run it without `--configuration theorem` or `GCLOUD_ACCOUNT=theorem`.
2. For a build or measurement machine, do not ask Sami to log in: use `az vm create` (resource group `sami`, location `westus2`), because `az` needs no daily login.
3. When a step needs Sami's login:
   - Start the login yourself; do not ask Sami to run it. In a terminal that stays open (`tmux`), run `gcloud auth login --no-launch-browser` with no `--configuration`, `GCLOUD_ACCOUNT`, `CLOUDSDK_ACTIVE_CONFIG_NAME` or `--cred-file`: any of those with a non-empty value stops the routing below. While `box` is the active configuration, `~/.dotfiles/shims/gcloud` routes this plain login to `theorem`, says so, and drops `--update-adc`, so the box's ADC stays its own identity. The login renews `legacy_credentials/sami@theorem.dev/adc.json`, the file client libraries use (above).
   - Do not run `gcloud auth application-default login` or `revoke` for this: the shim refuses both while the box's ADC is an `external_account` credential.
   - Find the open re-login ask (`dispatch search --query 'gcloud re-login'`) and post each printed link and the box error there. Open a new ask only if none is open: two asks split his answers. If it already holds another session's link, post no link; add one line naming the step you need.
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
2. If it fails again, compare the file's key with `gcloud compute instances get-guest-attributes <vm> --zone <zone> --query-path=hostkeys/`. If they differ, stop and send the user the VM name and both keys.

Never remove `~/.ssh/google_compute_known_hosts`: it holds every session's keys. Never use `StrictHostKeyChecking=no` or `accept-new`: they stop the check that the VM is the right one.
