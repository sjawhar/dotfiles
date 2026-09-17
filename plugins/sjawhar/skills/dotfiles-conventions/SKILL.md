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

Agent-harness launchers (`scripts/oc`, `scripts/agentbox`, `shims/omp`) must provide the same session environment: gh-app `GIT_CONFIG_*` routing include, dotfiles `shims/` first on `PATH`, envoy env. A capability added to one belongs in all of them — drift means sessions silently act as the user on GitHub or lose messaging. `agentbox` builds that environment for `docker run` (one box per session, `shims/omp` as the container's main process), so a change to what a session needs goes into `session_env()` there and into the shim.

## Skills here are repo-agnostic (hard)

A skill in this repo (`opening-a-pr`, `landing-a-pr`, `disk-hygiene`, all of `plugins/sjawhar/skills/`) runs in every repo Sami works in, so it names no project: no agent-c paths, lanes, workflow names, or task vocabulary. A project's rule lives in that project's AGENTS.md or its own `.claude/skills/`, and the generic skill points at "the repo's own X" where one exists (Sami, #384, 2026-09-06: "landing-a-pr and opening-a-pr are repo-agnostic, so we shoudln't put agent-c specific stuff in there"; Dispatch 5a4422a7, 2026-09-12: "Just make a (generic, non-agent-c) skill for it my dotfiles and I'll invoke it as needed"). A dated incident from one repo may illustrate a generic rule; the rule itself must still read correctly with that repo's name removed.

## Commit discipline

Consolidate a session's work into 1-2 described commits per topic before pushing — never a trail of per-step fragments (Sami, #1148, 2026-09-09: "IT really fucking pisses me off that agents keep doing this fucking bullshit where I have 50 tiny fucking commits in 24 hours"). A push here belongs to a task that is about these files (a skill or config change he asked for, or a standing role that owns them, such as the librarian's skill-mechanics lane); a dotfiles edit made on the way to something else stays in the working copy and is named in your report (Sami, #384, 2026-09-06: "I really wish you fucking agents would stop making unauthorized pushes to my dotfiles"; #1054, 2026-09-09: "what part of my skills or instruction or anything are telling you that... you should be trying to commit in my dotfiles repo?"). If Sami is actively working in the repo, leave changes in the working copy and say so.
