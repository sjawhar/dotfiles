---
name: using-docker
description: Use when running ANY docker, docker compose, or buildx command — building images, starting or debugging containers, reading logs, cleaning up — and when a docker command fails. Also triggers on Dockerfile or compose file edits, "container won't start", suspected stale images, registry/auth errors, and any temptation to claim Docker infrastructure is unavailable.
---

# Using Docker

Rules mined from 90 days of real agent failures on this machine. The theme: agents guess where they must verify, and improvise where a harness exists.

## Step zero: use the project's run path

Before any hand-rolled `docker run`, find the repo's real harness — `tl run`, a make target, a compose project, the CI workflow. If one exists, use it. An ad-hoc container reproduces a *different* environment; its results don't transfer.

Docker, depot, and registry access all work here. Every past "sandbox infra not available" claim was wrong. Same rule for "this repo has no Docker setup": an absence claim requires a repo-wide search (`fd -HI 'Dockerfile|compose.*\.ya?ml'` from the root, plus CI workflows) and a failing command's output — not a glance at the repo root.

## Never guess — verify

| Before you... | Run |
|---|---|
| reuse a container ID from an earlier step | `docker ps` — containers exit or auto-remove between steps (#1 failure: 62 sessions) |
| reference an image tag or registry path | `docker images` / `docker manifest inspect` — never invent refs |
| pass `-f <compose path>` or name a service | find the file (`fd 'compose.*\.ya?ml'`) and read it |
| exec a binary inside a container | `docker exec <c> which <bin>` — wget/kubectl/your-tool may not exist there |
| explain where/how a build executes | `docker buildx ls` / `docker buildx inspect` — no inferred topology claims |
| claim Docker isn't installed or a daemon isn't running | `docker version` |
| assume a build is (or isn't) still running | look at the actual process/log output, not your memory of launching it |

## Lifecycle

- Never combine `-d --rm` for anything whose logs you might want — on crash, the logs vanish with the container. Use `-d`, read logs, `docker rm` when done.
- Reusing `--name`: `docker stop` returns before `--rm` removal finishes and the next `run --name` collides. `docker rm -f <name>` (your own only) or use unique names.
- Extracting files from a CMD-less image: `docker create <img> true` (dummy command) — bare `docker create` fails with "no command specified".
- Can't reach a published port: the app must bind `0.0.0.0` inside the container, not `127.0.0.1`; then check the `-p` mapping.
- Container misbehaving? `docker exec -it <c> sh` and look from the INSIDE — `ps`, `ls`, `cat` the config, `curl localhost` — before theorizing from the outside.

## Build cache: warm until proven cold

Just because it's the first time YOU run a build doesn't mean it's the first time the build has been run. This daemon and depot's remote cache are shared across every agent and session — assume warm. Never `--no-cache` "to be safe", and don't quote cold-cache time estimates.

Caches are per-builder, so know which one you're on: `docker buildx ls` (this box has several — `default`, stray docker-container builders, depot's). `docker build`, `docker buildx build`, and `depot build` can all hit different caches.

## Stale images

The inverse mistake: after editing source, the image still contains the old code — rebuild before testing, every time. Mutable tags (`:main`, `:task-X-local`) get cached by ref string downstream (Modal, compose); pin digests when freshness matters.

## Blast radius

Kill/remove only containers YOU started this session. Never `docker kill` / `docker rm -f` someone else's, never broad `--filter ... | xargs docker rm` sweeps — other agents' sandboxes share this daemon. Clean up your own on completion: forgotten containers accumulate for weeks (today's `docker container ls -a`: a nameless one up 7 days) and this box has hit 682 GB of image clutter. Use `--rm` for true one-offs, and always `--name` what you start so cleanup is targeted.

## Long builds

Know the expected build time before launching — full-image bakes have wedged 4+ hours. Run in background with progress checks and a ~30 min cap, then report; never sit blind on a foreground build.

## Rationalization table

| You're thinking | Reality |
|---|---|
| "Docker/depot isn't available here" | It is. Show the failing command or drop the claim. |
| "Building the image is optional, tests cover it" | "Running Docker builds is not optional. If you haven't done it, you're not done." |
| "A quick ad-hoc `docker run` reproduces enough" | Use the harness. Ad-hoc runs test a different environment. |
| "The container ID from earlier is still valid" | 62 sessions thought so. `docker ps` first. |
| "I'll sweep up all the matching containers" | Some are other agents'. Touch only your own. |
| "Fresh checkout, so the cache is cold" | The cache outlives you. Check before estimating or `--no-cache`-ing. |

## Dockerfile authoring

Writing or reviewing a Dockerfile → [references/dockerfile.md](references/dockerfile.md) (layer caching, multi-stage, ARG vs ENV, secrets, non-root, debugging table).
