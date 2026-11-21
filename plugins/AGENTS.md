# plugins

Skills, agents and commands for Claude Code and omp. `sjawhar/` is Sami's plugin. `cursor-harvest/` holds a skill copied verbatim from a Cursor plugin: leave it as it is.

## Layout

- `sjawhar/.claude-plugin/plugin.json`: the plugin manifest. `.claude-plugin/marketplace.json` at the repository root declares the marketplace.
- `sjawhar/skills/<name>/SKILL.md`, with optional `references/` and `scripts/`; `sjawhar/agents/*.md`; `sjawhar/commands/*.md`. Follow the frontmatter of the entries beside yours.

## Writing a skill

- A skill here loads in every repository Sami works in, so it names no repository, single-repository path or project workflow. A project's rule goes in that project's `AGENTS.md` or `.claude/skills/`; point at it from here where that helps. A rule drawn from one repository's incident must read correctly with the repository's name removed.
- A skill that names a customer goes in the private pool that `skills-sources.json` names, not here.
- The `writing-agent-skills` skill has the authoring rules. Check a skill with `uv run --script plugins/sjawhar/skills/writing-agent-skills/scripts/check_skill.py plugins/sjawhar/skills/<name>`.
- A skill's script tests sit beside its scripts: `uvx pytest plugins/sjawhar/skills/<name>/scripts`.
- Publish as root `AGENTS.md`, "Publishing a change", says.

## How changes take effect

- Claude Code reads the plugin through the marketplace (installed once with `/plugin install sjawhar@sjawhar`) and watches these directories live. omp reads them through `skills-sources.json`.
- Both read this checkout's working copy, not `main`: a merged change reaches this machine's agents only when the working copy moves onto it (root `AGENTS.md`, "Publishing a change").
