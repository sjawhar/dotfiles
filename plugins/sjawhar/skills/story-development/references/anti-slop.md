# Structural anti-slop patterns

Banned-word lists (don't write "delve," "leverage," "tapestry") catch density, not the actual
problem: "the problem is density and reflex, not any single occurrence," and "the fix is never
just deleting tics; it's reinstating the concrete detail the slop displaced" — the de-slopify
skill in [Dicklesworthstone/agent_flywheel_clawdbot_skills_and_integrations](https://github.com/Dicklesworthstone/agent_flywheel_clawdbot_skills_and_integrations).
The patterns below are structural instead: they're about shape, pacing and rhythm across a whole
piece, and they survive prompt engineering that only targets vocabulary.

## The list

1. **Over-explanation.** The single most common failure
   ([NousResearch/autonovel](https://github.com/NousResearch/autonovel)'s own ranking) — stating a
   feeling or a theme the scene has already shown. Cut this first; cutting it is usually most of
   the needed edit (Borgen: "mostly manually and through AI helping me deciding what to cut...
   take away some of the stuff that AI does particularly badly, the text, the overexplanation").
2. **Reflexive groups of three.** Three examples, three adjectives, three items in a list, by
   default rather than because three is the right number here.
3. **"Not X but Y" antithesis**, reached for as a rhetorical shape rather than because the
   contrast is the actual point.
4. **Uniform paragraph length.** Real prose varies; a paragraph that's always four to six
   sentences is a tell, not a style.
5. **Beats landing exactly where the outline put them.** An outline is a plan, not a contract —
   prose that hits every outline beat on schedule, with no beat arriving early, late, or
   differently than planned, reads as mechanical.
6. **Every chapter or scene ending the same way** (a cliffhanger, a wry line, a callback)
   regardless of what that particular scene needs.
7. **Resolving tension too early.** Traced to helpfulness training — a model answers the question
   a scene raised instead of sitting in it
   ([haowjy/creative-writing-skills](https://github.com/haowjy/creative-writing-skills), Apache-2.0).
8. **Collapsing deliberate ambiguity.** The same training pressure makes a model resolve an
   ambiguity the author placed there on purpose, because an unresolved question reads to the model
   like an incomplete answer.

## Scoring and reader reactions

- **Don't score critique 1-10.** "Standard 1-10 scoring collapses to a 2-point band" — compare
  options or passages head-to-head instead (NousResearch/autonovel).
- **A tiered pass helps for prose specifically** (not plot or structure): triage flagged lines by
  how bad they are before deciding what to fix first — P0 credibility killers, P1 obvious tells,
  P2 stylistic polish — rather than treating every flagged phrase as equally urgent
  ([conorbronsdon/avoid-ai-writing](https://github.com/conorbronsdon/avoid-ai-writing), MIT).
- **Experienced readers can tell, and disclosure is the low-risk path.** Annotators who regularly
  use LLMs for writing "excel at detecting AI-generated text" — five of them together
  misclassified one in 300 articles (Russell, Karpinska and Iyyer,
  [arXiv:2501.15654](https://arxiv.org/abs/2501.15654), 2025). Automated detectors do much worse:
  seven GPT detectors flagged over half of a set of non-native English essays as AI-written
  (average false positive rate 61%; Liang et al., *Patterns*, 2023,
  [arXiv:2304.02819](https://arxiv.org/abs/2304.02819)), so treat any detector's output as a
  signal to investigate, not a verdict.

## Why diversity is the fix, not a word filter

AI story ideas made individual stories rated more novel and better written, but stories written
with AI help converged on each other and on the AI's own idea — a gap equal to roughly 9-11% of
the spread you'd see among human-only stories — while writers' own ratings of their stories'
novelty showed no difference between groups (Doshi and Hauser, *Science Advances*,
[full text](https://pmc.ncbi.nlm.nih.gov/articles/PMC11244532/), 2024; their setup was a one-shot,
single-voice idea prompt). Giving writers plots from ten deliberately different personas instead
of one restored story diversity to or above the human-only baseline (Wan and Kalman,
[arXiv:2504.13868](https://arxiv.org/abs/2504.13868), 2025). That's the evidence behind this
skill's "many options, varied angles, unranked" rule for whenever the author does ask for options
— a single suggestion, however well-written, is the convergence risk in miniature.
