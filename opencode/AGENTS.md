# opencode

OpenCode configuration, OMO profiles, and local plugins.

## Key files

- **`opencode.json`** — main config: `plugin` list, `references` (indexed upstream repos), custom `provider` model definitions, `mcp` servers (context7, ceramic), keybinds. Symlinked to `~/.config/opencode/opencode.json` by `installers/opencode.sh`.
- **`oh-my-openagent.full.json` / `oh-my-openagent.minimal.json`** — OMO profiles. The `omo` shell function symlinks the chosen one to `~/.config/opencode/oh-my-openagent.json`.
- **`tui.json`** — TUI settings (keybinds) plus its own `plugin` list for TUI plugins. Symlinked to `~/.config/opencode/tui.json`.
- **`plugins/`** — local plugin scripts. Server plugins are loaded via `file://` entries in `opencode.json`: `dotfiles-bridge.ts` (bridges skills/agents/commands from `plugins/sjawhar` and `vendor/` into OpenCode), `jj-snapshot.ts`, `session-env.ts`, `session-registry.ts`. TUI plugins are loaded from `tui.json` instead: `pr-status.tsx` (shows the GitHub PR for the current change in the prompt row).

## Conventions

- Reference new plugins by their `file://{env:HOME}/.dotfiles/opencode/plugins/...` path — in `opencode.json` for server plugins, in `tui.json` for TUI plugins. A module exports either `server` or `tui`, never both.
- TUI plugins render via the `@opencode-ai/plugin/tui` slot API and need a `/** @jsxImportSource @opentui/solid */` pragma on line 1. Bun transpiles the `.tsx` at load time, so no build step or local `solid-js` dependency is required.
- Keep profile files named `oh-my-openagent.<name>.json` so the `omo` function discovers them.
- Do not commit `node_modules` or `bun.lock` churn unrelated to your change.

## How changes take effect

`opencode.json` and `tui.json` are symlinked, so edits apply on the next OpenCode start. Adding a new profile only requires the correctly named file; switch with `omo <name>`.
