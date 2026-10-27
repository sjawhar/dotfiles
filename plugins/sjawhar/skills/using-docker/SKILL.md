---
name: using-docker
description: Use when running any docker, docker compose, buildx or depot command, or when one fails or hangs. Also use when editing a Dockerfile or compose file, or when a container won't start or an image may be stale. Also use on registry or auth errors, and before claiming Docker is unavailable.
---

# Using Docker

## One daemon, many sessions

Every agent on this machine shares one Docker daemon, its cache, and depot's remote build cache.

- Stop or remove only the containers you started in this session. `docker kill` or `docker rm -f` on another session's container destroys its work, and a sweep (`docker ps -q | xargs docker rm`) hits every session. Give each container a `--name`, and remove yours when you finish; `--rm` is safe only on a foreground run.
- Never pass `--no-cache`: it throws away a cache every session shares, and later builds miss too, because the layer ids they look for are gone. Force one stage with `--no-cache-filter=<stage>`. Never quote a cold-cache time as the build time.
- Each builder has its own cache, and `docker build`, `docker buildx build` and `depot build` can use different ones: run `docker buildx ls` to see which you are on.
- Never write `~/.docker/config.json`: every session pulls with it, and a bad write breaks them all. `docker login`, `crane auth login` and `docker-credential-*` helpers write it unless you send them elsewhere:

  ```bash
  # `secrets list` names the available tokens
  DOCKER_CONFIG=$(mktemp -d) secrets <TOKEN> -- docker login <registry> -u x --password-stdin
  ```

- On `denied` or a scope error, repeat that login with a token whose scope covers the image. If the shared credentials themselves stopped working, send the user the command and its output; do not edit the file.

## Check, never guess

Docker, depot and registry access work on this machine. Before you say one of them is unavailable, quote the output of the command you ran. Before you say the repo has no Docker setup, search it from the root (`fd -HI Dockerfile`, `fd -HI 'compose.*ya?ml'`) and read its CI workflows.

| Before you | Run |
|---|---|
| reuse a container id from an earlier step | `docker ps` (it may have exited or been auto-removed) |
| name an image tag or registry path | `docker images`, `docker manifest inspect` |
| pass `-f <compose file>` or name a service | read that file |
| run a binary in a container | `docker exec <c> which <bin>` |
| describe where or how a build runs | `docker buildx inspect` |
| say Docker or its daemon is missing | `docker version` |
| say a build is or isn't running | read its process (`ps aux \| grep build`) or its log; `docker ps` lists only containers |

## Builds

- Run the build before you report an image change done; tests do not replace it. Rebuild after every source edit that goes into the image.
- A full image build can run for hours, so find its expected time first (CI logs, the repo's docs), start it in the background and keep working. If it runs 30 minutes, tell the user its last log line and keep working while it runs.
- Modal and compose cache an image by its tag string, so a re-pushed `:main` is not pulled again. After you push, refer to the image as `<repo>@sha256:<digest>` (the digest `docker push` prints) wherever you run it.
- Run the project the way the repo does: its make target, compose service or CI step. Hand-roll a `docker run` only when the repo defines none, because an ad-hoc container tests a different environment.

## Containers

- Never pair `-d` with `--rm`: a crash takes the logs with the container. Use `-d`, read `docker logs`, then `docker rm`.
- `docker stop` returns before `--rm` removal finishes, so a reused `--name` collides. Name each container for your own session (`<task>-$$`); `docker rm -f` an old one only if this session started it.
- Start any `docker run` you may interrupt with `--init`: killing the client (`timeout`, Ctrl-C) leaves the container running when its PID 1 is a shell or script with no signal handler. Stop your own with `docker rm -f <name>`.
- Port unreachable: the app must bind `0.0.0.0` in the container, not `127.0.0.1`. Then check `docker port <c>` against your `-p host:container` argument.
- Container fails or answers wrongly: read `docker logs <c>`, then look inside with `docker exec <c> sh -c 'ps; curl -s localhost:<port>'` (no `-it`: an agent session has no TTY) before you propose a cause.
- Copy files out of an image with no CMD: `docker create --name <n> <img> true`, `docker cp <n>:<path> <dest>`, `docker rm <n>`. Without the `true`, `docker create` fails with "no command specified".

## Commands that never return

A `docker rm -f` or `docker inspect` still running after a minute: read [references/hangs.md](references/hangs.md) before you retry it or restart the daemon, because both make it worse.

## Dockerfile authoring

Writing or reviewing a Dockerfile: read [references/dockerfile.md](references/dockerfile.md).
