#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Literature grounding via citation graph — Semantic Scholar (primary) + OpenAlex (fallback).

Ground a paper's claims in the historical literature by looking up the OLDER
works it cites/compares against (those are indexed even when the new paper is not)
and how the field actually received them: citation counts, influential-citation
counts, TLDRs, and (optionally) citation contexts.

  uv run litground.py --ids 2307.15043,2310.08419,2312.02119
  uv run litground.py --reception 2307.15043 --limit 8   # how the field cites GCG

No community/comments needed. Set SEMANTIC_SCHOLAR_API_KEY for reliable rate limits
(free key); without one, S2 uses a shared pool that 429s often, so we fall back to
OpenAlex automatically.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

S2 = "https://api.semanticscholar.org/graph/v1"
OA = "https://api.openalex.org"
KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or os.environ.get("S2_API_KEY")
HDR = {"Accept": "application/json", "User-Agent": "paper-podcast-litground/1.0"}


def _get(url: str, key_header: bool = False, tries: int = 3):
    headers = dict(HDR)
    if key_header and KEY:
        headers["x-api-key"] = KEY
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.loads(r.read())
        except HTTPError as e:
            if e.code == 429 and i < tries - 1:
                time.sleep(1.5 * (i + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def s2_paper(arxiv_id: str) -> dict | None:
    fields = "title,year,venue,citationCount,influentialCitationCount,tldr,externalIds"
    try:
        d = _get(f"{S2}/paper/arXiv:{arxiv_id}?fields={fields}", key_header=True)
        tldr = (d.get("tldr") or {}).get("text") if isinstance(d.get("tldr"), dict) else None
        return {"source": "semantic_scholar", "title": d.get("title"), "year": d.get("year"),
                "venue": d.get("venue"), "citations": d.get("citationCount"),
                "influential_citations": d.get("influentialCitationCount"), "tldr": tldr}
    except (HTTPError, URLError, ValueError):
        return None


def openalex_paper(arxiv_id: str) -> dict | None:
    # Exact lookup by the arXiv-assigned DOI (10.48550/arXiv.<id>), NOT a fuzzy
    # text search — a search would return a wrong best-match and inject bad data.
    bare = arxiv_id.split("v")[0]
    try:
        w = _get(f"{OA}/works/doi:10.48550/arXiv.{bare}")
    except (HTTPError, URLError, ValueError):
        return None
    if not isinstance(w, dict) or not w.get("id"):
        return None
    src = (w.get("primary_location") or {}).get("source") or {}
    return {"source": "openalex", "title": w.get("title"), "year": w.get("publication_year"),
            "venue": src.get("display_name"), "citations": w.get("cited_by_count"),
            "influential_citations": None, "tldr": None}


def lookup(arxiv_id: str) -> dict:
    rec = s2_paper(arxiv_id) or openalex_paper(arxiv_id)
    return {"id": arxiv_id, **(rec or {"source": None, "error": "not found in S2 or OpenAlex"})}


def reception(arxiv_id: str, limit: int) -> dict:
    """How the field cites this paper: contexts + intents + influential flags."""
    fields = "contexts,intents,isInfluential,title,year"
    try:
        d = _get(f"{S2}/paper/arXiv:{arxiv_id}/citations?fields={fields}&limit={limit}", key_header=True)
        out = []
        for c in d.get("data", []):
            cp = c.get("citingPaper", {})
            out.append({"citing_title": cp.get("title"), "year": cp.get("year"),
                        "influential": c.get("isInfluential"), "intents": c.get("intents"),
                        "contexts": (c.get("contexts") or [])[:2]})
        # influential citations first
        out.sort(key=lambda x: (not x["influential"]))
        return {"id": arxiv_id, "citing": out}
    except (HTTPError, URLError, ValueError) as e:
        return {"id": arxiv_id, "error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="comma-separated arXiv ids to look up (the cited baselines)")
    ap.add_argument("--reception", help="single arXiv id: show how the field cites it")
    ap.add_argument("--limit", type=int, default=8)
    a = ap.parse_args()
    if a.reception:
        print(json.dumps(reception(a.reception, a.limit), indent=2))
    elif a.ids:
        ids = [x.strip() for x in a.ids.split(",") if x.strip()]
        print(json.dumps([lookup(i) for i in ids], indent=2))
    else:
        ap.error("provide --ids or --reception")
    return 0


if __name__ == "__main__":
    sys.exit(main())
