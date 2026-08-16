---
name: dotfiles-conventions
description: "Use before creating, moving, or wiring ANY file in ~/.dotfiles — wrapper scripts, install steps, config for a new tool, symlinks, plugin/extension consumption, or agent-harness setup (omp, opencode, claude). Also use when tempted to symlink into a vendor/ or repo checkout, install something from a local file path, or invent a new directory. The repo has a fixed placement map and hard portability rules that are not discoverable from one file. Triggers: dotfiles, ~/.dotfiles, shims, installers, omp config, opencode config, add a wrapper, laptop setup, where does this script go."
---

# Dotfiles Conventions

`~/.dotfiles` is installed on every machine (devbox, laptop, agents hosts). Everything committed must work on all of them. These rules exist because violating them breaks other machines silently.

## Read before writing

The layout is a fixed placement map. Before adding anything, read the sibling files and the directory's AGENTS.md — the pattern you need almost certainly exists:

| What you have | Where it goes |
|---|---|
| Wrapper around a binary (inject env/auth, then exec) | `shims/` — one file, named exactly like the binary it wraps |
| Utility script invoked by name | `scripts/` — no `.sh` extension |
| Setup for a tool (symlinks, clones, config patching) | `installers/<tool>.sh`, sourced from `install.sh`, built on `installers/lib.sh` helpers |
| Tool binary | `mise.toml` pin — never a hand-placed file. `bin/` is gitignored bootstrap-only |
| Config for a tool | Its config dir (`opencode/`, `omp/`, ...), symlinked into place by an installer. Config dirs never contain a `bin/` |
| Third-party source | `vendor/` via `ensure_vendor` — pristine, never grafted onto |
| Adopted external skill/agent | Verbatim copy into `plugins/` (real files) |

## Portability rules (hard)

- **No committed absolute paths.** Not in symlink targets, not in manifests, not in generated files. `$HOME`, `$DOTFILES_DIR`, and relative targets resolve at run time; `/home/ubuntu/...` breaks every other machine.
- **Committed symlinks: relative, intra-repo only.** A link to `vendor/` or `plugins/` content from elsewhere in the repo is usually wrong — copy verbatim or point the consumer at the source dir instead.
- **Per-machine state is built, not committed.** Link farms (`~/.omp/agent/skills`), checkout-backed paths, and anything derived from what exists on a machine get created by installers or `scripts/*-sync-*` scanning at run time. No frozen manifests of paths.

## Distribution rules (hard)

- **Sami's own software is consumed from GitHub, pinned.** The pattern is everywhere: `opencode.json` plugins (`name@git+https://github.com/sjawhar/x.git#tag`), `omp/plugins/package.json` (`github:sjawhar/x#ref`), `mise.toml` (`"github:sjawhar/x" = "latest"`). Extending it beats inventing: give the source repo the manifest it needs (an `omp`/`pi` field, a plugin entry) rather than wiring a local path.
- **File paths and checkout symlinks are prototyping only.** Fine while iterating; they never land. `file://` plugin entries are the one exception, and only for scripts that live in this repo.
- **Fork releases follow the opencode fork pattern**: tag `v<upstream>-sami.<YYYYMMDD>-<HHMMSS>`, release title `sami build <tag>`, consumer pins `latest`. New own-repos must be added to `minimum_release_age_excludes` in `mise.toml` by hand or `latest` will not resolve.
- **Committed build artifacts are rejected in source repos.** If a consumer needs a bundle, fix resolution at the root (export conditions, resolver) or ship via a release asset — do not commit `dist/`.

## Wrapper parity (hard)

Agent-harness launchers (`scripts/oc`, `shims/omp`) must provide the same session environment: gh-app `GIT_CONFIG_*` routing include, dotfiles `shims/` first on `PATH`, envoy env. A capability added to one belongs in both — drift means sessions silently act as the user on GitHub or lose messaging.

## Commit discipline

Consolidate a session's work into 1-2 described commits per topic before pushing — never a trail of per-step fragments. If Sami is actively working in the repo, leave changes in the working copy and say so.
