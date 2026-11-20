---
name: paper-podcast
description: Use when the user wants research papers turned into a podcast episode. Also use when asked to "make a podcast", a "paper podcast", an "audio digest" or a "research digest". Applies to any field and to an arXiv query, arXiv IDs or a PDF list.
---

# Paper Podcast

Turn papers into a faithful, critical, long-form two-host MP3.

- You read, ground, extract and write. Scripts only fetch, search and render: a script that summarizes a paper invents numbers.
- `out/ground-truth.json` is the real product. The user also quizzes themselves from it with a voice AI, so record every claim, number and limitation the script speaks, each claim with its `source`. The audio states no claim that is not in it.
- Deliver locally. Do not write to the user's Google Drive or any cloud account; upload only when the user asks, and ask where first.
- Set `SKILL` to the absolute path of this skill's directory, given when the skill loaded; a relative path breaks when the session's directory differs. Output goes to `./out` by default. Each script in `$SKILL/scripts/` takes `--help`.

## Phase 0: resolve the source

- The user's inline query, IDs, PDFs, instructions and voices override the config.
- Config: `$PAPER_PODCAST_CONFIG` or `~/.config/paper-podcast/config.md`. Use the named `## Feed: NAME`, else the Default feed. Take its verbatim Query (or IDs/PDFs), Since, Max, Voices and Instructions. Apply instructions in Phases 3 and 5, voices in Phase 6.
- No source from either: ask. Do not invent a topic or assume a field.

## Phase 1: fetch candidates

```bash
uv run "$SKILL/scripts/fetch_papers.py" --out ./out --since 7 --max 40 --query '<feed query>'
# or --ids 2501.00001,2501.00002, or --pdf-list papers.txt
```

- It writes `out/manifest.json`, `out/pdfs/<id>.pdf` and full text in `out/text/<id>.txt`.
- Take `--since` and `--max` from the feed, and fetch wide (about 40): Phase 2 curates.
- Match abstracts, not only titles: widen every `ti:` term to `(ti:TERM OR abs:TERM)`, also in a user's query. AND a term other fields also use with one only this field uses: `... AND (abs:ANCHOR ...)`.

## Phase 2: the user picks the lineup

- List every candidate in `out/manifest.json`, numbered: title, arXiv ID, primary category, one-line relevance note. Flag tangential ones. Do not pre-filter.
- Ask which papers to keep or drop, and wait. Extract and write nothing until the user answers. Keep only the papers they choose.

## Phase 3: extract the ground truth

Read each paper's full `out/text/<id>.txt`, not the abstract, and capture enough for about 8-12 minutes of dialogue per paper:

- Every claim the paper states, the full mechanism, the setup, and every exact number, method name and benchmark name. Invent none; omit what the paper lacks.
- The authors' hedging, so the script claims no more than the paper.
- `limitations` and `open_questions` for every paper.

Write `out/ground-truth.json` in this shape, one object per paper. Keep the keys; adapt the example values (metric names, units) to the field.

```json
{"papers": [{
  "id": "2501.00001", "title": "...", "authors": ["..."],
  "context": "problem setting and assumptions",
  "claims": [{
    "claim": "what the paper asserts",
    "evidence": "the experiment, mechanism or reasoning: how it works",
    "conclusion": "what it implies for the field",
    "numbers": [{"metric": "<metric>", "value": "X", "baseline": "<prior method> Y"}],
    "source": "§4.2"}],
  "limitations": ["what the authors admit fails or where it breaks"],
  "open_questions": ["what the paper raises but does not answer"],
  "grounding": [{
    "related_id": "<arxiv id>", "related_title": "...",
    "relevance": "baseline | prior | competing | follow-up",
    "what_it_shows": "the specific claim or number from that work",
    "verdict": "corroborates | contests | contextualizes",
    "skeptic_note": "the caveat a knowledgeable host would note: calm, specific, a limitation, not an accusation"}]
}]}
```

## Phase 4: ground each paper

1. `uv run "$SKILL/scripts/alphaxiv.py" --ids <ids> --similar 6`. Similar papers show baselines and competing work; comments hold replications and critiques. For papers only days old, comments are usually empty and the AI overview can 404: rely on similar papers.
2. For each baseline the paper claims to beat, and each prior, competing or follow-up work it compares against, fetch its reception and its own numbers:
   ```bash
   uv run "$SKILL/scripts/litground.py" --ids <baseline ids>       # citations, influential citations, TLDRs
   uv run "$SKILL/scripts/litground.py" --reception <baseline id>  # if TLDRs and counts leave its reception unclear
   uv run "$SKILL/scripts/arxiv_search.py" --ids <baseline ids>    # abstracts, exact numbers
   ```
   `litground.py`'s docstring covers why it reads the older works, its API key and its fallback.
3. Dispatch `scout` subagents with the `task` tool in parallel, one per paper, for replications, critiques and competing results steps 1-2 missed.
4. Record in `grounding` whether each related work corroborates, contests or contextualizes the framing. Look for a weakened or compute-capped baseline, a favorable metric, or a competitor with similar results.

- Report a weak baseline as a limitation, with the stronger number from its own paper. State it plainly and assume good faith.
- Recency is not a red flag, here or in the script. Do not doubt a result for being new, or present "it is new" as a finding. Check what you can; if nothing has tested a result yet, say so in one line.

## Phase 5: write the script

Write `out/script.txt`: one `Speaker: text` turn per line, exactly two labels matching `--voices` (default `Alex`, `Jordan`).

- Length: roughly 1,200-1,800 spoken words (8-12 minutes) per paper, about 7,000-9,000 for five papers, at about 150 spoken words a minute. Count words per paper before render: under 1,200 means a part below is missing.
- Alex is a calm, precise skeptic who tests claims against `grounding` and asks for the real delta ("Y's own paper reports Z under setting W, so the gap is narrower"). Jordan explains each mechanism and setup, and gives results mostly in qualitative terms. Not two co-equal experts: the listener needs one host who asks and one who explains.
- Per paper, give real time to claim → mechanism → significance → grounding → limitations → open questions. Lead with what happened and why it matters.
- Speak a number only when the claim depends on its size; say the rest as "better than X" or "about the same as X".
- No superlatives or hype words ("groundbreaking"). State limitations plainly, assume good faith, and do not allege misconduct.
- Open with the most surprising result and its number. Attribute to the authors in third person. No "Great point" openers; Alex also challenges with `grounding` facts, so turns are not short question, long answer.

## Phase 6: render

```bash
uv run "$SKILL/scripts/tts.py" --script ./out/script.txt --out ./out/episode.mp3 --voices "Alex=Kore,Jordan=Puck"
```

`tts.py`'s docstring covers auth and billing. A long episode takes several minutes: run it in the background and keep working.

## Phase 7: deliver locally

- `ffprobe out/episode.mp3`: duration should be near word count / 150 minutes; much shorter means audio was lost.
- Report the local paths of `episode.mp3` and `ground-truth.json`. Tell the user they can paste the JSON into any voice AI to be quizzed.
