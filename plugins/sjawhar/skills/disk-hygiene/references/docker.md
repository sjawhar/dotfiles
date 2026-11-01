# Phase 5: Docker

`S` and `SKILL` are as set in SKILL.md. Do the steps in order.

1. If `docker system df` fails with `rw layer snapshot not found for container X`, run `docker rm -f X` and run it again. Never use `docker system df -v`: it stalls under IO load. Use `$S images` and `$S containers`.
2. `docker image prune -f` (dangling only). Always safe.
3. Buildkit: `docker buildx du --builder B`.
   - Large Total with 0 B Reclaimable means leaked leases, and a `--filter until=24h` prune frees nothing. First make sure that no build runs (`docker top buildx_buildkit_B0`, host `docker build`/`buildx`/`depot`). Then `docker restart buildx_buildkit_B0` and `docker buildx prune -a -f --builder B`.
   - Find the default builder (`docker buildx ls`, the `*`) before you delete a builder.
   - Size the volumes before you blame test fixtures. Fixtures are rarely more than a few GB.
   - The standard cut is `docker buildx prune --builder B --keep-storage 20GB`. For a builder that grows tens of GB/day, give it a gc policy in a BuildKit daemon config (`docker buildx create --buildkitd-config FILE`) and report it, because a prune frees space for only hours.
   - A dev-slot `deploy.py up` that builds its images leaves 25-40 GB per attempt. Reused published images leave a few GB. Check which before you decide the minimum free disk a box needs for dev-slot proofs. After a dev-slot proof, prune both builders and run `docker image prune -a` on the box daemon.
4. Tagged images: `python3 $S images --older-than-hours 48`. Delete each with `docker rmi REF`, without `-f`: a refusal means a container uses it. Leave younger ones for their owner. A local build (no registry prefix) belongs to its builder and can take an hour to rebuild.
   - **Neither size field answers "what do I get back".** `.Size` is cumulative and counts shared layers, and `Reclaimable` in `docker system df` is an upper bound that assumes nothing is shared. Four images displayed as 21.2 GB returned 6.2 GB, because they shared almost everything with one base. Measure `docker system df` before and after, and report that difference.
   - **One image, two tags, is one image.** Compare `docker image inspect --format '{{.Id}}' <ref>` across the pair: the same Id means untagging one frees nothing, and a tally counting tags overstates a reclaim by about double. Six of twelve large entries on this box were such pairs.
   - **`rmi` that prints only `Untagged:` and no `Deleted:` freed nothing.** Read its output rather than its exit status.
   - **Never delete an image you did not pull.** One on this box was the only reachable carrier of a toolchain whose tag has no public release, so removing it would have cost an eval its toolchain rather than a rebuild.
   - **A container's bind-mounted output appears in no docker measurement.** A container whose writable layer was 36.9 kB wrote tens of gigabytes through a `-v` onto the shared volume. Ask what your container writes to and how much, measure that directory, and cap it from inside the container.
5. Containers: `python3 $S containers` gives a family, owner and verdict ([containers.md](containers.md)). Labels attribute; they never decide liveness. Delete only what the verdict names, and run the census again before `rm`.
   - **local-stack** `idle`: leave it and message the owner.
   - **ryuk**, running: never delete it by hand. Find its client with `docker port <ryuk>` and `ss -tnp`. If that is an orphaned pytest, kill it, and ryuk cleans up.
   - **tc-fixture**: delete without its ryuk only at `orphan-no-ryuk`. Never delete a fixture whose ryuk exists: the ryuk removes it, and a manual delete can collide with that removal.
   - **agentbox**: never.
   - **other**: delete only eval or task sandboxes, never service containers (postgres, nats, registries, envoy, buildx). A sandbox is an orphan when no eval process exists and nothing names it, or when its only holder is a dangling `docker exec -it ... bash`. Kill the shell, then `docker compose -p PROJECT down -v`. If a live session can be the owner, ask first.
6. `docker volume prune -f` (dangling only, attached to no container). Always safe.
