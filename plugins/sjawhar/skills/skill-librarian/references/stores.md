# Stores: lifetime and delivery

Read this when you choose a lesson's home or propose removing one. CLAUDE.md is the one pushed store with a byte cap, because it costs tokens in every session. Memory is also pushed, bounded by what recall returns. Every other store is pulled and costs only when it loads.

| Store | Holds | How it reaches a session | Lifetime |
| --- | --- | --- | --- |
| `~/.dotfiles/.claude/CLAUDE.md` | Sami's rules beyond one repository | Pushed: loaded into every session | Until Sami removes the rule. Hard cap 8,000 bytes |
| Mnemopi memory | Facts about Sami's or Theorem's people and places, Sami's phrasing | Pushed: recalled entries are injected into each session; also `recall` | Until consolidated, invalidated or forgotten (`memory_edit`) |
| A skill in `~/.dotfiles/plugins/sjawhar/skills/` | A procedure, or a tool fact in a `using-<tool>` skill | Pulled: an agent reads it when its description matches the task | Until removed under `skill://writing-agent-skills` section 5 |
| A repository's AGENTS.md, docs/ or repo skill | One repository's design, taste or workflow | Loaded in sessions that work in that repository | That repository's own rules |
| `working-at-theorem` in theorem-labs/verification-skills (created by the first company-wide lesson; not yet present) | Company-wide Theorem rules | Pulled, as a skill, at the pinned version | Until a versioned PR removes it |
| The librarian ledger (Dispatch) | Lessons, sessions and leads with their verdicts | Read by the librarian at startup | Permanent record; not read by other sessions |
