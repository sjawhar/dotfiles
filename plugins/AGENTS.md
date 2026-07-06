# plugins

Custom skills, agents, and commands, all under `sjawhar/`.

## Layout

- **`sjawhar/.claude-plugin/plugin.json`** — plugin manifest (`name: sjawhar`).
- **`sjawhar/skills/`** — skill directories, each with a `SKILL.md`.
- **`sjawhar/agents/`** — subagent definitions (`*.md`).
- **`sjawhar/commands/`** — slash-command definitions (`*.md`).

## Conventions

- Add a skill as `sjawhar/skills/<name>/SKILL.md`; agents and commands as single `*.md` files in their respective dirs.
- Follow the frontmatter/structure of existing entries in the same dir.

## How changes take effect

Claude Code discovers this content through the marketplace plugin (declared at the repo root and registered once with `/plugin install sjawhar@sjawhar`); it watches these dirs live, so no symlinking is needed. OpenCode picks up the same dirs through `opencode/plugins/dotfiles-bridge.ts` and via `~/.claude/skills/sjawhar` symlinked by `installers/opencode.sh`.
