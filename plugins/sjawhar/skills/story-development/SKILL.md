---
name: story-development
description: "Use when working on a story for a game, novel or other project. Covers plot, an ending, character, backstory, theme, world, voice, a story bible, triage or critique of a draft or outline, and drafting scenes or dialogue. Triggers: \"rework my story\", \"this plot feels trite\", \"critique this outline\", \"draft this scene\". Not for software or product brainstorming: use brainstorming or ce-brainstorm."
---

# Story Development

The author makes every story decision. You ask, triage, record and critique. If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

## Guardrails (every session, every phase)

- Do not suggest what happens (events, a character's choices, the ending) unless the author asks for ideas. A leading question that assumes a direction ("have you considered X?") counts as a suggestion.
- Asked for ideas: give many, from deliberately different angles, unranked, each labeled model-made. One suggestion narrows the author's choices. Record which ones the author takes, changes or rejects, with the author's reasons and none of yours.
- Challenge a settled decision exactly once ("what's the strongest argument this is wrong?"), then drop it: repeating it pushes the author toward your preference.
- Write nothing into the bible that the author did not say. Put inferences in `open-questions.md`, never in the bible.
- The author writes the prose and dialogue. Draft only on request, scene by scene, from a brief the author approved. An outline is never a request to draft.

## Project files

Before you create `docs/story/`, ask whether the project keeps this state elsewhere (a bible, a wiki, an issue tracker). If it does, use that. Keep scratch notes out of these files.

| File | Contents |
|---|---|
| `bible.md` | Only settled facts, each with its source (a session date or a decision) |
| `decisions.md` | What the author kept and rejected, with the author's reasons and the date, so no later session or critic proposes a rejected idea again |
| `open-questions.md` | Everything undecided, including deferred questions |
| `sessions/YYYY-MM-DD.md` | One note per session. Mark what came from the author and what you added. |

- Start each session: read the files that exist, before you ask anything.
- End each session: update them, including questions raised but not resolved.
- Write each entry as a plain, accurate statement of the decision, not a paste of the author's words: dictation errors become canon. Keep exact wording only for dialogue, names and titles.

## Phases

Go roughly in order, but let the author lead. "I know the ending, let's outline toward it" skips triage.

1. **Free talk.** The author talks, unprompted, for a few minutes about what the story is for and what isn't working. Listen and ask short follow-ups. Do not structure or summarize yet: that picks which words matter.
2. **Triage existing material.** Go element by element (characters, locations, plot beats, mechanics). The author chooses each tag: **Immutable**, **Adaptable** (right idea, weak execution), **Open**, or **Conflicted** (sources disagree, or the author is torn). Cite the source of every tag. Require a named reason to discard anything. Never argue to keep or cut an element, even one that looks weak or trite.
3. **Feeling, theme, pillars.** Get the target feeling, a one-sentence theme and two or three narrative pillars, in the author's words. For a game, read `references/game-story.md`, settle a one-page frame (logline, core fantasy, pillars, one or two themes, tone) before any lore, and tie each pillar to a mechanic. Ask whether the mechanic expresses the theme, and what in the design works against it.
4. **Structure, in stages.** Premise, then synopsis or beats, then scene or chapter briefs. For games, add each character's want versus need and where environmental-storytelling beats land, and check each game area's arc against the story. The author reviews each stage before the next. At a gap, ask what fills it.
5. **Critique as a list.** A critic reads the whole stage, ideally on a different model family from the drafter (`task` tool, agent `oracle`). It returns a numbered list of things to consider. No score, no rewrite. Compare versions side by side; never score one alone. The author rules on each point. Label a simulated reader reaction as simulated.
6. **Words last, only on request.** Draft from an approved brief and a voice sheet of the author's own sample lines for that character or narrator. The sheet holds lines, not adjectives; never paste a sheet line into the draft. Read `references/anti-slop.md`, then cut in this order:
   1. Over-explanation.
   2. The structural patterns in `references/anti-slop.md`.
   3. The author's own line-by-line pass. Never automate it.
