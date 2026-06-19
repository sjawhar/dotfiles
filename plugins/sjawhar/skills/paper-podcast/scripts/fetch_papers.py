#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf"]
# ///
"""Resolve a paper source into downloaded PDFs + extracted full text + a manifest.

Sources (pick one):
  --query "<arxiv query>"    arXiv API search_query (e.g. 'ti:TERM OR abs:TERM')
  --ids   2606.1,2606.2      comma-separated arXiv IDs
  --pdf-list FILE            file with one PDF URL or local path per line

Common:
  --since DAYS   (query only) keep papers submitted within N days (default: no filter)
  --max N        cap number of papers (default 40)
  --out DIR      output directory (default ./out)

Writes: <out>/pdfs/<id>.pdf, <out>/text/<id>.txt, <out>/manifest.json
The manifest is the handoff to the extraction step.
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fitz  # pymupdf

ATOM = "{http://www.w3.org/2005/Atom}"
ARX = "{http://arxiv.org/schemas/atom}"
API = "http://export.arxiv.org/api/query"
UA = {"User-Agent": "paper-podcast/1.0 (mailto:noreply@example.com)"}


def _get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90).read()


def _arxiv(params: dict) -> list[dict]:
    url = f"{API}?{urllib.parse.urlencode(params)}"
    root = ET.fromstring(_get(url))
    out = []
    for e in root.findall(f"{ATOM}entry"):
        abs_url = (e.findtext(f"{ATOM}id") or "").strip()
        pdf = ""
        for ln in e.findall(f"{ATOM}link"):
            if ln.get("title") == "pdf":
                pdf = ln.get("href", "")
        pc = e.find(f"{ARX}primary_category")
        out.append({
            "id": abs_url.rsplit("/", 1)[-1],
            "title": " ".join((e.findtext(f"{ATOM}title") or "").split()),
            "authors": [a.findtext(f"{ATOM}name", "").strip() for a in e.findall(f"{ATOM}author")],
            "abstract": " ".join((e.findtext(f"{ATOM}summary") or "").split()),
            "abs_url": abs_url,
            "pdf_url": pdf or abs_url.replace("/abs/", "/pdf/"),
            "published": (e.findtext(f"{ATOM}published") or "").strip(),
            "primary_category": pc.get("term") if pc is not None else "",
        })
    return out


def from_query(query: str, since: int | None, cap: int) -> list[dict]:
    entries = _arxiv({"search_query": query, "sortBy": "submittedDate",
                      "sortOrder": "descending", "max_results": "200"})
    if since is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since)
        kept = []
        for e in entries:
            try:
                dt = datetime.strptime(e["published"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if dt >= cutoff:
                kept.append(e)
        entries = kept
    return entries[:cap]


def from_ids(ids: list[str], cap: int) -> list[dict]:
    return _arxiv({"id_list": ",".join(ids), "max_results": str(max(len(ids), 1))})[:cap]


def from_pdf_list(path: str, cap: int) -> list[dict]:
    out = []
    for line in Path(path).read_text().splitlines():
        loc = line.strip()
        if not loc or loc.startswith("#"):
            continue
        pid = re.sub(r"[^A-Za-z0-9._-]", "_", Path(loc).stem)[:60] or f"paper{len(out)}"
        out.append({"id": pid, "title": Path(loc).stem, "authors": [], "abstract": "",
                    "abs_url": loc, "pdf_url": loc, "published": "", "primary_category": ""})
    return out[:cap]


def fetch_pdf(pdf_url: str, dest: Path) -> None:
    if pdf_url.startswith(("http://", "https://")):
        dest.write_bytes(_get(pdf_url))
    else:
        dest.write_bytes(Path(pdf_url).expanduser().read_bytes())


def extract_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    return "\n".join(page.get_text() for page in doc)


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--query")
    g.add_argument("--ids")
    g.add_argument("--pdf-list")
    ap.add_argument("--since", type=int, default=None)
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--out", default="./out")
    a = ap.parse_args()

    if a.query:
        papers = from_query(a.query, a.since, a.max)
    elif a.ids:
        papers = from_ids([x.strip() for x in a.ids.split(",") if x.strip()], a.max)
    else:
        papers = from_pdf_list(a.pdf_list, a.max)

    out = Path(a.out)
    (out / "pdfs").mkdir(parents=True, exist_ok=True)
    (out / "text").mkdir(parents=True, exist_ok=True)

    manifest = []
    for i, p in enumerate(papers, 1):
        pid = p["id"]
        pdf_path = out / "pdfs" / f"{pid}.pdf"
        txt_path = out / "text" / f"{pid}.txt"
        try:
            fetch_pdf(p["pdf_url"], pdf_path)
            text = extract_text(pdf_path)
            txt_path.write_text(text)
            p["text_path"] = str(txt_path)
            p["text_chars"] = len(text)
            print(f"[{i}/{len(papers)}] {pid}: {len(text)} chars  {p['title'][:70]}")
        except Exception as exc:  # noqa: BLE001 - report and skip, don't abort batch
            p["error"] = str(exc)
            print(f"[{i}/{len(papers)}] {pid}: FAILED — {exc}", file=sys.stderr)
        manifest.append(p)

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    ok = sum(1 for p in manifest if "text_path" in p)
    print(f"\nmanifest: {out / 'manifest.json'} — {ok}/{len(manifest)} papers with text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
