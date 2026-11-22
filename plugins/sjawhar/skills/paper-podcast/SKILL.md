---
name: paper-podcast
description: Use when the user wants research papers turned into a podcast episode. Also use when asked to "make a podcast", a "paper podcast", an "audio digest" or a "research digest". Applies to any field and to an arXiv query, arXiv IDs or a PDF list.
---

# Paper Podcast

Turn papers into a faithful, critical, long-form two-host MP3.

**Done when:** `ffprobe out/episode.mp3` shows a duration within 20% of word count / 150 minutes; more than 20% shorter means audio was lost.

- You read, ground, extract and write. Scripts only fetch, search and render: a script that summarizes a paper invents numbers.
- Record in `out/ground-truth.json`, the real product, because the user also quizzes themselves from it with a voice AI:
  - every claim, number and limitation the script speaks;
  - each claim's `source`.
- State no claim in the audio that is not in `out/ground-truth.json`.
- **Deliver locally.** Do not write to the user's Google Drive or any cloud account.
- Upload only when the user asks.
- Ask the user where before you upload.
- Set `SKILL` to the absolute path of this skill's directory, given when the skill loaded: a relative path breaks when the session's directory differs.

Output goes to `./out` by default. Each script in `$SKILL/scripts/` takes `--help`.

## Phase 0: resolve the source

- The user's inline query, IDs, PDFs, instructions and voices override the config.
- **Config** (`$PAPER_PODCAST_CONFIG` or `~/.config/paper-podcast/config.md`):
  - Use the named `## Feed: NAME`, else the Default feed.
  - Take its verbatim Query (or IDs/PDFs), Since, Max, Voices and Instructions.
  - Apply instructions in Phases 3 and 5.
  - Apply voices in Phase 6.
- No source from either: ask.
  - Do not invent a topic or assume a field.

## Phase 1: fetch candidates

```bash
uv run "$SKILL/scripts/fetch_papers.py" --out ./out --since 7 --max 40 --query '<feed query>'
# or --ids 2501.00001,2501.00002, or --pdf-list papers.txt
```

It writes `out/manifest.json`, `out/pdfs/<id>.pdf` and full text in `out/text/<id>.txt`.

- Pass the feed's Since as `--since` and its Max as `--max`, as given.
- With no Max, keep the default `--max 40`: fetch wide, because Phase 2 curates.
- Match abstracts, not only titles: widen every `ti:` term to `(ti:TERM OR abs:TERM)`, also in a user's query.
- AND a term other fields also use with one only this field uses: `... AND (abs:ANCHOR ...)`.

## Phase 2: the user picks the lineup

- List every candidate in `out/manifest.json`, numbered: title, arXiv ID, primary category, one-line relevance note.
- Flag tangential candidates.
- Do not pre-filter.
- Ask which papers to keep or drop, and wait.
- Extract and write nothing until the user answers.
- Keep only the papers the user chooses.

## Phase 3: extract the ground truth

Read each paper's full `out/text/<id>.txt`, not the abstract. Capture enough for about 8-12 minutes of dialogue per paper:

- Every claim the paper states, the full mechanism, the setup, and every exact number, method name and benchmark name.
  - Invent none.
  - Omit what the paper lacks.
- The authors' hedging, so the script claims no more than the paper.
- `limitations` and `open_questions` for every paper.

Write `out/ground-truth.json` in this shape, one object per paper. Keep the keys. Adapt the example values (metric names, units) to the field.

**Read `references/ground-truth-shape.md` now**: it holds this shape as JSON, with an example value for each of these keys per paper:

- `id`, `title`, `authors` and `context`.
- `claims`, each with `claim`, `evidence`, `conclusion`, `numbers` (`metric`, `value`, `baseline`) and `source`.
- `limitations` and `open_questions`.
- `grounding`, each with `related_id`, `related_title`, `relevance` (baseline | prior | competing | follow-up), `what_it_shows`, `verdict` (corroborates | contests | contextualizes) and `skeptic_note` (the caveat a knowledgeable host would note: calm, specific, a limitation, not an accusation).

## Phase 4: ground each paper

This phase is required for every kept paper, before Phase 5: Alex's challenges come from `grounding`.

1. **Search alphaXiv.** Run `uv run "$SKILL/scripts/alphaxiv.py" --ids <ids> --similar 6`. Similar papers show baselines and competing work. Comments hold replications and critiques.
   - For papers only days old, rely on similar papers: comments are usually empty and the AI overview can 404.
2. **Fetch related work.** For each baseline the paper claims to beat, and each prior, competing or follow-up work it compares against, fetch its reception and its own numbers:
   ```bash
   uv run "$SKILL/scripts/litground.py" --ids <baseline ids>       # citations, influential citations, TLDRs
   uv run "$SKILL/scripts/litground.py" --reception <baseline id>  # when its --ids record has no tldr, or influential_citations is null or 0
   uv run "$SKILL/scripts/arxiv_search.py" --ids <baseline ids>    # abstracts, exact numbers
   ```
   `litground.py`'s docstring covers why it reads the older works, its API key and its fallback.
3. **Dispatch `scout` subagents** in parallel, one per paper, for replications, critiques and competing results steps 1-2 missed.
   - Call the `task` tool with `agent: "scout"`. `scout` is omp's bundled read-only agent.
4. **Record the verdicts.** Record in `grounding` whether each related work corroborates, contests or contextualizes the framing.
   - Look for a weakened or compute-capped baseline, a favorable metric, or a competitor with similar results.

- Report a weak baseline as a limitation, with the stronger number from its own paper.
  - State it plainly.
  - Assume good faith.
- **Recency is not a red flag, here or in the script.**
  - Do not doubt a result for being new.
  - Do not present "it is new" as a finding.
  - Check what you can.
  - If nothing has tested a result yet, say so in one line.

## Phase 5: write the script

Write `out/script.txt`: one `Speaker: text` turn per line. Use exactly two labels, matching `--voices` (default `Alex`, `Jordan`).

- Length: roughly 1,200-1,800 spoken words (8-12 minutes) per paper, about 7,000-9,000 for five papers, at about 150 spoken words a minute.
- Count words per paper before render: under 1,200 means a part below is missing.

| Host | Role |
| --- | --- |
| Alex | A calm, precise skeptic who tests claims against `grounding` and asks for the real delta ("Y's own paper reports Z under setting W, so the gap is narrower") |
| Jordan | Explains each mechanism and setup, and gives results mostly in qualitative terms |

- Not two co-equal experts: the listener needs one host who asks and one who explains.
- Per paper, give each part of claim → mechanism → significance → grounding → limitations → open questions at least 100 spoken words.
  - Lead with what happened and why it matters.
- Speak a number only when the claim depends on its size; say the rest as "better than X" or "about the same as X".
- Use no superlatives or hype words ("groundbreaking").
- State limitations plainly.
- Assume good faith.
- Do not allege misconduct.
- Open with the most surprising result and its number.
- Attribute to the authors in third person.
- Use no "Great point" openers.
- Alex also challenges with `grounding` facts, so turns are not short question, long answer.

## Phase 6: render

```bash
PYTHONUNBUFFERED=1 uv run "$SKILL/scripts/tts.py" --script ./out/script.txt --out ./out/episode.mp3 --voices "Alex=Kore,Jordan=Puck"
```

`tts.py`'s docstring covers auth and billing. A long episode takes several minutes:

- Run it in the background.
- Keep working while it runs.
- Check its progress in the `chunk i/n` line it prints per TTS call. `PYTHONUNBUFFERED=1` makes each line appear when printed, not at the end.

## Phase 7: deliver locally

- Check the duration as the Done when line says.
- Report the local paths of `episode.mp3` and `ground-truth.json`.
- Tell the user they can paste the JSON into any voice AI to be quizzed.
