# Where a lesson goes (one home each)

A preference of Sami's is not a company rule. A rule is company-wide only when a written Theorem policy, a Theorem person other than Sami, or Sami himself says it applies to all of Theorem.

- One repository's design, taste or workflow: its AGENTS.md, `docs/` or a repo skill, by a PR under its rules (in the dotfiles, `scripts/dotfiles-publish`, which waits for the code owner on these files).
- A company-wide Theorem rule: the `working-at-theorem` skill in theorem-labs/verification-skills. Every PR bumps the version; tag after the merge.
- Sami's rule or preference beyond one repository: `~/.dotfiles/.claude/CLAUDE.md`, after his yes. No Theorem name, repository, person, operations contact or Dispatch project there. Quote Sami in the commit message, and publish with `scripts/dotfiles-publish`, which waits for his review.
- A fact about Sami's people, places or phrasing, or Theorem's people, places or tools: memory, with `retain`.
- A tool fact: `~/.dotfiles/plugins/sjawhar/skills/using-<tool>/SKILL.md`, after his yes; create or extend it. If a vendor skill for the tool exists, leave it unchanged and name it there.
- A blocker a skill or document covers: no write. `envoy_send` the pointer; ledger `replied`.
- Already covered by a home, seen in one session only and not Sami's words, or not checkable by a command, file read or reproduction: no write. Ledger `dropped`, with the reason.

Also:

- Theorem material never goes into CLAUDE.md: Sami's preference about Theorem work goes to its one repository, or to memory.
- No agent owns a repository, so make every repository fix yourself; do not send it to another session. A lesson that shows a defect in a tool gets a code fix in that tool, not a doc line that works around it. Say so in the ledger.
- Vendored and upstream skills (superpowers, compound-engineering) are never a home.
- Propose an edit to Sami's CLAUDE.md or skills only for his own words not yet written there, or a failure that passes step 1 of `writing-agent-skills`. Else drop the lesson.
- Before you create or edit a skill, follow `writing-agent-skills`.
