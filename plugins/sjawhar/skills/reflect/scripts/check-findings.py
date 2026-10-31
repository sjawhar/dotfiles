#!/usr/bin/env python3
"""Check reader output against the record list: every record read, every quote in its record.

    check-findings.py RECORDS_DIR ROWS.jsonl... [--out check.json]

A reader writes one JSON line per record it was given (references/reader.md has the shape).
This reports, and exits 1 if any are found:

- missing: a record in RECORDS_DIR/batches.jsonl with no row; duplicate: a record with two rows;
+ bad quote: an `instruction`, `action` or `pushback` of a finding, or the `quote` of a pattern,
  that is not in the record text (or, for an instruction with `instruction_source` naming a
  file, in that file). Whitespace runs count as one space; `...` or `…` in a quote splits it into
  pieces that must appear in order; a quote is also matched against the record with JSON escapes
  undone (`\n` a space, `\"` a quote), since a message quoted inside a tool payload or a Dispatch
  envelope is stored escaped, and a quote of his that spans a newline would otherwise be missed;
+ missing source: an `instruction_source` path that no file answers, so the quote could not be
  checked at all. This is not a bad quote: the usual cause is a stale path (`~/.claude/CLAUDE.md`
  for `~/.dotfiles/.claude/CLAUDE.md`), and the fix is the path, not the finding;
+ pushback not Sami's: a `pushback` that is in the record but not inside one of his messages.
  His words in a record are: the `## SAMI'S MESSAGE` section (its `### The Dispatch envelope it
  arrived in` subsection included — a Dispatch message's words are in the envelope, and that
  section's body is then empty), the `## His message (verbatim)` section of a Dispatch-only
  record, and every `[L… ] SAMI:` block.

A finding with `pushback: null` is counted as `unpushed`: a failure Sami did not push back on.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# The headings build-records.py writes. A section of his words runs to the next one of these, so a
# heading inside a message of his (pasted markdown) does not cut it short.
SECTION = r"^## (?:SAMI'S MESSAGE|His message|Sami's previous message|Before:|After:|The issue thread)"
SAMI_SECTION = re.compile(rf"^## (?:SAMI'S MESSAGE|His message)[^\n]*\n(.*?)(?={SECTION}|\Z)", re.M | re.S)
SAMI_LINE = re.compile(rf"^\[L\d+ [^\]]*\] SAMI:\n(.*?)(?=^\[L\d+|{SECTION}|\Z)", re.M | re.S)
ESCAPE = re.compile(r"\\[ntr\"'\\]")
ESCAPED = {"\\n": " ", "\\t": " ", "\\r": " ", '\\"': '"', "\\'": "'", "\\\\": "\\"}


def norm(s: str) -> str:
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", s).strip().strip('"').strip()


def unescape(s: str) -> str:
    """A record carries a message quoted inside a tool payload or an envelope with JSON escapes."""
    return ESCAPE.sub(lambda m: ESCAPED[m.group(0)], s)


def variants(s: str) -> tuple[str, ...]:
    """The text as written and with its escapes undone, both normalised; duplicates dropped."""
    out = [norm(s)]
    plain = norm(unescape(s))
    if plain != out[0]:
        out.append(plain)
    return tuple(out)


def sami_words(raw: str) -> str:
    """Everything in a record that is Sami's own words, in record order."""
    blocks = [(m.start(), m.group(1)) for m in SAMI_SECTION.finditer(raw)]
    blocks += [(m.start(), m.group(1)) for m in SAMI_LINE.finditer(raw)]
    return " ".join(b for _, b in sorted(blocks))


def found(quote: str, text: str | tuple[str, ...]) -> bool:
    """Is the quote in the text? Either side may carry escapes, so try every pair of variants."""
    texts = (text,) if isinstance(text, str) else text
    return any(_in(q, t) for q in variants(quote) for t in texts)


def _in(quote: str, text: str) -> bool:
    pos = 0
    for piece in re.split(r"\.\.\.|…", quote):
        piece = piece.strip()
        if not piece:
            continue
        i = text.find(piece, pos)
        if i < 0:
            return False
        pos = i + len(piece)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("records_dir")
    ap.add_argument("rows", nargs="+")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d = Path(a.records_dir)
    expected = {}
    for line in open(d / "batches.jsonl"):
        b = json.loads(line)
        for rid, path in zip(b["records"], b["paths"]):
            expected[rid] = (b["batch"], path)
    rows: dict[str, dict] = {}
    dup, unknown = [], []
    for p in a.rows:
        for line in open(p):
            if not line.strip():
                continue
            r = json.loads(line)
            rid = r.get("record")
            if rid not in expected:
                unknown.append(rid)
            elif rid in rows:
                dup.append(rid)
            else:
                rows[rid] = r
    missing = sorted(set(expected) - set(rows))
    bad, not_sami, no_src = [], [], []
    n_find = n_unpushed = n_pat = 0
    for rid, r in rows.items():
        raw = Path(expected[rid][1]).read_text()
        text = variants(raw)
        sami = variants(sami_words(raw))
        for k, f in enumerate(r.get("findings") or []):
            n_find += 1
            for key in ("instruction", "action", "pushback"):
                q = f.get(key)
                if q is None and key == "pushback":
                    n_unpushed += 1
                    continue
                src = text
                if key == "instruction" and f.get("instruction_source") not in (None, "", "record"):
                    path = Path(f["instruction_source"].split(":")[0]).expanduser()
                    if not path.is_file():
                        no_src.append({"record": rid, "finding": k, "source": f["instruction_source"], "quote": q})
                        continue
                    src = variants(path.read_text())
                if not q or not found(q, src):
                    bad.append({"record": rid, "finding": k, "field": key, "quote": q})
                elif key == "pushback" and not found(q, sami):
                    not_sami.append({"record": rid, "finding": k, "quote": q})
        for k, s in enumerate(r.get("sami_patterns") or []):
            n_pat += 1
            if not s.get("quote") or not found(s["quote"], sami):
                bad.append({"record": rid, "pattern": k, "field": "quote", "quote": s.get("quote")})
    report = {
        "records": len(expected), "rows": len(rows), "missing": missing, "duplicate": dup, "unknown": unknown,
        "findings": n_find, "unpushed": n_unpushed, "patterns": n_pat,
        "bad_quotes": bad, "pushback_not_sami": not_sami, "missing_source": no_src,
        "missing_batches": sorted({expected[m][0] for m in missing}),
    }
    out = Path(a.out) if a.out else d / "check.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"{len(rows)}/{len(expected)} records have a row; {len(missing)} missing "
          f"(batches {' '.join(report['missing_batches']) or '-'}); {n_find} findings ({n_unpushed} unpushed), "
          f"{n_pat} patterns; {len(bad)} quotes not found, {len(not_sami)} pushbacks outside Sami's words, "
          f"{len(no_src)} instruction sources missing -> {out}")
    sys.exit(1 if missing or dup or bad or not_sami or no_src else 0)


if __name__ == "__main__":
    main()
