---
name: paper-podcast
description: Use when the user wants to turn research papers into a podcast episode. Also use when asked to "make a podcast", a "paper podcast", an "audio digest" or a "research digest". Applies to any field and to an arXiv query, arXiv IDs or a PDF list.
---

# Paper Podcast

Turn papers into a faithful, critical, long-form two-host MP3. Never assume a field. The user gives the topic and the source.

- You read, ground, extract and write. Scripts only fetch, search and render. Never give extraction or script writing to a script.
- `ground-truth.json` is the real product. The user also quizzes themselves from it with a voice AI, so keep it accurate, structured and complete. The audio states no claim that is not in it.
- Never write to the user's Google Drive or any cloud account. Deliver locally. Upload only when the user asks, and ask where first.

Set `SKILL` to the absolute path of this skill's directory, given when the skill loaded. Never hardcode a relative path or `.claude/...`. Output goes to `./out` by default. Each script in `$SKILL/scripts/` takes `--help`.

## Phase 0 — Resolve the source

- The user's inline query, IDs, PDFs, instructions and voices override the config.
- Config: `$PAPER_PODCAST_CONFIG` or `~/.config/paper-podcast/config.md`. Use the named `## Feed: NAME`, else the **Default feed**. Take its verbatim **Query** (or **IDs**/**PDFs**), **Since**, **Max**, **Voices** and **Instructions**. Apply instructions in Phases 3 and 5, voices in Phase 6.
- No source from either? Ask. Never invent a topic.

## Phase 1 — Fetch candidates

```bash
uv run "$SKILL/scripts/fetch_papers.py" --out ./out --since 7 --max 40 --query '<feed query>'
# or --ids 2501.00001,2501.00002, or --pdf-list papers.txt
```

- It writes `out/manifest.json`, `out/pdfs/<id>.pdf` and full text in `out/text/<id>.txt`.
- Take `--since` and `--max` from the feed.
- Match abstracts, not only titles. Widen every `ti:` term to `(ti:TERM OR abs:TERM)`, also in a user's query. Gate broad or ambiguous terms with a field anchor: `... AND (abs:ANCHOR ...)`.
- Fetch wide (about 40), then curate in Phase 2.

## Phase 2 — The user picks the lineup (mandatory)

- List every candidate in `out/manifest.json`, numbered: title, arXiv ID, primary category, one-line relevance note. Flag tangential ones. Show your reasoning. Do not pre-filter.
- Ask which papers to keep or drop, and wait. Extract and write nothing until the user answers. Keep only the papers they choose.

## Phase 3 — Extract the ground truth

Read each paper's full `out/text/<id>.txt`, not the abstract. Capture enough for about 8-12 minutes of dialogue per paper. Write `out/ground-truth.json` in the [schema](references/ground-truth-schema.md).

- Record several claims, the full mechanism, the setup, and every exact number, method name and benchmark name. Never invent one. Omit what the paper lacks.
- Keep the authors' hedging.
- Limitations and open questions are first-class, one set per paper.
- `evidence` explains how it works, not vibes.

## Phase 4 — Ground each paper (required)

1. `uv run "$SKILL/scripts/alphaxiv.py" --ids <ids> --similar 6`. Similar papers show baselines and competing work. Comments hold replications and critiques. For papers only days old, comments are usually empty and the AI overview can 404: rely on similar papers.
2. For each baseline the paper claims to beat, and key prior, competing or follow-up work it cites, fetch its reception and its own numbers:
   ```bash
   uv run "$SKILL/scripts/litground.py" --ids <baseline ids>       # citations, influential citations, TLDRs
   uv run "$SKILL/scripts/litground.py" --reception <baseline id>  # when useful: how the field cites it
   uv run "$SKILL/scripts/arxiv_search.py" --ids <baseline ids>    # abstracts, exact numbers
   ```
   New papers lag indexing, so ground them through the older works they cite. Set `SEMANTIC_SCHOLAR_API_KEY` for reliable rate limits. OpenAlex is the automatic fallback.
3. Dispatch `scout` subagents with the `task` tool in parallel, one per paper or theme, for deeper search and synthesis.
4. Record in `grounding` whether each related work corroborates, contests or contextualizes the framing. Look for a weakened or compute-capped baseline, a favorable metric, or a competitor with similar results.

- Frame a gap as a limitation that calibrates the real delta, not an indictment. Weak baselines are near-universal. State the gap plainly and assume good faith.
- Recency is not a red flag, here or in the script. New releases have thin corroboration. Do not manufacture doubt that one is real or present "it is new" as a finding. Check what you can, note real uncertainty in one line, move on.

## Phase 5 — Write the script (long-form)

Write `out/script.txt`: one `Speaker: text` turn per line, exactly two labels matching `--voices` (default `Alex`, `Jordan`).

- Length: roughly 1,200-1,800 spoken words (8-12 minutes) per paper, about 7,000-9,000 for five papers. TTS speaks about 150 words a minute. Never compress to save effort. A thin script is this skill's top failure.
- Alex is a calm, precise skeptic. Alex tests claims against `grounding` and asks for the real delta ("Y's own paper reports Z under setting W, so the gap is narrower"). Calibrate, never prosecute.
- Jordan explains each mechanism and setup, and gives results mostly in qualitative terms. Jordan uses a number when the magnitude is the point.
- Never two co-equal experts.
- Per paper, give real time to claim → mechanism → significance → grounding → limitations → open questions. Lead with what happened and why it matters.
- Speak only numbers whose magnitude changes the takeaway. Summarize the rest.
- Measured, specific language. No superlatives or hype. State limitations plainly, assume good faith, and never allege misconduct.
- Open with a concrete hook. Attribute to the authors in third person. No filler affirmations, no Q&A rhythm.

## Phase 6 — Render

```bash
uv run "$SKILL/scripts/tts.py" --script ./out/script.txt --out ./out/episode.mp3 --voices "Alex=Kore,Jordan=Puck"
```

Auth: Vertex AI through `gcloud auth print-access-token`, no API key. A long episode takes several minutes. Run it in the background and check its progress. Do not block on it.

## Phase 7 — Deliver locally

- Check that `episode.mp3` plays and that its printed duration matches the target.
- Report the local paths of `episode.mp3` and `ground-truth.json`. Tell the user they can paste the JSON into any voice AI to be quizzed.
