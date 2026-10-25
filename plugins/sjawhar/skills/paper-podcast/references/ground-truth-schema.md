# ground-truth.json schema

## Extraction rules

- Record every claim the paper states, the full mechanism, the setup, and every exact number, method name and benchmark name. Never invent one. Omit what the paper lacks.
- Keep the authors' hedging, so the script claims no more than the paper.
- Fill `limitations` and `open_questions` for every paper.
- `evidence` names the experiment, mechanism or reasoning behind the claim.

## Schema

One object per paper. Keep the JSON keys. Adapt the example values (metric names, units) to the paper's research area.

```json
{
  "papers": [{
    "id": "2501.00001",
    "title": "...",
    "authors": ["..."],
    "context": "problem setting and assumptions",
    "claims": [{
      "claim": "what the paper asserts",
      "evidence": "the experiment, mechanism or reasoning: HOW it works",
      "conclusion": "what it implies for the field",
      "numbers": [{"metric": "<metric>", "value": "X", "baseline": "<prior method> Y"}],
      "source": "§4.2"
    }],
    "limitations": ["what the authors admit fails or where it breaks"],
    "open_questions": ["what the paper raises but does not answer"],
    "grounding": [{
      "related_id": "<arxiv id>",
      "related_title": "...",
      "relevance": "baseline | prior | competing | follow-up",
      "what_it_shows": "the specific claim or number from THAT work",
      "verdict": "corroborates | contests | contextualizes",
      "skeptic_note": "the caveat a knowledgeable host would note: calm, specific, a limitation, not an accusation"
    }]
  }]
}
```
