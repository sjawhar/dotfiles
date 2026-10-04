# shims

PATH-priority wrappers: each file here shadows the binary of the same name and adds behavior
before handing the call to the real one.

## What each shim adds

- **`gh`** sources the auth token. In an agent session (omp, agentbox, or anything launched
  with the gh-app routing include) it routes every call to the repo owner's GitHub App; an
  owner with no App installation (METR, can1357, every upstream we fork) gets the upstream
  token from the secrets store (`gh-app.agent.fallback-secret`, `GH_PUBLIC_REPO_PAT`). It
  refuses the user's own login, so an agent never reaches the keyring. `gh pr create` for an
  agent-c pull request that changes only documentation is refused unless its head is the
  day's `docs-batch/<topic>/<UTC date>` branch and that batch has not merged yet
  (`scripts/docs-pr-gate`).
- **`jj`** locks the git worktree of every workspace `jj workspace add` creates.
- **`git`**, in an agent session, refuses a bare `git worktree prune` and locks every
  `git worktree add`, so no box can prune another box's live registration, and gives every
  `git commit` from an omp session its `Omp-Session` trailer.
- **`omp`** finds the build to run and sets up the session environment (below).
- `gh-app-token`, `gcloud`, `gws`, `google-user-token`, `aws-cp`, `tmux`, `xdg-open`,
  `pyright`, `basedpyright`: see each file's header.

## Adding to a shim

A shim composes with everything else that wraps its binary or already does what it adds.
Before adding a side effect or a binary lookup, search for the callers that already do the
same thing and for every other wrapper of the same binary, not only for the callers you mean
to protect: a lock the shim adds can collide with a caller's own lock, and a lookup can find a
pass-through wrapper that hands the call straight back to the shim. A shim that resolves its
real binary takes the first match past its own PATH entry, never the first from the top, and
a pass-through wrapper hands the next binary the PATH tail past its own entry, so nothing
below it can find the wrapper again. `shims/gh` still searches from the top, which is safe
only while nothing puts a gh wrapper above it.

## How `shims/omp` resolves omp

It looks for a local build anywhere on PATH, skipping every `omp` whose real path is in a
`shims/` directory (itself under another name, another checkout's copy) and mise's own dirs.
Its fallback is `mise x github:sjawhar/oh-my-pi -- omp`. mise resolves that bare `omp` with the
tool dirs it adds ahead of the PATH it was given, but it adds a dir only when that install dir
is not already there. An install dir that arrived on PATH without mise's record of adding it
(a tmux window opened from a shell outside tmux gets that shell's PATH and none of its
`__MISE_*` state) counts as the caller's, the shims dir ahead of it wins, and mise and the shim
hand `omp` back and forth forever. So the shim hands `mise x` a PATH without mise's install
dirs, which is what makes mise resolve the release, and a second entry into the shim in the
same pid within 10 s exits with an error instead of looping (a slower same-pid entry is omp's
`/restart` exec'ing `omp` in place, which must relaunch). A shim that falls back to
`mise x <tool> -- <bin>` needs the same.

The child `mise x` starts gets a different PATH, decided by whether the PATH mise was handed
carries mise's own shims dir (`~/.mise/shims`): with it (every PATH `.bashrc` builds has it,
and the shim keeps it), the entries ahead of that dir, the dotfiles shims dir among them, stay
ahead of the tool dirs, so `omp` by name in the child is the shim; without it the tool dirs
come first. That is why `scripts/omp-no-provider-keys`, which the shim execs inside `mise x`
for a default-profile session, takes a bare `omp` from the first mise install dir on PATH
instead of looking it up: correct in both cases, because the shim hands mise no install dir of
its own and the requested tool's dir is the first install dir either way.
