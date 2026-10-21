---
name: dotfiles-conventions
description: "Use before you create, move or wire any file in ~/.dotfiles. That includes wrappers, shims, scripts, installers, tool config, symlinks, plugin consumption, and omp or claude harness setup. Use when tempted to symlink into vendor/ or a checkout, install from a local path, or add a directory. Use when you upgrade a shared tool. Use when you change a tool Sami operates himself (his skills, omp, dispatch, knives)."
---

# Dotfiles Conventions

Everything committed to `~/.dotfiles` must work on every machine that installs it (devbox, laptop, agent hosts). A violation breaks the other machines silently.

## Placement

Before you add a file, read its siblings and the AGENTS.md of its directory. The pattern almost always exists.

| What you have | Where it goes |
|---|---|
| Wrapper: sets env or auth, then execs a binary | `shims/<binary name>`, one file |
| Script called by name | `scripts/<name>`, no `.sh` |
| Tool setup (symlinks, clones, config patches) | `installers/<tool>.sh` on `installers/lib.sh` helpers, sourced from `install.sh` |
| Tool binary | A `mise.toml` pin, never a hand-placed file. `bin/` (gitignored) is only for bootstrap |
| Tool config | Its config dir (`omp/`, ...), linked into place by an installer. No `bin/` in a config dir |
| Third-party source | `vendor/` through `ensure_vendor`, unchanged. Never graft onto it |
| Adopted external skill or agent | A verbatim copy (real files) in `plugins/` |

## Portability (hard)

- No committed absolute paths: not in symlink targets, manifests or generated files. Use `$HOME`, `$DOTFILES_DIR` or relative paths.
- Committed symlinks are relative and stay in the repo. A link from elsewhere in the repo to `vendor/` or `plugins/` content is usually wrong: copy verbatim or point the consumer at the source dir.
- Installers or `scripts/*-sync-*` build per-machine state at run time: link farms (`~/.omp/agent/skills`), checkout-backed paths, all that comes from what a machine has. Never commit it or a frozen manifest of paths.

## Distribution (hard)

- Consume Sami's own software from GitHub, pinned: `github:sjawhar/x#ref` in `omp/plugins/package.json`, `"github:sjawhar/x" = "latest"` in `mise.toml`. Give the source repo the manifest it needs (an `omp`/`pi` field, a plugin entry), not a local path.
- File paths and checkout symlinks are for prototypes and never land. The one exception: `file://` plugin entries for scripts in this repo.
- Fork releases: tag `v<upstream>-sami.<YYYYMMDD>-<HHMMSS>`, title `sami build <tag>`, consumers pin `latest`. Add each new own-repo to `minimum_release_age_excludes` in `mise.toml` by hand, or `latest` does not resolve.
- Never commit build artifacts (`dist/`) in a source repo. Fix resolution at the root (export conditions, resolver) or ship a release asset.

## Wrapper parity (hard)

`scripts/cld`, `scripts/agentbox` and `shims/omp` must give each session the same environment: the gh-app `GIT_CONFIG_*` routing include, `shims/` first on `PATH`, the secrets-broker identity. Drift makes sessions act as the user on GitHub, or lose messaging or commit attribution.

- Put a change that all sessions need in `scripts/agent-session`. On the host, `shims/omp` and `scripts/cld` exec their agent through it, and it execs in turn, so the pid stays.
- Put the same change in `session_env()` of `agentbox`. It builds that environment for `docker run`: one box per session, `shims/omp` as the main process.
- No launcher sets an Envoy or Dispatch variable. It would override the `envoy.json` of every profile. A box reaches the host file through `agentbox/mounts` and the host listener through its entrypoint relay.
- No launcher sets or clears a provider key. Keys are `apiKey` entries in the `models.yml` of each profile.

## Skills here are repo-agnostic (hard)

- A skill here (`opening-a-pr`, `landing-a-pr`, `disk-hygiene`, all of `plugins/sjawhar/skills/`) runs in every repo Sami works in. It names no project: no agent-c paths, lanes, workflow names or task vocabulary.
- Put a project rule in the AGENTS.md or `.claude/skills/` of that project. Point the generic skill at "the repo's own X" where one exists.
- An incident from one repo can show a rule. The rule must read correctly with the repo name removed.

## Changes to tools Sami operates

- Upgrade a shared tool without asking, then say what changed and how to roll it back. Ask first only when active sessions must restart for the upgrade (bun, node, the omp binary).
- When you change a tool Sami operates himself (his skills, dotfiles, omp, dispatch, knives), say what will happen and what happened as you act.

## Commits and pushes

- Before you push, consolidate the session into 1-2 described commits per topic, never a trail of per-step fragments.
- Push only for a task about these files: a skill or config change Sami asked for, or a standing role that owns them. Leave an edit made on the way to other work in the working copy, and name it in your report.
- If Sami is working in the repo, leave your changes in the working copy and say so.
