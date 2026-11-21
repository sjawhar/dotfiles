# Envoy

- Type check, from this directory: `npm ci && npx tsc --noEmit`.
- Apply a stack only from its own machine: `prod` on the devbox, `laptop` on the laptop. A machine with no `sshHost` is deployed through the Docker socket of the machine that runs Pulumi, so an apply from another machine starts that listener there.
- Set a secret with `pulumi config set --secret`: without `--secret` Pulumi writes the value in plain text to `Pulumi.<stack>.yaml`, and this repository is public.

Where this file and the code disagree, the code is right: follow the code and correct this file.
