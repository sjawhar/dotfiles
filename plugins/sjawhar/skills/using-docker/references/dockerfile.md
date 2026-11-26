# Dockerfile authoring

## Layers

- Copy the dependency manifests (`package*.json`, `uv.lock`, `Cargo.toml`) and install deps before you `COPY` the source. Then a source edit keeps the dependency layer.
- Give package managers a cache mount:

  ```dockerfile
  RUN --mount=type=cache,target=/root/.npm npm ci
  RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
  ```

- Keep a `.dockerignore` that excludes `.git`, dependency dirs, build artifacts and env or secret files. A large context slows builds and can leak secrets into the image.
- Multi-stage: the build stage holds the toolchain. The runtime stage copies only artifacts (`COPY --from=build`), onto the smallest base (slim, alpine, distroless) on which the image's tests or smoke run still pass.
- Remove package caches in the same `RUN` layer (`rm -rf /var/lib/apt/lists/*`). A later layer can't shrink an earlier one.

## ARG, ENV and secrets

- `ARG` exists only at build time. Put runtime config the app reads in `ENV` or `-e`. An `ARG DATABASE_URL` is unset in the running container.
- Put secrets in neither. `docker history` keeps both (an `ARG` once a layer uses it). Use a secret mount, then search the history:

  ```dockerfile
  RUN --mount=type=secret,id=api_key \
      API_KEY=$(cat /run/secrets/api_key) some-build-step
  ```

  ```bash
  docker history --no-trunc <img> | grep -iE 'secret|key|password|token'
  ```

## Runtime

- Run as non-root: a user with explicit UID/GID, `USER` before `CMD`, `COPY --chown`.
- Use exec-form `CMD`/`ENTRYPOINT` (`["bin", "arg"]`). With shell form, PID 1 gets no signals, so `docker stop` takes exactly 10 s and then sends SIGKILL.
- Entrypoint scripts need the exec bit, a correct shebang and LF line endings. If the container exits at once, run `docker logs <c>` and `docker inspect --format '{{.Config.Cmd}} {{.Config.Entrypoint}}' <img>`.
- If compose uses `depends_on: condition: service_healthy`, add a `HEALTHCHECK`. Its probe binary (curl, wget) must be in the image, or the check never passes. Install it, or probe with a binary the image has.
- A file is missing in the container: `.dockerignore` excluded it, or the image is stale. Check `.dockerignore`, then rebuild.
