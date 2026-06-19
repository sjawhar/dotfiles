---
name: paper-podcast
description: Use when the user wants to turn research papers into a podcast episode (or "make a podcast" / "paper podcast" / "audio digest" / "research digest"). Works for any field. Takes a user-supplied paper source (arXiv query, arXiv IDs, or a PDF list), confirms the lineup with the user, extracts a faithful ground-truth JSON, grounds each paper against related work, writes a long-form two-host script, and renders an MP3 with Gemini 3.1 Flash TTS.
---

# Paper Podcast

Turn a configurable set of papers into a faithful, critical, long-form two-host
audio episode. The skill is topic-agnostic — the user supplies the subject and
the paper source; this skill never assumes a field.

**Key principle:** *You* (the agent) do the thinking — read the papers, ground
them against related work, extract the ground truth, write the script. The
bundled scripts do the mechanical work — fetch PDFs, search arXiv, synthesize
audio. Never outsource the extraction or script to a helper script, and never let
the audio contain claims that aren't in the ground-truth JSON.

**The ground-truth JSON is the real product.** The MP3 is one rendering of it.
The same JSON is what the user feeds to any voice-enabled AI to self-quiz on the
papers — so it must be accurate, structured, and complete.

**Three hard rules:**
- **Confirm the paper lineup with the user before extracting** (Phase 2). Never
  silently decide which papers make the episode.
- **Ground claims, don't parrot them** (Phase 4). A paper that claims it beats a
  baseline must be checked against what that baseline actually reports and against
  competing/prior/follow-up work.
- **Never write to the user's Google Drive or any external/cloud account.**
  Deliver locally. Upload only if the user explicitly asks, and confirm the
  destination first.

## Artifacts (all written under the output dir, default `./out`)

| File | Produced by | Purpose |
|------|-------------|---------|
| `manifest.json`, `text/<id>.txt`, `pdfs/<id>.pdf` | `fetch_papers.py` | downloaded candidates + full text |
| `ground-truth.json` | **you** | faithful structured extraction + grounding (also the quiz source) |
| `script.txt` | **you** | long-form two-host dialogue, speaker-labelled |
| `episode.mp3` | `tts.py` | the audio episode |

Helpers: `fetch_papers.py` (fetch + extract text), `alphaxiv.py` (alphaXiv:
similar-papers, comments, AI overview), `litground.py` (citation-graph grounding
via Semantic Scholar + OpenAlex: citation counts, influential citations, TLDRs,
reception), `arxiv_search.py` (arXiv metadata + abstracts), `tts.py` (render).

## Phase 0 — Read the feed config

Topics, queries, and per-feed instructions live in a well-known **Markdown** file,
NOT in this skill. Read it first:

1. Path: `$PAPER_PODCAST_CONFIG` if set, else `~/.config/paper-podcast/config.md`.
2. Choose the feed: the `## Feed: NAME` section the user named, else the one
   marked **Default feed** at the top.
3. From that feed section take the **Query** (the code block — use it verbatim),
   **Since**, **Max**, **Voices**, and the **Instructions** prose. **Carry the
   instructions into Phase 3 (what to emphasize when extracting) and Phase 5
   (script audience + style), and use the feed's voices in Phase 6.** A feed may
   instead list **IDs** or **PDFs** as its source.

If the config or the named feed is missing, tell the user and ask for the source —
never invent a topic. A user may also give a one-off source inline, which
overrides the config for that run.

## Phase 1 — Resolve the source into candidate papers

Use the source from the resolved feed (Phase 0). Set `SKILL` to the **absolute
path of this skill's directory** — you were given it when this skill loaded (the
folder containing this `SKILL.md` and its `scripts/`); never hardcode a relative
path or `.opencode/...`, since the skill can be installed anywhere. Then run the
matching source mode:

```bash
# arXiv query (use the feed's query / since_days / max from the config)
uv run "$SKILL/scripts/fetch_papers.py" --out ./out --since 7 --max 40 \
  --query '<feed query from config>'

# specific papers / arbitrary PDFs
uv run $SKILL/scripts/fetch_papers.py --out ./out --ids 2501.00001,2501.00002
uv run $SKILL/scripts/fetch_papers.py --out ./out --pdf-list papers.txt
```

Query hygiene (applies to any field):
- **Match abstracts, not just titles.** A `ti:`-only query silently misses papers
  whose relevance is in the abstract. If the user hands you a title-only query,
  widen each term to also match `abs:`. Gate broad or ambiguous terms with a
  field anchor to cut noise — e.g. `(ti:TERM OR abs:TERM) AND (abs:ANCHOR ...)`
  where ANCHOR names the field (so a generic word doesn't pull in other domains).
- **Fetch wide, then curate.** Pull ~40 and narrow to the final lineup in Phase 2,
  rather than capping low and missing strong papers.

Downloads PDFs, extracts full text, writes `out/manifest.json`.

## Phase 2 — Confirm the lineup with the user (MANDATORY)

Read `manifest.json` and present **every** candidate as a compact, numbered list:
title, arXiv id, primary category, one-line relevance note (flag tangential
ones). Show the full fetched set and your reasoning — do not silently pre-filter.
**Ask which papers to include or drop, and wait.** Do not extract or generate
anything until the user confirms. Keep only the confirmed papers.

## Phase 3 — Extract the ground truth (you do this)

Read each confirmed paper's `out/text/<id>.txt` (**full paper**, not the
abstract). Extract deeply — enough material to support ~8-12 minutes of dialogue
per paper, so capture MULTIPLE claims, the full mechanism, the experimental
setup, every headline number, and the authors' own limitations and open
questions. Anti-dilution rules:

- **Exact numbers, method names, benchmark names.** Capture them all here — the JSON is the complete quiz source, so it should be exhaustive. (Phase 5 decides which few are actually spoken aloud; the script never recites them all.) Never invent; omit if absent.
- **Preserve the authors' hedging.** Qualified claims stay qualified.
- **Limitations and open questions are first-class**, one set per paper.
- **Mechanism over vibes** — `evidence` explains *how* it works.

## Phase 4 — Ground each paper against related work (you do this)

Start with alphaXiv to discover related work automatically — its `similar-papers`
surfaces the relevant baselines/competing work, and (when present) community
`comments` are replication results and critiques straight from other researchers:

```bash
uv run $SKILL/scripts/alphaxiv.py --ids <paper arxiv ids> --similar 6
```

Caveat: for papers only days old, alphaXiv comments are usually empty (cold
start) and the AI overview may 404 — `similar-papers` is the reliable signal for
fresh papers; comments become valuable on older / high-traction papers.

Then ground the claims in the literature with the citation graph. For each
baseline the paper claims to beat (and key prior/competing work it cites), look
up how the field actually received it — citation count, influential-citation
count, TLDR — and, when useful, how it is cited:

```bash
uv run $SKILL/scripts/litground.py --ids <baseline arxiv ids the paper cites>
uv run $SKILL/scripts/litground.py --reception <a baseline arxiv id>   # how the field cites it
uv run $SKILL/scripts/arxiv_search.py --ids <baseline arxiv ids>       # abstracts + exact numbers
```

Brand-new papers lag indexing, so ground them via the OLDER works they cite
(those are indexed). Set SEMANTIC_SCHOLAR_API_KEY for reliable limits; OpenAlex is
the automatic fallback.

**Recency is not a red flag.** A new model, dataset, or paper is *by definition* under-represented in search and citation indexes simply because it is new — thin corroboration for a recent release is expected, not suspicious. Don't manufacture doubt about whether a recently-released thing is real, and don't treat "this is new" as if it were a finding. New releases are new; that is what makes them new. Verify what you reasonably can, note any genuine uncertainty in a single line, and move on.

This literature research IS part of the job, not optional. Dispatch `librarian`
sub-agents (in parallel, one per paper or theme) for the deeper search and synthesis.
Then, for each related work, decide whether it **corroborates** or
**contests/contextualizes** the paper's framing — e.g. is a baseline run in a
weakened or compute-capped setting, is the comparison metric a favorable one, has
a competing method already shown similar results? Record this in
`ground-truth.json`. Frame these as limitations that calibrate the real delta, not
as indictments: weak or unfavorably-configured baselines are near-universal in
this literature, so note the specific gap plainly and assume the authors acted in
good faith rather than dwelling on it as a failing.

Full schema, one object per paper (fields are general — adapt wording to the field):

```json
{
  "papers": [{
    "id": "2501.00001",
    "title": "...",
    "authors": ["..."],
    "context": "the problem setting — what the paper addresses and under what assumptions",
    "claims": [{
      "claim": "the assertion the paper makes",
      "evidence": "the experiment / mechanism / reasoning — HOW it works",
      "conclusion": "what it implies for the field",
      "numbers": [{"metric": "<metric>", "value": "X", "baseline": "<prior method> Y"}],
      "source": "§4.2"
    }],
    "limitations": ["what the authors admit doesn't work / where it breaks"],
    "open_questions": ["what the paper raises but does not answer"],
    "grounding": [{
      "related_id": "<arxiv id>",
      "related_title": "...",
      "relevance": "baseline | prior | competing | follow-up",
      "what_it_shows": "the specific claim/number from THAT work",
      "verdict": "corroborates | contests | contextualizes",
      "skeptic_note": "the substantive caveat or check a knowledgeable host would note — calm and specific, framed as a limitation rather than an accusation"
    }]
  }]
}
```

## Phase 5 — Write the script (you do this) — LONG-FORM

Write `out/script.txt` as a two-host dialogue, one turn per line,
`Speaker: text`. Exactly two speaker labels matching the `--voices` map (default
`Alex` and `Jordan`).

**Length: this is a deep dive, not a digest. Target ~8-12 minutes of dialogue
PER paper — roughly 1,200-1,800 spoken words per paper — so a 5-paper episode is
~45-60 minutes / ~7,000-9,000 words.** Do NOT compress to save effort; a thin
script is the #1 failure of this skill. As a rough calibration, the TTS speaks
~150 words/minute, so word count drives runtime directly.

Host dynamic — asymmetric, never two co-equal experts:
- **Alex** — a careful skeptic who checks claims against the `grounding` notes and
  asks what the real delta is ("the authors report X over prior method Y; Y's own
  paper reports Z under setting W, so the gap is narrower than the headline").
  Calm and precise, not adversarial — the aim is to calibrate a claim, not to
  prosecute the authors.
- **Jordan** — the explainer who walks through each mechanism and setup and
  conveys results mostly in qualitative terms (what's large, what's marginal, what
  it means), reaching for a specific number when the magnitude is the actual point.

Per paper, move through **claim → mechanism → significance → grounding/context →
limitations → open questions**, and spend real time on each. Lead with qualitative
meaning: the listener wants to understand what happened and why it matters more
than they want a recital of figures.

Tone and number discipline — this is where episodes most often go wrong:
- **Numbers earn airtime by changing the takeaway.** A contrast like 80% versus
  20% is worth stating because the gap carries the point; a run of decimals (85.42
  vs 0.7159 vs 22.9 …) does not — summarize those qualitatively ("roughly four
  times the baseline", "a large drop", "essentially unchanged") and speak at most
  the one or two figures that anchor the magnitude. The full numbers live in the
  JSON; the script is a guided tour, not a readout.
- **Measured register, not superlatives.** Avoid "the best / the worst / the
  most", "collapses", "extraordinary", "killer result", "staged", "damning". Prefer
  plain, specific language — "notably higher", "a meaningful gap", "weaker than it
  looks". Extremity reads as hype and erodes trust; understatement is more credible.
- **Limitations are limitations, not moral failings.** Weak or unfavorably-
  configured baselines are near-universal, so note the specific gap and move on
  without dwelling or implying misconduct — assume good faith.
- **Don't litigate recency.** A new model or recent paper having little
  search/citation footprint is expected, not a story; never spend script time
  doubting that a recently-released thing is real or "really" novel. If something
  is genuinely unverifiable, say so in a sentence and continue.

Conventions: open with a concrete hook; attribute to authors in third person; no
filler affirmations, no Q&A rhythm.

## Phase 6 — Render audio

```bash
uv run $SKILL/scripts/tts.py --script ./out/script.txt --out ./out/episode.mp3 \
  --voices "Alex=Kore,Jordan=Puck"
```

Vertex AI via `gcloud auth print-access-token` (no API key). A long episode means
many chunked TTS calls and several minutes — run it in the background and poll,
don't block on it.

## Phase 7 — Deliver (locally)

- Confirm `episode.mp3` plays and the duration matches the target (script prints it).
- Report the **local paths** of `episode.mp3` and `ground-truth.json`; remind the
  user they can paste the JSON into any voice-enabled AI to be quizzed.
- **No Drive / cloud upload unless the user explicitly asks** (then confirm where).

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Inventing or narrowing the query | The user supplies the source. Don't substitute your own topic; widen title-only queries to `abs:`. |
| Capping `--max` too low | Fetch wide (~40), curate in Phase 2. A low cap silently drops strong papers. |
| Choosing the papers yourself | Phase 2 is mandatory: present the full candidate list, let the user pick. |
| Thin / short script | Long-form: ~8-12 min per paper. Word count = runtime. Don't compress. |
| Parroting the authors | Phase 4 grounding: check claims against baselines + competing work. |
| Uploading to Drive / cloud unprompted | Deliver locally. Only upload if explicitly asked. |
| Extracting from the abstract only | Read the full `text/<id>.txt`. |
| Inventing numbers/benchmarks | Use only numbers present in the source; omit otherwise. |
| Dropping limitations / open questions | Mandatory per paper, in JSON and script. |
| Two co-equal hosts | Use the skeptic/explainer asymmetry. |
| Reciting every number | Speak only the figures whose magnitude carries the point; summarize the rest qualitatively. The complete numbers live in the JSON, not the audio. |
| Superlative / bombastic tone | Measured register. Avoid "best/worst/most", "collapses", "extraordinary". Understatement reads as more credible than hype. |
| Treating recency as suspicious | New releases and recent papers naturally have thin search/citation footprints. Don't doubt a recent thing is real or spend airtime litigating novelty. |
| Moralizing weak baselines | Weak/unfair baselines are near-universal. Note the specific gap to calibrate the real delta; assume good faith, don't allege misconduct. |

## Done when

1. The user confirmed the paper lineup (Phase 2).
2. `ground-truth.json` has, per paper: claims, limitations, open questions, AND grounding against related work.
3. `script.txt` is long-form (~8-12 min/paper) and covers claim→mechanism→numbers→critique→limitations→open-questions.
4. `episode.mp3` is produced, plays, and hits the target duration.
5. Artifacts reported by **local path** (no cloud upload unless the user asked).
