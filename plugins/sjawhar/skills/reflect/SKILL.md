---
name: reflect
description: "Analyze recent Claude Code and OpenCode sessions for recurring corrections, preferences, and automation opportunities. Use for a retrospective over recent agent work."
---

# Reflect

For the requested number of days (default: 7), index both sources and inspect flagged
turns without copying session content into the database:

```bash
INDEXER=~/.dotfiles/plugins/sjawhar/skills/reflect/index-sessions.py
python3 "$INDEXER" --days <N>
SESSION_DB="$(python3 -c 'import runpy,sys; print(runpy.run_path(sys.argv[1])["get_db_path"]())' "$INDEXER")"
sqlite3 "$SESSION_DB" "SELECT s.source,s.project,s.id,t.turn_number,f.flag_type FROM flags f JOIN turns t ON t.id=f.turn_id JOIN sessions s ON s.id=t.session_id WHERE s.timestamp > datetime('now','-<N> days') ORDER BY s.timestamp DESC;"
```

Redact secrets before sharing flagged content. Keep the initial prompt and adjacent
turns; compare with the newest prior report.

Run five agents in parallel; each gets: focus + flagged content + prior-report context.

- **Mistake Finder:** corrected agent errors.
- **Preference Learner:** recurring user preferences.
- **Command Repeater:** workflow candidates.
- **Prompt Repeater:** similar initial requests.
- **CLAUDE.md Miner:** project-specific durable rules.

Deduplicate and rank patterns by frequency and impact. Assess earlier improvements as
improved, unchanged, or regressed, then present actionable examples and choices. Put
approved skills in `plugins/sjawhar/skills/`, agents in `plugins/sjawhar/agents/`,
commands in `plugins/sjawhar/commands/`, and global rules in `.claude/CLAUDE.md`.

Save reports as `YYYY-MM-DD.md` in `~/.dotfiles/.claude/session-analysis/`. If there
are no flags, report that healthy result; if an agent fails, continue and record it.
