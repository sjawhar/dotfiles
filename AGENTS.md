# Dotfiles

Sami's development environment: shell, git, jj, tmux and editor configuration, the agent harnesses' configuration, the shims and scripts every agent session on his machines runs, and the installers that set a machine up. It targets Linux machines and bash; `laptop/` holds the laptop's own configuration.

## Layout

- Root configuration (`.bashrc`, `.gitconfig`, `.jjconfig.toml`, `.tmux.conf`, `mise.toml`, `starship.toml`, `nvim/`, `.claude/`): linked into place by the installers.
- `install.sh` and `installers/`: machine setup. `devbox/`, `laptop/`, `oryx/`, `phone/`, `raspberry-pi/`, `vm/`: the setup and units of one machine or role each.
- `shims/`: wrappers that shadow a binary on `PATH`. `scripts/`: commands, on `PATH`. `bin/` (gitignored): standalone binaries such as `mise`.
- `omp/`: oh-my-pi configuration. `plugins/`: skills, agents and commands. `skills-sources.json`: the skill pools omp reads.
- `agentbox/`, `agent-secrets/`, `envoy/`, `forward/`, `chathub/`, `chief-dashboard/`: services and tools.
- `vendor/` (gitignored): third-party clones the installers make. `docs/`: plans and notes.

## Package guides

Read the guide of each directory you change:

- `agentbox/AGENTS.md`: agent boxes (`scripts/agentbox` and its subcommands), box storage and the home allow-list.
- `agent-secrets/AGENTS.md`: the secrets broker's host helper, and how sessions and boxes get a broker identity.
- `envoy/AGENTS.md`: the Envoy listener deployment, and where a session's Dispatch and Envoy credentials come from.
- `installers/AGENTS.md`: writing and running installers.
- `omp/AGENTS.md`: omp configuration, extensions, models, profiles and plugin pins.
- `plugins/AGENTS.md`: skills, agents and commands, and how a change reaches the agents.
- `scripts/AGENTS.md`: the commands and scheduled watches in `scripts/`.
- `shims/AGENTS.md`: the rules for changing a shim, and the leak-term list.
- `devpod/AGENTS.md`: a dormant bare-VM cloud-init. `chief-dashboard/README.md`: the streams dashboard. `README.md`: the laptop and oryx.

## Install

- `bash install.sh` runs the installers it lists, in order; an installer it does not list does not run from it. A machine role runs its own (`devbox/install.sh`, `laptop/install.sh`, `oryx/install.sh`). Every installer is safe to run twice.
- Configuration files are symlinked into place, never copied. `ensure_link` fails rather than overwrite a file that differs from the copy here; reconcile that file by hand.
- `installers/shell.sh` makes the first line of `~/.bashrc` source `~/.dotfiles/.bashrc`. That file has two zones: environment (`PATH`, mise, variables) above the `[[ $- == *i* ]] || return 0` guard, interactive setup (aliases, completions, prompt) below it.

## Adding or upgrading a tool

1. Pin its version in `mise.toml`; no floating versions. A repository of Sami's also goes in `minimum_release_age_excludes` there, by hand.
2. Setup beyond mise (links, configuration patches) goes in `installers/<tool>.sh`, sourced from `install.sh` or run from a role's install script.
3. Environment goes in `.bashrc` above the interactive guard; aliases and functions below it. Completions are generated in `install.sh`.
4. Upgrade a pin without asking, then say what changed and how to roll it back. Ask Sami first when running sessions must restart for it (bun, node, omp).

## Conventions

- **Everything committed is portable.** No committed file holds an absolute path: use `$HOME`, `$DOTFILES_DIR` or a relative path. A committed symlink has a relative target inside the repository, never in gitignored `vendor/`. Per-machine links and paths are made by the installers at install time, never committed.
- Sami's own software installs from GitHub at a pinned tag or SHA (`mise.toml`, `omp/plugins/package.json`). A local path or a symlink to a checkout is for a prototype and never lands; the one exception is a `file://` plugin entry for a script in this repository. When a consumer needs something from the source repository (an `omp` manifest, an export, a release asset), change that repository; never commit its build output.
- Placement: `shims/` holds wrappers and one data file, `shims/person-names`; `scripts/` holds commands (no `.sh` on a command run by name); `installers/` setup; `bin/` binaries only; a configuration directory holds configuration only. Third-party code is cloned into `vendor/` by `ensure_vendor` and never edited there. Read a directory's sibling files before you add one.
- `shims/omp` and `scripts/cld` (`cld [--profile NAME]`, Claude Code) exec the agent through `scripts/agent-session`, the session environment every launcher shares; its header lists each part. `session_env()` in `scripts/agentbox` builds a box's. A change every session needs goes in both: a launcher without it makes its sessions act as Sami on GitHub or Google Cloud.

## The live checkout

- `~/.dotfiles` is production: every session and systemd user unit on the machine reads its files, so an edit is live when it is saved, before any commit. Test a change to a shim or a script from a copy outside the checkout, and save it here only when it is ready.
- Write a shim atomically: a temporary file in the same directory, then `mv` over the path. A shim truncated in place is, for a moment, an empty executable that exits 0 and does nothing.
- A tool that copies a script to its own path checks the copy against its intended source (`cmp`) afterwards: a complete copy of the wrong file passes every other check. A file byte-identical to another tracked file is that corruption; restore it from `git show HEAD:<path>`, not by hand. A file that only differs from `HEAD` is someone's edit.
- The checkout always holds other sessions' uncommitted work. Do not stash, reset, check out or move it aside, or make a separate worktree to get around it; commit only your own paths.
- When you change something Sami uses (his skills, omp, the shims), tell him what the change will do, then what it did.

## Publishing a change

Every change reaches `main` through a pull request. In `~/.dotfiles`, a colocated jj checkout whose git HEAD is detached by design:

1. Read `jj status`, and leave out every path that is not yours.
2. `jj commit <your paths> -m '<what and why>'`: one described commit per topic, so a session lands as one or two commits, never one per step or file.
3. `scripts/dotfiles-publish <commit>`. It tries the rebase onto origin's main on a duplicate first and refuses a conflict with every file left as it was, then pushes `dotfiles-publish/<change id>`, opens the pull request and squash-merges it.
4. Exit 3: the commit changes a file `.github/CODEOWNERS` gives an owner (every `AGENTS.md`, `CLAUDE.md`, `SKILL.md` and `skills/` file), so the pull request waits for Sami's review. Run the same command again after he approves.

In `~/.dotfiles`, do not run `jj rebase` onto main, `git pull`, `git rebase`, `git push` to main, `--force`, or a `git switch` or `git checkout` of a branch: a rebase that conflicts writes conflict markers into files every session runs. `scripts/dotfiles-sync local` moves the checkout onto origin's main and keeps uncommitted edits. In any other clone, push a branch and open a pull request with `gh pr create`.

Through the shims, a commit, a push or a gh call whose text holds a listed leak term is refused; remove the term (`shims/AGENTS.md`, "Leak-term list").

## Tests and lint

- Python, installers included, from the repository root: `PYTHONPATH=. uvx pytest scripts/tests shims/tests` (some tests import `scripts.tests`). One file: `uvx pytest scripts/tests/test_ensure_link.py`.
- omp extensions: `bun test omp/extensions/__tests__/`. CI runs it on each change under `omp/extensions/`.
- Envoy, in `envoy/`: `npm ci && npx tsc --noEmit`. CI runs it on each change under `envoy/`.
- A skill's script tests sit in its `scripts/` directory (`plugins/AGENTS.md`).
- Lint with the pinned `shellcheck <file>` and `basedpyright <file>` (`pyrightconfig.json`).

## Environment Facts

- Personal vs. company boundary: company infrastructure repositories must not reference `~/.dotfiles`, and the dotfiles install is not part of company machine provisioning.
- Envoy tools exist only in Sami's own sessions: do not tell anyone else to use them.
- systemd user lingering must stay enabled (`loginctl enable-linger <user>`): without it the user's services die with the last login session. Cleanup of "lingering processes" is unrelated; do not disable linger.
- To show Sami a file on a dev box, send the URL `forward url <path>` prints. A `file://` link or a hand-written `localhost:12802` link opens the path on the wrong machine. `forward port <port>…` makes a port on a dev box, or in an agent box, reach his laptop's `localhost`.
- A dev box reaches the laptop's YubiKey through forward's pcsc channel (`forward doctor` shows it). On the laptop, polkit refuses PC/SC to SSH-session processes and permits systemd `--user` ones: test with `systemd-run --user`, never a raw SSH shell.

## Commit signing and identity

- jj (`.jjconfig.toml` `[signing]`) and git (`.gitconfig`) both sign every commit with the machine's `~/.ssh/jj-signing`. A machine without that key fails to commit rather than commit unsigned.
- `scripts/git-identity` sets the commit identity for git and jj together; `git-identity --help` lists the identities. In one repository the two must agree: jj drops the signature of a commit whose author email is not its `user.email` when it rewrites it.
- A new machine: `ssh-keygen -t ed25519 -N "" -C "jj-signing-$(hostname -s)" -f ~/.ssh/jj-signing`, add the public key to `jj-allowed-signers` under every identity, and register it at <https://github.com/settings/ssh/new> as a Signing Key. Each email must also be verified on the GitHub account, or its commits show Unverified.
