# Phase 5: Docker

Read this in Phase 5, and before you delete a tree in which a container left root-owned files. `S` is set in SKILL.md. Do the steps in order.

1. If `docker system df` fails with `rw layer snapshot not found for container X`, run `docker rm -f X` and run it again. Never use `docker system df -v`: it stalls under IO load. Use `$S images` and `$S containers`.
2. `docker image prune -f` (dangling only). Always safe.
3. Buildkit: `docker buildx du --builder B`.
   - A Total over 20 GB (the standard cut below) with 0 B Reclaimable means leaked leases, and a `--filter until=24h` prune frees nothing. Make sure no build runs (`docker top buildx_buildkit_B0`, host `docker build`/`buildx`/`depot`), then `docker restart buildx_buildkit_B0` and `docker buildx prune -a -f --builder B`.
   - Find the default builder (`docker buildx ls`, the `*`) before you delete a builder.
   - Size the volumes before you blame test fixtures: fixtures are rarely more than a few GB.
   - The standard cut is `docker buildx prune --builder B --keep-storage 20GB`. A builder that grows tens of GB a day needs a gc policy in a BuildKit daemon config (`docker buildx create --buildkitd-config FILE`); report it, because a prune frees space for only hours.
   - A dev-slot deploy is a project's `deploy.py up` on an agent box's own Docker daemon. One that builds its images leaves 25-40 GB of build cache per attempt; one that reuses published images leaves a few GB. Check which it does before you decide the minimum free disk a box needs for such deploys. After one, prune each builder that `docker buildx ls` lists on the box daemon, and run `docker image prune -a` there.
4. Tagged images: `python3 $S images --older-than-hours 48`. Delete each with `docker rmi REF`, without `-f`: a refusal means a container uses it. Leave younger ones to their owner.
   - `.Size` counts shared layers, and `Reclaimable` assumes nothing is shared. Report the difference of `docker system df` before and after. Where the daemon stores images under containerd, the bytes are in `/var/lib/containerd`.
   - One image under two tags is one image: compare `docker image inspect --format '{{.Id}}' <ref>`. An `rmi` that prints only `Untagged:` and no `Deleted:` freed nothing.
   - Delete what you built. Delete an image from a shared registry, even one you pulled, only when `docker ps -a --filter ancestor=<id>` lists no container and a grep of the checkouts on the box for its digest and tag (pipeline scripts, lock files) finds nothing. The first check says whether it is used now, not whether another session will pull it, and the image can be the only reachable copy of a toolchain. A local build (no registry prefix) can take its builder an hour to rebuild.
   - An unauthenticated registry read cannot tell a private tag from a missing one, so do not read its answer as "the image is irreplaceable" or as "the tag is gone".
5. Containers: `python3 $S containers` gives each a family, an owner and a verdict; read `cmd_containers` in `$S` for how it sets each verdict. Labels attribute; they never decide liveness. Trust its client check only on a host whose `docker ps` lists the container. Delete only what the verdict allows, and run the census again before `rm`:

   | Family and verdict | Action |
   | --- | --- |
   | local-stack `idle` | Leave it and message the owner. |
   | ryuk, running | Never delete it by hand; it removes its fixtures about 10 s after its client disconnects. Find the client with `docker port <ryuk>` and `ss -tnp`; if that is an orphaned pytest, kill it. |
   | ryuk, `stranded` (created, never started) | Remove it together with the fixtures labelled with its `org.testcontainers.session-id`. |
   | tc-fixture | Delete without its ryuk only at `orphan-no-ryuk`. A manual delete of a fixture whose ryuk exists can collide with the ryuk's removal. |
   | agentbox | Never. |
   | other | Delete only eval or task sandboxes, never service containers (postgres, nats, registries, envoy, buildx). A sandbox is an orphan when no eval process exists and nothing names it, or when its only holder is a dangling `docker exec -it ... bash`: kill the shell, then `docker compose -p PROJECT down -v`. If a live session can be the owner, ask it first. |

   - A container's bind-mounted output appears in no docker measurement: measure the directory it writes to, and cap that output from inside the container.
6. `docker volume prune -f` (dangling only, attached to no container). Always safe.

## Root-owned files a container left on a bind mount

`rm -rf` as your user fails on each root-owned file, so the tree looks deleted at the top and is not. Remove it with a throwaway container on the same mount, `docker run --rm -v <parent>:/w alpine:3 rm -rf /w/<tree>`, under `systemd-run --user --wait` when you cannot show that it ends inside your tool call's timeout, then `ls` the path to confirm it is gone.
