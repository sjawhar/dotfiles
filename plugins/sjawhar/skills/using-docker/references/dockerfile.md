# Dockerfile authoring

## Layer caching

- Copy dependency manifests first (`package*.json`, `uv.lock`, `Cargo.toml`), install deps, THEN `COPY` source — source edits must not invalidate the dependency layer.
- Cache mounts for package managers:

  ```dockerfile
  RUN --mount=type=cache,target=/root/.npm npm ci
  RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
  ```

- `.dockerignore` must exist and exclude `.git`, dependency dirs, build artifacts, env/secret files — a bloated context slows every build and can leak secrets into the image.
- Multi-stage: build stage owns the toolchain; runtime stage copies artifacts only (`COPY --from=build`).

## ARG vs ENV — the #1 recurring confusion

| Declared as | Visible at build | Visible at runtime | In `docker history` |
|---|---|---|---|
| `ARG` | yes | **no** | yes (if used in a layer) |
| `ENV` | yes | yes | yes |

- Runtime config the app reads → `ENV` (or runtime `-e`). `ARG DATABASE_URL` silently produces an unset variable in the running container.
- Secrets → **neither**. Both persist in image history. Use:

  ```dockerfile
  RUN --mount=type=secret,id=api_key \
      API_KEY=$(cat /run/secrets/api_key) some-build-step
  ```

- Then verify the fix actually worked:

  ```bash
  docker history --no-trunc <img> | grep -iE 'secret|key|password|token'
  ```

## Correctness checklist

- [ ] Non-root: create user with explicit UID/GID, `USER` before `CMD`, `COPY --chown`
- [ ] Exec-form `CMD`/`ENTRYPOINT` (`["bin", "arg"]`) — shell form blocks signals from reaching PID 1, so `docker stop` waits 10s then SIGKILLs
- [ ] Entrypoint scripts are executable and have correct shebang/line-endings
- [ ] `HEALTHCHECK` present if compose uses `depends_on: condition: service_healthy` — and its probe binary (curl/wget) actually exists in the image
- [ ] Package caches cleaned in the SAME `RUN` layer (`rm -rf /var/lib/apt/lists/*`) — a later layer can't shrink an earlier one
- [ ] Runtime base as small as correctness allows (slim/alpine/distroless); build tools never in the runtime stage

## Runtime debugging

| Symptom | Likely cause | Fix |
|---|---|---|
| Can't connect to published port | App binds `127.0.0.1` inside container | Bind `0.0.0.0`; verify `-p` host:container order |
| Env var empty at runtime | Declared as `ARG` | `ENV`, or pass `-e` at run time |
| Container exits immediately | Bad CMD, non-executable entrypoint, CRLF line endings | `docker logs <c>`; `docker inspect --format '{{.Config.Cmd}} {{.Config.Entrypoint}}' <img>` |
| `docker stop` takes exactly 10s | Shell-form CMD swallows SIGTERM | Exec-form CMD/ENTRYPOINT |
| Healthcheck always unhealthy | Probe binary missing from image | Install it, or probe with what exists |
| File missing inside container | `.dockerignore` excluded it, or stale image | Check `.dockerignore`; rebuild |
