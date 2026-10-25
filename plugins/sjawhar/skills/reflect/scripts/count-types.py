#!/usr/bin/env python3
"""Hand checked findings to the coder, then count failure types across sessions.

    count-types.py export RECORDS_DIR ROWS.jsonl... --out findings.jsonl
    count-types.py count RECORDS_DIR findings.jsonl types.jsonl [--prior LAST/counts.json] --out counts.json

`export` writes one line per finding and per Sami pattern, with an id (`M0012#0` for finding 0
of record M0012, `M0012~0` for pattern 0), its session, its open code and its quotes. The coder
(references/coder.md) reads only that file and writes types.jsonl: one line per id,
{"id": ..., "type": ...}. `count` fails if an id is left without a type, then counts per type:
findings, records, distinct sessions, how many Sami pushed back on, and example ids. A type is
`recurs` when it holds findings from at least 2 sessions; only those may get a fix. With
--prior, each type also gets last run's rate and this run's, per 100 records, for the
"did last run's fixes hold" table. Patterns (ids with `~`) are counted the same way, and are
`enough` only from 6 sessions up.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def load_rows(paths: list[str]) -> dict[str, dict]:
    rows = {}
    for p in paths:
        for line in open(p):
            if line.strip():
                r = json.loads(line)
                rows.setdefault(r["record"], r)
    return rows


def export(a: argparse.Namespace) -> None:
    recs = {r["id"]: r for r in map(json.loads, open(Path(a.records_dir) / "records.jsonl"))}
    rows = load_rows(a.rows)
    with open(a.out, "w") as f:
        for rid, r in sorted(rows.items()):
            sid = recs[rid]["session_id"]
            for k, x in enumerate(r.get("findings") or []):
                f.write(json.dumps({"id": f"{rid}#{k}", "session_id": sid, **x}, ensure_ascii=False) + "\n")
            for k, x in enumerate(r.get("sami_patterns") or []):
                f.write(json.dumps({"id": f"{rid}~{k}", "session_id": sid, **x}, ensure_ascii=False) + "\n")
    print(f"exported findings and patterns of {len(rows)} records -> {a.out}")


def count(a: argparse.Namespace) -> None:
    n_records = sum(1 for _ in open(Path(a.records_dir) / "records.jsonl"))
    items = {x["id"]: x for x in map(json.loads, open(a.findings))}
    types = {x["id"]: x["type"] for x in map(json.loads, open(a.types))}
    untyped = sorted(set(items) - set(types))
    if untyped:
        sys.exit(f"count-types: {len(untyped)} ids have no type, e.g. {untyped[:5]}")
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for i, x in items.items():
        groups[("pattern" if "~" in i else "failure", types[i])].append(x)
    prior = json.loads(Path(a.prior).read_text()) if a.prior else None
    prior_rate = {}
    if prior:
        for t in prior["types"]:
            prior_rate[(t["kind"], t["type"])] = t["per_100_records"]
    out = []
    for (kind, t), xs in groups.items():
        sessions = {x["session_id"] for x in xs}
        recs = {x["id"].split("#")[0].split("~")[0] for x in xs}
        row = {
            "kind": kind, "type": t, "items": len(xs), "records": len(recs), "sessions": len(sessions),
            "per_100_records": round(100 * len(recs) / n_records, 2),
            "examples": [x["id"] for x in xs[:6]],
        }
        if kind == "failure":
            row["pushed_back"] = sum(1 for x in xs if x.get("pushback"))
            row["recurs"] = len(sessions) >= 2
        else:
            row["enough"] = len(sessions) >= 6
        if prior is not None:
            row["prior_per_100_records"] = prior_rate.get((kind, t))
        out.append(row)
    out.sort(key=lambda r: (r["kind"], -r["sessions"], -r["records"]))
    Path(a.out).write_text(json.dumps({"records": n_records, "types": out}, indent=1, ensure_ascii=False))
    for r in out:
        flag = ("recurs" if r.get("recurs") else "once") if r["kind"] == "failure" else ("enough" if r["enough"] else "few")
        prev = f"  prior {r['prior_per_100_records']}" if prior is not None else ""
        print(f"{r['kind']:8} {r['sessions']:3} sessions {r['records']:4} records {flag:7} "
              f"{r['per_100_records']:6}/100{prev}  {r['type']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("records_dir")
    e.add_argument("rows", nargs="+")
    e.add_argument("--out", required=True)
    c = sub.add_parser("count")
    c.add_argument("records_dir")
    c.add_argument("findings")
    c.add_argument("types")
    c.add_argument("--prior")
    c.add_argument("--out", required=True)
    a = ap.parse_args()
    export(a) if a.cmd == "export" else count(a)


if __name__ == "__main__":
    main()
