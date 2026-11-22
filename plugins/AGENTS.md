# plugins

- `plugins/cursor-harvest/` holds a skill copied verbatim from a Cursor plugin: leave it as it is.
- A skill here loads in every repository Sami works in, so it names no repository, no path of a single repository and no project's workflow, and a rule drawn from one repository's incident reads correctly with that repository's name removed. A project's rule goes in that project's own `AGENTS.md` or `.claude/skills/`; where the repository has its own `AGENTS.md`, skill, workflow or tool for the job, point the skill here at it.
- A skill that names a customer goes in the private skill pool (`.secrets/dotfiles/skills`, a clone of a private repository), never here: this repository is public.
- `plugins/sjawhar/agents/` holds Claude Code's agent definitions and `omp/agents/` holds omp's, under the same names: a change to one does not reach the other.
- Check a skill with `uv run --script plugins/sjawhar/skills/writing-agent-skills/scripts/check_skill.py plugins/sjawhar/skills/<name>`.
- A skill's script tests sit beside its scripts: `uvx pytest plugins/sjawhar/skills/<name>/scripts`.

Where this file and the code disagree, the code is right: follow the code and correct this file.
