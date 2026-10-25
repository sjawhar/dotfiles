#!/usr/bin/env python3
"""Split the records into reader batches. Code assigns every record; no reader picks its own.

    assign-readers.py RECORDS_DIR [--max-records 12] [--max-bytes 250000] [--out batches.jsonl]

Reads RECORDS_DIR/records.jsonl (build-records.py). A session's records stay together and in time
order, so a reader sees how one exchange led to the next; a session larger than one batch is split
at the budget. Writes one JSON line per batch: {"batch": "B001", "records": [...ids], "paths":
[...], "bytes": n}. Every record lands in exactly one batch; the script fails if one does not.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("records_dir")
    ap.add_argument("--max-records", type=int, default=12)
    ap.add_argument("--max-bytes", type=int, default=250_000)
    ap.add_argument("--out", default=None, help="default RECORDS_DIR/batches.jsonl")
    a = ap.parse_args()
    d = Path(a.records_dir)
    recs = [json.loads(line) for line in open(d / "records.jsonl") if line.strip()]
    by_sid: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_sid[r["session_id"]].append(r)
    groups = sorted((sorted(v, key=lambda r: r["ts"]) for v in by_sid.values()), key=lambda v: v[0]["ts"])
    batches: list[list[dict]] = [[]]
    for group in groups:
        for r in group:
            cur = batches[-1]
            size = sum(x["bytes"] for x in cur)
            if cur and (len(cur) >= a.max_records or size + r["bytes"] > a.max_bytes):
                batches.append([])
            batches[-1].append(r)
    batches = [b for b in batches if b]
    assigned = [r["id"] for b in batches for r in b]
    if sorted(assigned) != sorted(r["id"] for r in recs) or len(set(assigned)) != len(assigned):
        raise SystemExit("assign-readers: a record is missing or assigned twice")
    out = Path(a.out) if a.out else d / "batches.jsonl"
    with open(out, "w") as f:
        for n, b in enumerate(batches, 1):
            f.write(json.dumps({"batch": f"B{n:03d}", "records": [r["id"] for r in b],
                                "paths": [r["path"] for r in b], "bytes": sum(r["bytes"] for r in b)}) + "\n")
    print(f"{len(recs)} records in {len(batches)} batches -> {out}")


if __name__ == "__main__":
    main()
