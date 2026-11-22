---
name: using-docker
description: Use when running docker, docker compose, buildx or depot, or when one fails or hangs, a container won't start or an image may be stale. Also use when editing a Dockerfile or compose file, publishing a port with -p, on a registry or auth error, or before you say Docker is unavailable.
---

# Using Docker

## One daemon, many sessions

Every agent on the machine shares one Docker daemon, its build cache, and depot's remote cache.

- Stop or remove only containers this session started. Give each a `--name` (`<task>-$$`) and remove it when you finish. Never sweep (`docker ps -q | xargs docker rm`).
- Never pass `--no-cache`: it rebuilds every stage from scratch, which can take hours on a daemon and disk every session shares.
- Force one stage with `--no-cache-filter=<stage>`.
- Do not quote a cold-cache time as the build time.
- `docker build`, `docker buildx build` and `depot build` can use different builders and caches: `docker buildx ls` shows which one you are on.
- Never write `~/.docker/config.json`: every session pulls with it. `docker login`, `crane auth login` and `docker-credential-*` helpers write it unless you point them elsewhere:

  ```bash
  export DOCKER_CONFIG=$(mktemp -d)   # later docker calls in this shell use it
  secrets <KEY> -- sh -c 'printf %s "$<KEY>" | docker login <registry> -u x --password-stdin'
  ```

  - `secrets list` names the keys.
  - On `denied` or a scope error, log in again with a token whose scope covers the image.
  - If the shared credentials stopped working, send the user the command and its output; do not edit the file.

## Check, never guess

Docker, depot and registry access work on this machine; before you say one is unavailable, quote the output of the command you ran. Before you say a repo has no Docker setup, search from its root (`fd -HI Dockerfile`, `fd -HI 'compose.*ya?ml'`) and read its CI workflows.

| Situation | Check |
| --- | --- |
| Reusing a container id from an earlier step | `docker ps` first: it may have exited or been removed |
| Naming an image tag or registry path | `docker images`, `docker manifest inspect` |
| Passing `-f <compose file>` or naming a service | Read that file |
| Running a binary in a container | `docker exec <c> which <bin>` |
| Describing where or how a build runs | `docker buildx inspect` |
| Saying Docker or its daemon is missing | `docker version` |
| Saying a build is or is not running | Read its process or its log; `docker ps` lists only containers |

## Builds

- Run the build before you call an image change done, and again after every source edit that goes into the image; tests do not replace it.
- A full build can take hours: find its usual time (CI logs, the repo's docs), run it in the background and keep working. If it still runs after 30 minutes, tell the user its last log line and keep working.
- Modal and compose cache an image by its tag, so a re-pushed `:main` is not pulled again. After a push, refer to the image as `<repo>@sha256:<digest>` (the digest `docker push` prints).
- Run the project the way the repo does (its make target, compose service or CI step). Hand-roll `docker run` only when the repo defines none.

## Containers

- Never pair `-d` with `--rm`: a crash takes the logs with it. Use `-d`, read `docker logs`, then `docker rm`.
- `docker stop` returns before `--rm` removal finishes, so a reused `--name` can collide.
- Start a `docker run` you may interrupt with `--init`: killing the client (`timeout`, Ctrl-C) leaves the container running when PID 1 has no signal handler. Stop yours with `docker rm -f <name>`.
- Port unreachable: the app must bind `0.0.0.0` inside the container; then compare `docker port <c>` with your `-p`.
- Publish to `127.0.0.1:8080:8080`. `-p 8080:8080` publishes on every host address, the public one included. Docker's rules run before the host firewall, so `ufw deny` does not close it.
- Serve our own people and machines over the VPN (`tailscale serve`).
- Publish publicly only for a named outside caller that cannot join the VPN.
- Name each outside caller you publish publicly for in the repo's docs.
- Docker Engine before 28.0 lets hosts on the local network reach even a loopback-published port, through 127.0.0.1 and through the container's own address on the Docker bridge network. Check `docker version`.
- On an engine before 28.0, this rule in the `DOCKER-USER` chain, which Docker runs before its own rules, closes only the 127.0.0.1 route: `sudo iptables -I DOCKER-USER -m conntrack --ctorigdst 127.0.0.0/8 -j DROP`.
- The full fix is to update the engine to 28.0 or later. Ask the user first: the update restarts the daemon and every session's containers.
- A container fails or answers wrongly: read `docker logs <c>`, then `docker exec <c> sh -c 'ps; curl -s localhost:<port>'` (no `-it`: an agent session has no TTY) before you propose a cause.
- A container exits at once: read `docker logs <c>` and `docker inspect --format '{{.Config.Cmd}} {{.Config.Entrypoint}}' <img>`. An entrypoint script needs the exec bit, a correct shebang and LF line endings.
- A file is missing in the container: `.dockerignore` excluded it, or the image is stale. Check `.dockerignore`, then rebuild.
- Copy files out of an image with no CMD: `docker create --name <n> <img> true`, `docker cp <n>:<path> <dest>`, `docker rm <n>`. Without `true`, `docker create` fails with "no command specified".

## Dockerfiles

Read `references/dockerfile.md` when you write or review a Dockerfile.

## A `docker rm -f` or `docker inspect` that never returns

If `docker ps -a` still answers, the daemon is alive and is stuck in the kernel unmounting that container's filesystem. Seccomp is not the cause: a dropped seccomp listener returns ENOSYS at once, while a stuck unmount waits. Confirm the stuck unmount (expect `wchan=sync_inodes_sb`, `syscall=166`, which is `umount2`):

```bash
for t in /proc/$(pgrep -x dockerd)/task/*; do
  awk '$3=="D"{f=1} END{exit !f}' "$t/stat" &&
    echo "$t wchan=$(cat "$t/wchan") syscall=$(sudo -n cut -d' ' -f1 "$t/syscall")"
done
```

- Do not retry the removal: each try blocks another daemon thread. Do not restart the daemon: it stops every session's containers.
- `docker ps` can still say "healthy": `grep -l <container-id> /proc/[0-9]*/cgroup` printing nothing means its init is gone.
- It clears when the unmount finishes, or at a reboot. Send the user the container id and the thread line, leave the container, and go on with the rest of your task.
