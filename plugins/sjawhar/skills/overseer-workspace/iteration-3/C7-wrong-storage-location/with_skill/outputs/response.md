# Overseer Storage Locations

## Briefing Notepad
**Path:** `~/.dotfiles/.overseer/briefing.md`

This is the operational handover document — updated liberally throughout a session and always before wrapping up. Contains active workstreams, pending user decisions, recent actions taken, cross-pollination log, session inventory, and the watch list. It's the primary handoff mechanism between overseer sessions.

## Long-Term Memory
**Path:** `~/.dotfiles/.overseer/memory.md`

Institutional knowledge that persists across sessions. Different from the briefing (which is operational state) — memory captures learned patterns, user preferences, recurring corrections, failure modes, prioritization frameworks, and known quirks. Read on every startup, written to whenever something is learned that a future overseer would benefit from.

## Priority Documents
**Path:** `~/.dotfiles/.overseer/priorities/` (directory)

User-maintained markdown files with current priorities, project goals, deadline info, sprint plans, or strategic context. Read-only for the overseer — scanned during orientation, used for prioritization, never modified by the overseer.

## Why These Paths

All three live under `~/.dotfiles/.overseer/` because:
1. They're part of the dotfiles repo, so they persist across machines and are version-controlled
2. The `.overseer/` directory keeps overseer-specific state isolated from the rest of the dotfiles
3. These exact paths are specified in the overseer skill instructions and are what every overseer session expects to find
