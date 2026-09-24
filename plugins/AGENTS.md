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

**Every consumer reads the working copy of this checkout, not main.** `~/.claude/skills/sjawhar` and oh-my-pi's skills path are live links into `plugins/sjawhar/skills` here, so an agent sees a change the moment the file on disk changes -- and sees nothing when a commit lands on `main@origin` but this working copy still sits on an older base. Pushing a skill commit delivers it to other machines; delivering it to the agents on THIS machine means advancing the working copy onto it (`jj rebase -r @ -d main@origin` keeps uncommitted edits; check `jj log -r 'main@origin ~ ::@'` is empty). Measured 2026-09-12: the working copy parked five commits behind main served the pre-standard `product-demos` text to a judge agent for hours after the standard had been pushed, silently.
