# Fetch related work

Read at Phase 4, step 2, for each baseline the paper claims to beat, and each prior, competing or follow-up work it compares against.

```bash
uv run "$SKILL/scripts/litground.py" --ids <baseline ids>       # citations, influential citations, TLDRs
uv run "$SKILL/scripts/litground.py" --reception <baseline id>  # when its --ids record has no tldr, or influential_citations is null or 0
uv run "$SKILL/scripts/arxiv_search.py" --ids <baseline ids>    # abstracts, exact numbers
```
