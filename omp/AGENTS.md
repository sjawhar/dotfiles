# omp

oh-my-pi (omp) configuration. The binary is mise-managed (`"github:sjawhar/oh-my-pi"` in `mise.toml`, a fork release built by the fork's `fork-release.yml`); the wrapper at `shims/omp` injects secretsd keys, envoy env, and the gh-app routing gitconfig, then execs it. `installers/omp.sh` wires everything below into `~/.omp/agent/`.

## Key files

- **`config.yml`** — main config: modelRoles, retry/fallback chains, task agentModelOverrides, tui settings (`resizeScrollback: rebuild`). Symlinked to `~/.omp/agent/config.yml`.
- **`models.yml`** — model catalog patches (xhigh thinking tiers). Symlinked alongside.
- **`mcp.json`** — MCP servers, launched through the `secrets` CLI so tokens never sit in config.
- **`agents/`** — agent definition files, all real files. Some originate in `plugins/sjawhar/agents/` or vendor repos; they are copied in, not linked. Update by re-copying deliberately.
- **`extensions/`** — dotfiles-owned extension source only (`jj-snapshot.ts`). Everything else reaches omp as an installed plugin (below), never as a path or symlink to a checkout.
- **`plugins/`** — the omp plugin tree, symlinked to `~/.omp/plugins`. `package.json` pins each plugin to a GitHub ref (`github:sjawhar/knives#<sha>`, `github:sjawhar/secretsd#<tag>`, ...) exactly like `opencode.json`'s plugin entries. `bun install` here materializes them; `omp plugin install`/`upgrade` manage the pins, and the resulting `package.json`/lockfile changes get committed.

## Runtime state built per machine (not in this repo)

`~/.omp/agent/skills/` and `~/.omp/agent/prompts/` are flat symlink farms built at install time by `scripts/omp-sync-skills` and `scripts/omp-sync-prompts`, which scan the curated sources (the same list as `opencode/plugins/dotfiles-bridge.ts`) at run time. Nothing about them is committed — no manifests, no frozen paths.

## Conventions

- My own repos (knives, secretsd, legion) expose their omp extensions via an `omp` manifest in the repo root `package.json` and are consumed from GitHub with a pinned ref. A path or symlink into a local checkout is a prototyping state, never a landed one.
- Nothing committed in this directory may contain an absolute path or reference a machine-local checkout.
- Config dirs hold config. Executables live in `shims/` (wrappers), `scripts/` (utilities), or come from mise — never in a `bin/` under a config dir.
- The wrapper (`shims/omp`) and `scripts/oc` must provide the same session environment: gh-app `GIT_CONFIG_*` routing, dotfiles shims first on `PATH`, envoy env. A capability added to one belongs in both.

## How changes take effect

Config files are symlinked, so edits apply on next omp start. Plugin pin changes need `bun install` in `plugins/` (or re-run `installers/omp.sh`). New fork releases: cut via `knives release` in `~/oh-my-pi`, tag `v<upstream>-sami.<YYYYMMDD>-<HHMMSS>` (the `sami build` pattern, same as the opencode fork), then `mise install` picks up `latest` — new own-repos must be listed in `mise.toml`'s `minimum_release_age_excludes` or `latest` will not resolve.
