# omp

Configuration for oh-my-pi (omp), the agent harness. The binary is a fork release pinned in `mise.toml`; this directory holds what it reads. Each extension's header comment is its documentation. A bare path below is relative to `omp/`; one starting `scripts/`, `shims/`, `installers/` or `plugins/sjawhar/` is relative to the repository root.

## How it is wired

- `installers/omp.sh` links into `~/.omp/agent/`: `mcp.json`, `lsp.json`, `keybindings.yml`, `WATCHDOG.yml`, `WATCHDOG.md`, `agents/`, `hooks/`, each extension's entry file under `extensions/`, and `plugins/sjawhar/commands` as `prompts/`. It links `omp/plugins/` to `~/.omp/plugins`, links `rules/` with `scripts/omp-link-rules`, and builds `models.yml` (below).
- `shims/omp` runs on every launch: it puts `config.yml`, then the machine's local overlay, in `PI_CONFIG_FILES`, sets up a named profile, and execs omp through `scripts/agent-session`. That runs the first non-wrapper `omp` on `PATH`, so a local build ahead of mise's shims wins.
- `shims/omp` can also cap a session's memory in a systemd scope (`OMP_MEMORY_MAX`); its header says when, and lists everything else it sets.
- Skill pools are declared in `skills-sources.json` at the repository root and fed to omp by `extensions/dotfiles-skills.ts`.
- `*.service` and `*.timer` here are systemd user units, each linked by its own script in `installers/`.

## Run and test

- Run: `omp`, or `omp --profile NAME`; `omp -p '<prompt>'` for one scripted turn.
- All extension tests (CI runs these on each change under `omp/extensions/`): `bun test omp/extensions/__tests__/`
- One file: `bun test omp/extensions/__tests__/path-guard.test.ts`
- A condition rule: `omp ttsr test '<command>' --rule omp/rules/github-api-budget.md --source tool --tool bash`
- `shims/omp` and the `omp-models-*` scripts are tested in `scripts/tests/` (`test_omp_profile_links.py`, `test_omp_models_check.py`).

## When an edit takes effect

- A running session watches every `PI_CONFIG_FILES` entry, so an edit to `config.yml` in a machine's `~/.dotfiles` is live in every session there.
- Extensions load once per process: an edit reaches a session at its next start.
- `/reload-plugins` re-reads skills, commands, agent definitions and `mcp.json`; `/mcp reload` re-reads `mcp.json` alone.

## config.yml

- It is a read-only overlay, not a link: omp writes `/model` picks and TUI toggles to the profile's own `~/.omp/agent/config.yml`, and the committed values win on every key both set. A choice that should stick is an edit here.
- `config.local.yml` (gitignored) is the machine's own model routing, in `config.yml`'s shape, loaded after `config.yml` for the default profile only. A named profile loads `config.<profile>.local.yml` instead.
- A fallback chain for a cross-family model (`gpt-6.1-sol`, `gpt-6-astra`) names no Anthropic model: with nothing reachable the spawn refuses rather than answer from Claude. The one exception is `openai-codex/gpt-6-astra:high`, run only by the `sami-proxy` agent.

## models.yml

- It holds only universal provider and catalog patches. omp never reads it directly: `scripts/omp-models-build` merges the gitignored, machine-local `models.local.yml` over it into `~/.omp/agent/models.yml`.
- A pull does not rebuild it (`scripts/dotfiles-follow` does not run the build). After editing either file, run `scripts/omp-models-build`.
- Do not route Claude through the model gateway by giving the built-in `anthropic` provider a gateway `baseUrl`: every Claude chain ends on `anthropic/...`, so a down gateway would leave no Claude. Declare a separate provider and point roles at it in `config.local.yml`.

## Credentials

- A provider key lives only in a machine-local file: `models.local.yml`, or for a named profile its `~/.omp/profiles/NAME/agent/models.yml` (`models.overlay.yml` in a shared profile). A gateway provider mints a token per call with `scripts/gateway-token` (see its header).
- A key never goes in a committed file, in a session's environment (`shims/omp` sets none), or on an MCP server's command line, where any user can read it.
- `mcp.json` starts a server that needs a key through `secrets` or `scripts/secret-run`, which put the key in the server's environment.

## Profiles

- A shared profile is a named profile with `~/.omp/profiles/NAME/agent/models.overlay.yml`: `shims/omp` links the default profile's state into it and builds its `models.yml` from the default's plus the overlay, so only the keys differ.
- An opted-out profile is a named profile whose directory holds `.no-shared-setup`: `shims/omp` writes nothing there.
- The header of `shims/omp` has the rest, including the machine's default-profile file.

## Plugins, extensions, agents

- Plugin pins (`omp/plugins/package.json`, `bun.lock` and `omp-plugins.lock.json` beside it) change only by a commit; each machine installs them with `bun install --frozen-lockfile`. Do not edit a pin or run a plain `bun install` or `bun add` in a machine's live `~/.dotfiles`.
- A pin bump must redo each patch in `omp/plugins/patches/` (`bun patch <name>`, then `bun patch --commit node_modules/<name>`): bun skips a patch whose key no longer matches, with no error.
- `extensions/` holds dotfiles-owned extensions only. Anything else is a plugin pinned from GitHub, never a path or a link to a checkout.
- A new extension loads only after you add its `ensure_link` line to `installers/omp.sh`. Link the entry file only; its relative imports resolve from the link's real path.
- `hooks/post/jj-snapshot.ts` is the only jj snapshotter: add no other, and never await or kill a snapshot (its header says why).
- `agents/*.md` are omp's agent definitions; edit them here. `plugins/sjawhar/agents/` is a separate set: a change to one does not reach the other.
- Keep `WATCHDOG.yml`'s `advisors:` list non-empty: with an empty roster omp runs its legacy default advisor.
