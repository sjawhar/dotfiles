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
- **`jj`** locks the git worktree of every workspace `jj workspace add` creates, and in a
  checkout that sets `git config omp.sessionTrailer false` (the backing git repository's config)
  runs every command that can write a description with `templates.commit_trailers` empty and
  `immutable_heads()` back at `builtin_immutable_heads()`: the session overlay's cross-session
  guard (AGENTC-318) tells a session's own commits by their trailer, so it is off there, and
  another session's unpushed commits in such a repository can be rewritten without
  `--ignore-immutable`. Read-only commands (`log`, `st`, `show`, `diff`, `op`, `bookmark`, `git`,
  …) skip the check.
- **`git`**, in an agent session, refuses a bare `git worktree prune` and locks every
  `git worktree add`, so no box can prune another box's live registration, and gives every
  commit an omp session makes its `Omp-Session` trailer, in every repository, public ones
  included, unless the checkout sets `git config omp.sessionTrailer false`
  (`scripts/omp-session-trailer`, which jj asks too; `omp-session-trailer git` in a repository
  prints its answer). `git commit` gets it from git's own `--trailer`. `git merge`, `pull`,
  `cherry-pick` and `revert` run with `core.hooksPath` at `git-hooks/`: its prepare-commit-msg
  adds the line, and each hook name there then runs the repository's own hook of that name
  (`git-hooks/hook`). A rebase, including `pull --rebase`, leaves replayed commits as they were;
  an alias, `git am` and `git commit-tree` add nothing. Commits omp writes through its own git
  library never run `git`, so no shim reaches them: omp appends `PI_COMMIT_TRAILERS`, which
  `omp/extensions/session-env.ts` sets to the session's line.
- **`omp`** applies mise's environment, the committed config overlay, mnemopi's recall weights,
  a `PI_SHELL_PREFIX` that turns errexit, nounset and pipefail off before each bash call (so
  one call's `set -euo pipefail` does not break later calls in the persistent shell) and a named
  profile's links, then execs omp through `scripts/agent-session` (below). It sets no
  Envoy, Dispatch or provider-key variable: the session's plugin reads its `envoy.json`, and keys
  come from the profile's `models.yml` (`omp/AGENTS.md`). `OMP_MEMORY_MAX=<size>` (unset by
  default, so nothing changes without it) runs the session in a transient systemd user scope with
  that hard memory cap and swap off, waits for it instead of exec'ing, and says on the pane when
  the cap was what killed it. One session reached 138.0 GiB on 2026-10-26 and the box-wide OOM
  killer took two unrelated pods with it; inside a scope only that session dies. `MemoryHigh` is
  deliberately not set: it throttles a session into reclaim instead of killing it, which looks
  like a lane that is thinking.
  A named profile whose `agent/models.overlay.yml` exists is a shared profile: the shim links
  all of the default's state into it and rebuilds its `models.yml` as the default's merged with the
  overlay, on every launch, under a 30 s lock; a real entry on both sides stops the launch
  (`omp/AGENTS.md`, "A shared profile").
- **`claude`** and **`codex`** (a link to `claude`) export the model-spend environment of
  `scripts/agent-spend-env.sh` (dispatch://INFRA-165), then exec the first real binary of that name
  past this dir on PATH, skipping mise's shims dir and any file whose real path is in a `shims/` dir,
  else `mise which` it; arguments, stdin and PATH go through unchanged. They are for a claude or codex
  that a script or a session's bash tool starts by name (`claude -p` workers): those inherit their
  omp parent's collector and `OTEL_LOGS_EXPORTER=none`, and Claude Code sent nothing. Through the shim
  Claude Code gets `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_LOGS_EXPORTER=otlp`, traces and metrics off,
  and the project of its parent session's file (`OMP_SESSION_ID`); a different collector is kept as is.
  Codex takes only the tags from the environment: its exporter is the block `installers/agent-spend.sh`
  writes into `~/.codex/config.toml`. They do not exec `scripts/agent-session`, which `scripts/cld`
  still uses: that would also register every scripted worker as its own secrets-broker host session
  (a helper call and up to 10 s wait per launch) in place of the identity it inherits from its parent.
- **`knives`** execs the pinned knives (`"github:sjawhar/knives"` in `mise.toml`, looked up with
  `mise which -C / knives --tool github:sjawhar/knives`), or a local build on PATH past it,
  never a knives in a mise dir: a session keeps the PATH it started with, and that PATH names
  the install dir pinned back then. `-C /` keeps a project's own mise pin out, since the knives
  plugin every session loads is pinned machine-wide beside it. A third entry in one pid within
  10 s exits with an error; `knives gh` enters twice by design, through `shims/gh`.
- **`ssh-keygen`** drops `SSH_AUTH_SOCK` for `-Y`, the operations git and jj sign and verify commits
  with: the signing key is a file, and ssh-keygen asks the agent first, so a stuck agent socket
  hung every signed commit. It execs the first `ssh-keygen` past its own PATH entry.
- **`bq`** puts BigQuery's CLI on PATH at all. The Cloud SDK installs it beside gcloud in the
  SDK's own bin dir, which is on nobody's PATH here, so every caller named the SDK's full path
  instead (agent-infra's spend loaders, 2026-10-29). It takes the first `bq` past its own PATH
  entry (`shims/ssh-keygen`'s lookup), else the SDK's install location under the home gcloud uses, and
  turns gcloud's per-call file logging off, since bq shares that config dir and those logs.
- **`pulumi`** names a state backend only when the CLI would otherwise find none. Pulumi CLI
  3.268.0 that reaches no backend at all, and recognizes a coding agent around it (it reads
  `CLAUDECODE`, `CURSOR_AGENT`, `CODEX_*`, `GEMINI_CLI`, `OPENCODE`, `AI_AGENT` and others),
  creates an ephemeral Pulumi Cloud account of its own, writes the claim in
  `/tmp/.pulumi/agent-claim.json` and prints a claim URL; one was created on this box on
  2026-10-28 by an agent that only ran `pulumi package add`. `PULUMI_BACKEND_URL` outranks a
  `pulumi login` and a project file's own `backend.url` rather than falling back to either, and
  this box hosts programs on more than one real backend, so the shim sets it (to `file://~`)
  only with all four true: `PULUMI_BACKEND_URL` unset or empty, `PULUMI_ACCESS_TOKEN` unset or
  empty, no non-empty `current` in the credentials file a `pulumi login` writes, and no
  `backend.url` in the `Pulumi.yaml`/`Pulumi.yml` the command would read (searched upward from
  the working directory, which `--cwd`/`-C` moves). It sets `PULUMI_SKIP_UPDATE_CHECK=true`
  either way, and is otherwise transparent: same argv, stdin, stdout and exit code, and no output
  of its own. It takes the first `pulumi` past its own PATH entry (`shims/ssh-keygen`'s lookup).
- `gh-app-token`, `gcloud`, `google-user-token`, `aws-cp`, `tmux`, `xdg-open`,
  `pyright`, `basedpyright`: see each file's header.
- **mise off PATH:** `omp`, `knives`, `pyright` and `basedpyright` call mise. A unit's, a test
  harness's or a git credential helper's PATH can hold this dir without `~/.dotfiles/bin`, where
  mise lives; these shims then run `~/.dotfiles/bin/mise` by its path, with `~/.mise` as its data
  dir when `MISE_DATA_DIR` is unset.
- **`gcloud`**'s global-flag table is the installed SDK's. After any `gcloud components update`,
  run `python3 scripts/gcloud-global-flags --check`; on a difference, regenerate the table with
  `python3 scripts/gcloud-global-flags`, and the test fixture with `--json`. Run it with `python3`,
  not `bash`.
- `gws`: an executable shim that resolves the gws-accounts wrapper installed by the omp plugin pin
  (`omp/plugins/package.json`). Its marker lets the package wrapper skip the shim while finding real
  gws; if the package is absent, the shim fails loudly and directs `bun install` in `omp/plugins`.

## Adding to a shim

A shim composes with everything else that wraps its binary or already does what it adds.
Before adding a side effect or a binary lookup, search for the callers that already do the
same thing and for every other wrapper of the same binary, not only for the callers you mean
to protect: a lock the shim adds can collide with a caller's own lock, and a lookup can find a
pass-through wrapper that hands the call straight back to the shim. A shim that resolves its
real binary takes the first match past its own PATH entry, never the first from the top, and
a pass-through wrapper hands the next binary the PATH tail past its own entry, so nothing
below it can find the wrapper again. `shims/gh` still searches from the top, which is safe
only while nothing puts a gh wrapper above it. `shims/gcloud` does too, under the same
condition, and its routing tests depend on it: they put a stub `gcloud` above this dir and
expect the shim to hand the call to the stub. `shims/jj` and `shims/tmux` also search from the
top, and hand the binary they find the whole PATH: jj's own `git` calls must still reach
`shims/git`, and a tmux server started through the shim gives its PATH to every pane. The first
jj or tmux they find is usually mise's shim, which hands the call back to the first one on PATH
whenever mise has no version of it for the directory (a relocated `HOME` or `XDG_CONFIG_HOME`
hides mise's global config). So `DOTFILES_JJ_SHIM` and `DOTFILES_TMUX_SHIM` name every binary
the shim has handed the call to in that pid, and a re-entry in the same pid skips each one (and
every path to the same file), taking the next one on PATH or failing with exit 127 instead of
looping.

## How `shims/omp` resolves omp

`shims/omp` runs `mise env -s bash` first. A session keeps the PATH it started with, and that
PATH can name the install dir of an older pin; mise swaps it for the current pin's dir, so a
stale PATH still starts the pinned omp. Then `scripts/agent-session` takes the first `omp` on
PATH that is not a wrapper: it skips mise's shims dir (`~/.mise/shims`), whose `omp` hands the
call back to PATH when mise has no version for the directory, and every `omp` whose real path is
in a `shims/` directory (this shim, another checkout's copy). A local build on PATH ahead of
mise's shims dir wins: `mise env` keeps the entries ahead of that dir ahead of the tool dirs it
adds, and puts its tool dirs first on a PATH without it; with the pin not installed and no local build, the launch fails with
exit 127 instead of looping back into the shim.
