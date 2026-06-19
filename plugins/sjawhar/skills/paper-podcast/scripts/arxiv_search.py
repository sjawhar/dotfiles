#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Lightweight arXiv lookup for grounding — returns metadata + abstracts, no PDF download.

  uv run arxiv_search.py --query 'ti:"greedy coordinate gradient"' --max 5
  uv run arxiv_search.py --ids 2307.15043,2312.02119

Prints JSON: [{id, title, authors, published, primary_category, abstract, abs_url}]
Use it to pull the baselines/related work a paper compares against, so the
podcast can check claims instead of repeating them.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ATOM = "{http://www.w3.org/2005/Atom}"
ARX = "{http://arxiv.org/schemas/atom}"
API = "http://export.arxiv.org/api/query"
UA = {"User-Agent": "paper-podcast-grounding/1.0"}


def query_arxiv(params: dict) -> list[dict]:
    url = f"{API}?{urllib.parse.urlencode(params)}"
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
    root = ET.fromstring(raw)
    out = []
    for e in root.findall(f"{ATOM}entry"):
        abs_url = (e.findtext(f"{ATOM}id") or "").strip()
        pc = e.find(f"{ARX}primary_category")
        out.append({
            "id": abs_url.rsplit("/", 1)[-1],
            "title": " ".join((e.findtext(f"{ATOM}title") or "").split()),
            "authors": [a.findtext(f"{ATOM}name", "").strip() for a in e.findall(f"{ATOM}author")],
            "published": (e.findtext(f"{ATOM}published") or "")[:10],
            "primary_category": pc.get("term") if pc is not None else "",
            "abstract": " ".join((e.findtext(f"{ATOM}summary") or "").split()),
            "abs_url": abs_url,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--query")
    g.add_argument("--ids")
    ap.add_argument("--max", type=int, default=6)
    a = ap.parse_args()

    if a.query:
        params = {"search_query": a.query, "sortBy": "relevance", "max_results": str(a.max)}
    else:
        ids = [x.strip() for x in a.ids.split(",") if x.strip()]
        params = {"id_list": ",".join(ids), "max_results": str(max(len(ids), 1))}

    print(json.dumps(query_arxiv(params), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
