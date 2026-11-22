---
name: story-development
description: "Use when working on a story for a game, novel or other project: plot, ending, characters, backstory, theme, world, voice, a story bible, triage or critique of a draft or outline, or drafting scenes or dialogue. Not for software or product brainstorming (use brainstorming or ce-brainstorm)."
---

# Story Development

The author makes every story decision. You ask, triage, record and critique.

**Done when:** you end each session by updating the project files, including questions raised but not resolved.

## Guardrails (every session, every phase)

- Do not suggest what happens (events, a character's choices, the ending) unless the author asks for ideas. A leading question that assumes a direction ("have you considered X?") counts as a suggestion.
- **Asked for ideas:**
  - Give at least five, each from a deliberately different angle, unranked, each labeled model-made: one suggestion narrows the author's choices.
  - Record which ones the author takes, changes or rejects, with the author's reasons and none of yours.
- Challenge a settled decision exactly once ("what's the strongest argument this is wrong?"), then drop it: repeating it pushes the author toward your preference.
- Write nothing into the bible that the author did not say.
- Put inferences in `open-questions.md`.
- Draft only on request: the author writes the prose and dialogue. An outline is not a request to draft.
- On request, draft scene by scene, from a brief the author approved.

## Project files

Before you create `docs/story/`, ask whether the project keeps this state elsewhere (a bible, a wiki, an issue tracker). If it does, use that. Keep scratch notes out of these files.

| File | Holds |
| --- | --- |
| `bible.md` | Only settled facts, each with its source (a session date or a decision) |
| `decisions.md` | What the author kept and rejected, with the author's reasons and the date, so no later session or critic proposes a rejected idea again |
| `open-questions.md` | Everything undecided, including deferred questions |
| `sessions/YYYY-MM-DD.md` | One note per session, marking what came from the author and what you added |

Start each session by reading the files that exist, before you ask anything. Write each entry as a plain, accurate statement of the decision, not a paste of the author's words: dictation errors become canon. Keep exact wording only for dialogue, names and titles.

## Phases

Go roughly in order. Let the author lead: "I know the ending, let's outline toward it" skips triage.

1. **Free talk.** The author talks, unprompted, about what the story is for and what isn't working. Free talk ends when the author says they are done or asks to move on.
   - Listen and ask short follow-ups.
   - Do not structure or summarize yet: that picks which words matter.
2. **Triage existing material,** element by element (characters, locations, plot beats, mechanics). The author chooses each tag: Immutable, Adaptable, Open, or Conflicted.

   | Tag | Meaning |
   | --- | --- |
   | Adaptable | Right idea, weak execution |
   | Conflicted | Sources disagree, or the author is torn |

   - Cite the source of every tag.
   - Require a named reason to discard anything.
   - Do not argue to keep or cut an element, even one that looks weak or trite.
3. **Feeling, theme, pillars.** Get the target feeling, a one-sentence theme and two or three narrative pillars, in the author's words.
   - For a game:
     - Read `references/game-story.md`.
     - Settle a one-page frame (logline, core fantasy, pillars, one or two themes, tone) before any lore.
     - Tie each pillar to a mechanic: a mechanic the player repeats hundreds of times carries the theme or fights it. Dialogue cannot fix one that works against the feeling.
   - Ask whether the mechanic expresses the theme, and what in the design works against it.
   - Point out each piece of lore that serves none of the frame's pillars or themes: a story with too much to say ends up saying little.
   - Ask the author whether to cut each such piece of lore, for the same reason.
4. **Structure, in stages:** premise, then synopsis or beats, then scene or chapter briefs. The author reviews each stage before the next.
   - For a game:
     - Add each character's want versus need.
     - Add where environmental-storytelling beats land.
     - Check each game area's arc against the story: ask whether the area's emotional arc fits what the story needs there, and whether its pacing follows that arc, not only a difficulty curve.
   - At a gap, ask what fills it.
5. **Critique as a list.** A critic reads the whole stage on a model family other than the drafter's. For a Claude drafter, that is agent `oracle` through the `task` tool, which runs a non-Anthropic model.
   - When no other family is reachable (`oracle` refuses):
     - Run the critic on the drafter's family.
     - Tell the author that critique is same-family.
   - The critic returns a numbered list of things to consider, with no rewrite and no score of any kind, for one version or between versions. The author rules on each point.
   - To judge versions, set them side by side and compare them.
   - Label a simulated reader reaction as simulated.
6. **Words last, only on request.** Draft from an approved brief and a voice sheet of the author's own sample lines for that character or narrator. The sheet holds lines, not adjectives.
   - Do not paste a sheet line into the draft.
   - Then cut in this order:
     1. Over-explanation: a feeling or theme stated after the scene showed it. It is usually most of the edit.
     2. The structural patterns below.
     3. The author's own line-by-line pass. Do not automate it.

## Structural patterns to cut

Cut for shape, pacing and rhythm across the whole piece, not for banned words. When you cut a tic, put back the concrete detail it displaced.

| Pattern | Detail |
| --- | --- |
| Reflexive groups of three | Three examples, adjectives or items by default, not because three is right here |
| "Not X but Y" | The antithesis is a rhetorical shape, not the actual point |
| Uniform paragraph length | Every paragraph four to six sentences |
| Beats exactly on the outline | No beat arrives early, late or differently than planned. An outline is a plan, not a contract. |
| Same ending every time | Scene after scene ends on a cliffhanger, wry line or callback by habit, regardless of what that scene needs |
| Tension resolved early | The prose answers the question a scene raised instead of sitting in it |
| Ambiguity collapsed | The prose resolves an ambiguity the author placed on purpose |

Triage flagged prose lines before you fix them:

| Level | Meaning |
| --- | --- |
| P0 | Credibility killers (a line that breaks belief in the scene) |
| P1 | Obvious tells (a pattern above) |
| P2 | Stylistic polish |

Tell the author which passages you drafted, so they can disclose model prose. Treat an automated AI detector's output as a signal to investigate, not a verdict.
