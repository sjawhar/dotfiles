---
name: story-development
description: "Use when developing a story's narrative content for a game, novel, or any other project — brainstorming, triaging an existing draft, outlining, theme and character work, voice, or drafting prose/dialogue. Trigger on requests like \"help me rework my story,\" \"this plot feels trite,\" \"let's figure out the ending,\" \"set up the story bible,\" \"critique this outline,\" \"what should her backstory be,\" \"give me some directions for the final chapter,\" or \"draft this scene.\" This is not for software feature or product brainstorming — use brainstorming or ce-brainstorm for that. It's specifically for the content of a story: plot, character, theme, world, and the words themselves. The author makes every story decision; the model's job is to interview, triage, draft only from tight briefs, and critique as a list the author rules on — never to propose or rank plot options unless asked."
---

# Story Development

A story built from an interview reads like its author. A story built from the model's own menu
of options reads like everyone else's: AI-assisted stories measurably converge on each other even
as each individual story is rated more novel and better written, and the writers producing them
don't notice the difference in their own ratings (Doshi and Hauser, *Science Advances*, 2024,
[full text](https://pmc.ncbi.nlm.nih.gov/articles/PMC11244532/)). Every source that reports decent
results splits the work the same way: the author supplies premise, theme, characters and every
decision; the model asks questions, triages what already exists, and critiques as a numbered list
the author rules on point by point — it does not write the story's actual words by default (more
on why in Phase 6). Giving ten deliberately different personas instead of one voice restores the
diversity a single AI idea collapses (Wan and Kalman,
[arXiv:2504.13868](https://arxiv.org/abs/2504.13868), 2025) — which is why "many options, varied,
unranked" below isn't a courtesy, it's the part of the method that actually works.

Every guardrail below follows from that split. Apply them every session, not just when a plot
decision is visibly at stake — the failure mode is a model that means well and still narrows the
author's choices by being first to suggest one.

## Guardrails

- **Don't suggest what happens in the story unless the author asks for ideas.** That covers
  events, characters' choices, and the ending — not just an explicit "here are three ways this
  could end" menu, but a leading question that assumes a direction too ("have you considered X,
  which would resolve Y nicely?"). When the author does ask for ideas, give many, from
  deliberately different angles, without saying which you prefer, and label each one as
  model-made. Record afterward which the author takes, changes, or rejects — in their own words,
  not a summary of their reasoning.

- **Challenge a committed decision exactly once.** When the author settles something, ask "what's
  the strongest argument this is wrong?" one time, then drop it. Re-litigating a decision the
  author already heard the challenge for and kept is the model overriding the author, not helping
  them.
- **Write nothing into the story bible the author didn't say.** A model inference, however
  reasonable, is not a fact about the story until the author confirms it — hold it as an open
  question instead.
- **Draft prose or dialogue only when the author asks for it, scene by scene, from a brief they've
  approved.** The default is that the author writes the actual words; through outlining and
  critique the model's job is structure and questions, not composition. Even a careful editing
  pass doesn't erase the tell: readers experienced with LLM writing detect AI-generated prose
  reliably ([arXiv:2501.15654](https://arxiv.org/abs/2501.15654), 2025) — see Phase 6 for a real
  case. Asking for a draft is the author's call to make each time, not a step the model assumes
  once an outline exists.
- **A rejected idea stays on record,** so a later session (or a different critic) doesn't
  re-propose it as if it were new.

## Project files

Keep a persistent story bible separate from scratch work, so sessions build on each other instead
of re-deciding things (the pattern behind danjdewhurst/story-skills and most of the tools in
`references/`). Default location: `docs/story/` in the project repo.

| File | Contents |
|---|---|
| `bible.md` | Only facts the author has settled, each with a source (a session date, a quote, a decision record) |
| `decisions.md` | What was kept, what was rejected, and why — in the author's words |
| `open-questions.md` | Everything not yet decided, including questions the author has deferred |
| `sessions/YYYY-MM-DD.md` | One note per working session |

**Every session starts by reading all four** (or the subset that exists) before asking anything —
re-asking a settled question, or re-proposing a rejected idea, is the clearest tell that the model
skipped this step. **Every session ends by updating them**, including `open-questions.md` when a
question got raised but not resolved. In the session note, mark which material came from the
author and which the model added or suggested (Borgen: "I didn't want anything in the book or any
character to be a mouthpiece for me" — the session note is how a later reader, human or model,
tells the two apart without re-deriving it).

If the project already keeps this state somewhere else (an existing bible, a wiki, issue tracker),
use that location instead of creating a second one — ask where it lives before assuming the
default.

## Phases

Run these roughly in order, but let the author's actual conversation lead — a session that starts
with "I know the ending now, let's outline toward it" skips the triage phase, and that's fine. What
doesn't change is the guardrails above, which apply regardless of phase.

### 1. Free talk

Before anything is organized, let the author talk or type unprompted for a few minutes about what
the story is for and what isn't working. The model only listens and asks short follow-up
questions — no structuring, no "so it sounds like the themes are X and Y" synthesis yet. Premature
organization is itself a form of the model supplying content: it picks which of the author's
words matter.

### 2. Triage an existing draft

If there's existing material (an old draft, a previous game's design docs, an abandoned
manuscript), go through it element by element — characters, locations, plot beats, mechanics,
whatever the story is made of — and have the author tag each one, in their own words:

- **Immutable** — stays as-is
- **Adaptable** — the idea is right, the execution needs work
- **Open** — undecided
- **Conflicted** — sources disagree, or the author is torn

This is zenstory-ai/novel-to-game's triage pattern (cite its source for every tag, require a
named reason before anything gets discarded). The model asks and records; it never argues for
keeping or cutting an element, including ones that look obviously weak — "trite" is the author's
judgment to make, not the model's to confirm.

### 3. Feeling, theme, and pillars

Get the target feeling, a one-sentence theme, and two or three narrative pillars, all in the
author's words. For a game, also test each pillar against the actual mechanics: does the mechanic
express the theme, and is there anything in the current design that works against it? See
`references/game-story.md` for why feeling and mechanic come before plot in games specifically,
and for the sources behind that claim.

### 4. Structure, staged

Build the outline in stages, each one reviewed by the author before the next starts: premise, then
a synopsis or beat sequence, then scene or chapter briefs, then (for games) each character's want
versus need and where any environmental-storytelling beats land. Staging matters because a flaw
caught at the premise stage costs nothing; the same flaw caught after scene briefs exist costs a
rewrite. This is where the "don't suggest what happens" guardrail is most tempting to break — an
author staring at a gap in the outline invites a helpful suggestion. Ask what should fill the gap
instead.

### 5. Critique as a list

Once a stage is drafted, a critic — ideally a different model or model family than the one that
drafted it, so it isn't grading its own work — reads the whole thing and returns a numbered list
of things to consider, not a score and not a rewrite. The author rules on each point individually.
A simulated reader reaction is useful but must be labeled simulated, not reported as a finding.

### 6. Words last, and only on request

The author writes the actual prose or dialogue by default. Through Phase 5 the model's job is
outline, brief, and critique — not composing the story's words. If the author wants a scene
drafted, that's a request made explicitly, scene by scene; it's never an assumed next step once an
outline exists.

When asked, draft only from an approved brief plus a voice sheet built from the author's own
sample lines for that character or narrator — not adjectives describing the voice, actual lines
(Novelcrafter's phrasing: "an essence of the character, not bits of dialogue for the AI to insert
verbatim"). Then cut in this order:

1. **Over-explanation first** — the single most common failure across every source that measured
   this; see `references/anti-slop.md`.
2. **A structural anti-slop pass** — the patterns in that same reference file, which are about
   shape and rhythm, not word choice.
3. **The author's own line-by-line pass** — this step doesn't get automated away; GDC's 2026
   editing panel put it plainly: "you cannot replace the touch of a human. Editing is [...]
   emotional consistency" ([Game Developer, 2026-03-12](https://www.gamedeveloper.com/design/making-the-case-for-strong-human-narrative-editors)).

Why drafting stays opt-in even with that process: readers experienced with LLM writing detect
AI-generated prose reliably — five of them together misclassified one article in 300 (Russell,
Karpinska and Iyyer, [arXiv:2501.15654](https://arxiv.org/abs/2501.15654), 2025). A real test
case: a 2026 novel drafted chapter-by-chapter with a model as the primary tool, then edited down
about 14% by hand, still read as AI-written to a share of its own readers — "this reads like it
was written by AI" ([Astral Codex Ten, 2026-07-20](https://www.astralcodexten.com/p/open-thread-443)) —
despite that editing pass. The cutting order above is still the right process once the author asks
for a draft; it isn't a reason to make model-drafted prose the default when they haven't.

## Reference files

- `references/anti-slop.md` — the structural failure patterns to cut for, with sources. Read this
  before any line pass (Phase 6).
- `references/game-story.md` — why feeling and theme lead mechanic in game narrative specifically,
  with sources. Read this during Phase 3 for a game project.
