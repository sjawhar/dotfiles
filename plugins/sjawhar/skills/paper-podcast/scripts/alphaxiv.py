#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""alphaXiv grounding lookup (unofficial public API at api.alphaxiv.org).

For each arXiv id, pulls:
  - similar-papers  (automated related-work discovery; works on bare arXiv ids)
  - community comments (replication results / critiques — OFTEN EMPTY for papers
    only days old; populates later as a paper gets traction)
  - alphaXiv's AI overview excerpt (its own structured summary)

  uv run alphaxiv.py --ids 2606.18193,2606.15531 --similar 6

Prints JSON: [{id, similar:[{title,abstract,id}], comments:[...], overview_excerpt}]

NOTE: alphaXiv publishes no official API contract; these are observed-stable
public endpoints. Treat failures as non-fatal — the skill's arXiv/librarian
grounding remains the source of truth for baseline numbers.
"""
import argparse
import json
import sys
import urllib.request
from urllib.error import HTTPError, URLError

BASE = "https://api.alphaxiv.org"
HDR = {"Accept": "application/json", "User-Agent": "paper-podcast-grounding/1.0"}


def _get(path: str):
    req = urllib.request.Request(f"{BASE}/{path}", headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def ground(arxiv_id: str, n_similar: int) -> dict:
    out: dict = {"id": arxiv_id, "similar": [], "comments": [], "overview_excerpt": "", "errors": []}

    try:
        sims = _get(f"papers/v3/{arxiv_id}/similar-papers")
        for s in sims[:n_similar]:
            out["similar"].append({
                "title": " ".join((s.get("title") or "").split()),
                "id": s.get("universal_paper_id") or s.get("paper_group_id") or s.get("id"),
                "abstract": " ".join((s.get("abstract") or "").split())[:600],
            })
    except (HTTPError, URLError, ValueError) as e:
        out["errors"].append(f"similar: {e}")

    group_id = version_id = None
    try:
        meta = _get(f"papers/v3/legacy/{arxiv_id}")
        paper = meta.get("paper", {})
        group_id = paper.get("paper_group", {}).get("id")
        version_id = paper.get("paper_version", {}).get("id")
    except (HTTPError, URLError, ValueError) as e:
        out["errors"].append(f"meta: {e}")

    if group_id:
        try:
            comments = _get(f"papers/v3/legacy/{group_id}/comments")
            for c in comments:
                body = " ".join((c.get("body") or c.get("content") or "").split())
                if body:
                    out["comments"].append(body[:800])
        except (HTTPError, URLError, ValueError) as e:
            out["errors"].append(f"comments: {e}")

    if version_id:
        try:
            ov = _get(f"papers/v3/{version_id}/overview/en")
            text = ov.get("overview") or ov.get("blog") or ov.get("body") or ""
            if isinstance(text, dict):
                text = json.dumps(text)
            out["overview_excerpt"] = " ".join(str(text).split())[:1200]
        except (HTTPError, URLError, ValueError) as e:
            out["errors"].append(f"overview: {e}")

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--similar", type=int, default=6)
    a = ap.parse_args()
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    print(json.dumps([ground(i, a.similar) for i in ids], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
