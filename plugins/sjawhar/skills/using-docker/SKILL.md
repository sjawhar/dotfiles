---
name: using-docker
description: Use when running any docker, docker compose, buildx or depot command, or when one fails or hangs. Also use when editing a Dockerfile or compose file, or when a container won't start or an image may be stale. Also use on registry or auth errors, and before claiming Docker is unavailable.
---

# Using Docker

## Step zero: use the project's run path

- Before a hand-rolled `docker run`, use the repo's harness if it has one: `tl run`, a make target, a compose project, the CI workflow. An ad-hoc container tests a different environment.
- Docker, depot and registry access work here. To claim one is unavailable, or that the repo has no Docker setup, show a failing command's output. For the repo claim, also search the whole repo (`fd -HI 'Dockerfile|compose.*\.ya?ml'` from the root) and its CI workflows.

## Check, never guess

| Before you | Run |
|---|---|
| reuse a container ID from an earlier step | `docker ps` (it may have exited or auto-removed) |
| name an image tag or registry path | `docker images`, `docker manifest inspect` |
| pass `-f <compose file>` or name a service | find the file and read it |
| run a binary in a container | `docker exec <c> which <bin>` |
| describe where or how a build runs | `docker buildx ls`, `docker buildx inspect` |
| say Docker or its daemon is missing | `docker version` |
| say a build is or isn't running | read its process or log output |

## Lifecycle

- Do not use `-d --rm` when you may need the logs: a crash takes them with the container. Use `-d`, read the logs, then `docker rm`.
- `docker stop` returns before `--rm` removal ends, so a reused `--name` collides. Use unique names, or `docker rm -f` your own container.
- Copy files out of a CMD-less image with `docker create <img> true`. Bare `docker create` fails with "no command specified".
- Port unreachable: the app must bind `0.0.0.0` in the container, not `127.0.0.1`. Then check the `-p` host:container order.
- Misbehaving container: `docker exec -it <c> sh` and look inside (`ps`, the config, `curl localhost`) before you theorize.
- Stopping the `docker run` client (`timeout`, Ctrl-C, kill) does not stop a container whose PID 1 is a shell or script with no signal handler. Start such a container with `--init`, so that PID 1 handles the signal. Stop your own with `docker rm -f <name>`.

## `docker rm -f` that never returns

If `docker ps -a` answers and `docker inspect <id>` hangs, an unmount is stuck. A dead daemon fails both. It is IO, not seccomp: a dropped seccomp listener returns ENOSYS, a stuck unmount waits.

- Do not retry the removal. Each try parks another daemon thread.
- Do not restart the daemon. The lock holds only that container.
- Do not trust "healthy" from `docker ps`. If no pid's `/proc/<pid>/cgroup` names the container id, its init is gone.
- Check in `/proc`, do not guess. List the dockerd threads in state D, then read each one's `wchan` and `syscall`. Expect `syscall=166` (`umount2`) and `wchan=sync_inodes_sb`:

  ```bash
  for t in /proc/$(pgrep -x dockerd)/task/*; do awk '$3=="D"{print FILENAME}' $t/stat; done
  ```

## Builds

- Run the build. Tests do not replace it, and you are not done without it.
- Assume a warm cache: every agent and session shares this daemon and depot's remote cache. Never use `--no-cache` "to be safe", and never quote cold-cache times.
- Each builder has its own cache, and this machine has several. Know which builder you use: run `docker buildx ls`. `docker build`, `docker buildx build` and `depot build` can use different caches.
- Rebuild after every source edit, before you test.
- Downstream tools (Modal, compose) cache mutable tags such as `:main` by ref string. Pin digests when freshness matters.
- Know the expected build time before you start: full-image builds can hang for hours. Build in the background, check progress, stop at about 30 minutes and report.

## Blast radius

- Stop or remove only containers you started this session. Other agents share this daemon: never `docker kill` or `docker rm -f` theirs, and never sweep with `--filter ... | xargs docker rm`.
- Clean up your containers when you finish. Use `--rm` for true one-offs. Give every container a `--name`, so cleanup can target it.

## Registry credentials

- Never write `~/.docker/config.json`. It is Sami's file, and a bad write breaks pulls for every session. `docker login`, `crane auth login` and `docker-credential-*` helpers write it by default.
- Log in with the service token in a private config dir, never with Sami's login:

  ```bash
  DOCKER_CONFIG=$(mktemp -d) secrets GHCR_PULL_TOKEN -- crane auth login ghcr.io -u x --password-stdin
  # or: docker --config <tmpdir> login …
  ```

- On `denied` or `token does not match expected scopes`, fix your own scoped login, never his file. Report a shared-file credential that stopped working as a finding.

## Dockerfile authoring

Writing or reviewing a Dockerfile: read [references/dockerfile.md](references/dockerfile.md).
