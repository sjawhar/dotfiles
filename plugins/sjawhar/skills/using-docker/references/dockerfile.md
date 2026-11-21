# Writing or reviewing a Dockerfile

## Layers

- Copy the dependency manifests (`package*.json`, `uv.lock`, `Cargo.toml`) and install dependencies before you `COPY` the source, so a source edit keeps the dependency layer.
- Give each package manager a cache mount:

  ```dockerfile
  RUN --mount=type=cache,target=/root/.npm npm ci
  RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
  ```

- Keep a `.dockerignore` that excludes `.git`, dependency dirs, build artifacts and env or secret files: the build context can leak them into the image.
- Build in stages: the build stage holds the toolchain, and the runtime stage copies only the artifacts (`COPY --from=build`) onto the smallest base (slim, alpine, distroless) on which the image's tests or smoke run still pass.
- Remove package caches in the same `RUN` layer that wrote them (`rm -rf /var/lib/apt/lists/*`): a later layer cannot shrink an earlier one.

## ARG, ENV and secrets

- `ARG` exists only at build time; config the running app reads goes in `ENV` or `-e`.
- Put secrets in neither `ARG` nor `ENV`: `docker history` keeps both. Use `RUN --mount=type=secret,id=<id>` and read `/run/secrets/<id>`, then check `docker history --no-trunc <img>`.

## Runtime

- Use exec-form `CMD`/`ENTRYPOINT` (`["bin", "arg"]`); in shell form PID 1 gets no signals and `docker stop` waits 10 s, then kills.
- Run as non-root: a user with explicit UID/GID, `USER` before `CMD`, `COPY --chown`.
- A `HEALTHCHECK` (needed for compose `depends_on: condition: service_healthy`) must probe with a binary the image has.
