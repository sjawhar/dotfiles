# Dotfiles

Before you change a directory, read its `AGENTS.md` if it has one.

## The live checkout

- `~/.dotfiles` is production: every session and systemd user unit on the machine reads its files, so an edit is live when it is saved, before any commit. Test a change to a shim or a script from a copy outside the checkout, and save it here only when it is ready.
- Write a shim atomically: a temporary file in the same directory, then `mv` over the path. A shim truncated in place is, for a moment, an empty executable that exits 0 and does nothing.
- A tool that copies a script to its own path checks the copy against its intended source (`cmp`) afterwards: a complete copy of the wrong file passes every other check. A file byte-identical to another tracked file is that corruption; restore it from `git show HEAD:<path>`, not by hand. A file that only differs from `HEAD` is someone's edit: leave it alone and report it to its owner.
- The checkout always holds other sessions' uncommitted work. Do not stash, reset, check out or move it aside, or make a separate worktree to get around it; commit only your own paths.
- When you change something Sami uses (his skills, omp, the shims), tell him what the change will do, then what it did.

## Publishing a change

In `~/.dotfiles`, a colocated jj checkout whose git HEAD is detached by design:

1. Read `jj status`, and leave out every path that is not yours.
2. `jj commit <your paths> -m '<what and why>'`: one described commit per topic, so a session's work is one or two commits, never one per step or file.
3. `scripts/dotfiles-publish <commit>`.

In `~/.dotfiles`, do not run `jj rebase` onto main, `git pull`, `git rebase`, `git push` to main, `--force`, or a `git switch` or `git checkout` of a branch: a rebase that conflicts writes conflict markers into files every session runs. `scripts/dotfiles-sync local` moves the checkout onto origin's main and keeps uncommitted edits. In any other clone, push a branch and open a pull request with `gh pr create`.

## Conventions

- Everything committed is portable. No committed file reads, writes or links a path of one machine or one user: use `$HOME`, `$DOTFILES_DIR` or a relative path. A committed symlink has a relative target inside the repository, never in the gitignored `vendor/`. Per-machine links and paths are made by the installers at install time, never committed.
- Sami's own software installs from a pinned release (a GitHub tag or SHA, or an exact npm version). A local path or a symlink to a checkout is for a prototype and never lands; the one exception is a `file://` plugin entry for a script in this repository. When a consumer needs something from the source repository (an `omp` manifest, an export, a release asset), change that repository; never commit its build output.
- `vendor/` holds third-party clones the installers make: never edit them there.
- Upgrade a pinned tool without asking, then say what changed and how to roll it back. Ask Sami first when running sessions must restart for it (bun, node, omp).
- Two places build an agent session's environment: `scripts/agent-session`, which every host launcher execs, and `session_env()` in `scripts/agentbox`, for a box. A change every session needs goes in both: a launcher without it makes its sessions act as Sami on GitHub or Google Cloud.
- This repository is public. The shims refuse a commit, a push or a gh text that holds a private term. This repository keeps its terms only in the encrypted `leak-terms.sops.yaml`: change it only with `sops edit leak-terms.sops.yaml`, and never put a term in a file, an argument or the environment.

## Tests and lint

- Python, installers included, from the repository root: `PYTHONPATH=. uvx pytest scripts/tests shims/tests` (some tests import `scripts.tests`). One file: `uvx pytest scripts/tests/test_ensure_link.py`. CI runs none of these, and no CI result holds a merge.
- Lint with the pinned `shellcheck <file>` and `basedpyright <file>`.

## Environment Facts

- Personal vs. company boundary: company infrastructure repositories must not reference `~/.dotfiles`, and the dotfiles install is not part of company machine provisioning.
- Envoy tools exist only in Sami's own sessions: do not tell anyone else to use them.
- On the devbox the Dispatch token in a session's `envoy.json` is the shared token deployed for agent sessions, not Sami's personal credential: a session may read it there. Never commit it.
- systemd user lingering must stay enabled (`loginctl enable-linger <user>`): without it the user's services die with the last login session. Cleanup of "lingering processes" is unrelated; do not disable linger.
- To show Sami a file on a dev box, send the URL `forward url <path>` prints. A `file://` link or a hand-written `localhost:12802` link opens the path on the wrong machine. `forward port <port>…` makes a port on a dev box, or in an agent box, reach his laptop's `localhost`.
- A dev box reaches the laptop's YubiKey through forward's pcsc channel (`forward doctor` shows it). On the laptop, polkit refuses PC/SC to SSH-session processes and permits systemd `--user` ones: test with `systemd-run --user`, never a raw SSH shell.

## Commit signing and identity

- Set a repository's commit identity with `scripts/git-identity`, which sets git and jj together. In one repository the two must agree: jj drops the signature of a commit whose author email is not its `user.email` when it rewrites it.

Where this file and the code disagree, the code is right: follow the code and correct this file.
