---
name: dotfiles-conventions
description: "Use before you create, move or wire any file in ~/.dotfiles. That includes wrappers, shims, scripts, installers, tool config, symlinks, plugin consumption, and omp or claude harness setup. Use when tempted to symlink into vendor/ or a checkout, install from a local path, or add a directory. Use when you upgrade a shared tool. Use when you change a tool Sami operates himself (his skills, omp, dispatch, knives)."
---

# Dotfiles Conventions

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Everything committed to `~/.dotfiles` installs on every machine (devbox, laptop, agent hosts); a file that works only here breaks the others silently.

## Placement

Before you add a file, read its siblings and the AGENTS.md of its directory, and follow the pattern you find there.

| What you have | Where it goes |
|---|---|
| Wrapper: sets env or auth, then execs a binary | `shims/<binary name>`, one file |
| Script called by name | `~/.dotfiles/scripts/<name>`, no `.sh` |
| Tool setup (symlinks, clones, config patches) | `installers/<tool>.sh` on `installers/lib.sh` helpers, sourced from `install.sh` |
| Tool binary | A `mise.toml` pin, never a hand-placed file. `bin/` (gitignored) holds only standalone binaries like `mise` |
| Tool config | Its config dir (`omp/`, ...), linked into place by an installer. No `bin/` in a config dir |
| Third-party source, or an adopted external skill or agent | `vendor/<name>` through `ensure_vendor`, named in `skills-sources.json`. Never edit or add a file inside it |

## Portability (hard)

- No committed absolute paths: not in symlink targets, manifests or generated files. Use `$HOME`, `$DOTFILES_DIR` or relative paths.
- Committed symlinks are relative and stay in the repo. Do not link into `vendor/` or `plugins/` from elsewhere in the repo: copy the file, or point the consumer at the source dir.
- Installers and `~/.dotfiles/scripts/dotfiles-sync` build per-machine state at run time: checkout paths and any other value that differs per machine. Never commit it, or a manifest of it.

## Distribution (hard)

- Consume Sami's own software from GitHub, pinned: `github:sjawhar/x#ref` in `omp/plugins/package.json`, `"github:sjawhar/x" = "latest"` in `mise.toml`. Add the field the consumer needs (`omp`/`pi`, a plugin entry) to the source repo instead of wiring a local path.
- A local file path or a checkout symlink is for prototypes and never lands. The one exception: a `file://` plugin entry for a script in this repo.
- Fork releases: tag `v<upstream>-sami.<YYYYMMDD>-<HHMMSS>`, title `sami build <tag>`, consumers pin `latest`. Add each new own-repo to `minimum_release_age_excludes` in `mise.toml` by hand, or `latest` does not resolve.
- Never commit build artifacts (`dist/`) in a source repo. Fix the import in the source repo (export conditions, resolver) or ship a release asset.

## Wrapper parity (hard)

`~/.dotfiles/scripts/cld`, `~/.dotfiles/scripts/agentbox` and `shims/omp` must give each session the same environment: `GIT_CONFIG_*` including `gh-app-routes.gitconfig`, `shims/` first on `PATH`, secrets-broker registration via `agent-secrets-session`. A launcher missing one makes its sessions act as Sami on GitHub, lose Envoy messaging, or commit as the wrong author.

- Put a change all sessions need in `~/.dotfiles/scripts/agent-session` (which `shims/omp` and `cld` exec their agent through) and in `session_env()` of the agentbox script (the box's `docker run`).
- No launcher exports an Envoy or Dispatch variable: it would override every profile's `envoy.json`, and an agentbox container already reaches the host's Envoy through `agentbox/mounts` and its entrypoint relay.
- No launcher sets or clears a provider key. Keys are `apiKey` entries in each profile's `models.yml`.

## Skills here are repo-agnostic (hard)

- A skill here (all of `plugins/sjawhar/skills/`) runs in every repo Sami works in, so it names no repo, no single-repo path, and no project's workflow or task names.
- Put a project rule in the AGENTS.md or `.claude/skills/` of that project, and point the skill here at the repo's own AGENTS.md, skill, workflow or tool where one exists.
- An incident from one repo can show a rule. The rule must read correctly with the repo name removed.

## Changes to tools Sami operates

- Upgrade a tool in `mise.toml` or `install.sh` without asking, then say what changed and how to roll it back. Ask first only when active sessions must restart for the upgrade (bun, node, omp), because the restart interrupts their work.
- When you change a tool Sami operates himself (his skills, dotfiles, omp, dispatch, knives), tell him what each change will do, then what it did: he uses it while you change it.

## Commits and pushes

- Before you push, squash the session into 1-2 commits per topic, each saying what changed and why.
- Push only for a task about these files: a skill or config change Sami asked for, or a session whose role owns them. Leave an edit made on the way to other work in the working copy, and name it in your report.
- Overriding that: if `git status` shows changes not yours, or Sami says he is editing, leave yours uncommitted and name them.
