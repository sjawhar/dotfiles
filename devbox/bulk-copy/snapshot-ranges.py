#!/usr/bin/env python3
"""Valid page ranges of a page-blob SAS, and the chunks still to fetch (dispatch://INFRA-993).

Usage: snapshot-ranges.py SAS_FILE SIZE W
Writes W/ranges.v2 (all valid ranges, `start end`, inclusive) and W/chunks.v2 (the parts of them
not yet covered by W/done, cut at 256 MiB). Get Page Ranges is asked per 8 GiB window, 32 at once,
and FOLLOWS NextMarker: a response can stop early and name a marker (the first run missed that and
left zero holes in the image). A window answered with a 5xx is split into 8, down to 4 MiB.
The SAS URL is read from SAS_FILE and never printed.
"""
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sas_file, size, W = sys.argv[1], int(sys.argv[2]), sys.argv[3]
url = open(sas_file).read().strip()
RX = re.compile(rb"<Start>(\d+)</Start><End>(\d+)</End>")
NM = re.compile(rb"<NextMarker>([^<]*)</NextMarker>")


def get(s, e, marker):
    u = url + "&comp=pagelist" + (("&marker=" + urllib.request.quote(marker)) if marker else "")
    req = urllib.request.Request(u, headers={"x-ms-version": "2021-08-06", "x-ms-range": f"bytes={s}-{e}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def ranges(s, e):
    out, marker = [], ""
    while True:
        body = None
        for attempt in range(3):
            try:
                body = get(s, e, marker)
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as ex:
                last = ex
                time.sleep(5 * (attempt + 1))
        if body is None:
            n = e - s + 1
            sub = n // 8
            if sub < 4 << 20:
                raise RuntimeError(f"pagelist failed at {s}: {type(last).__name__}")
            out = []  # restart this window as 8 parts
            k = s
            while k <= e:
                out += ranges(k, min(e, k + sub - 1))
                k += sub
            return out
        out += [(int(a), int(b)) for a, b in RX.findall(body)]
        m = NM.search(body)
        marker = m.group(1).decode() if m else ""
        if not marker:
            return out


win = 8 << 30
windows = [(s, min(size - 1, s + win - 1)) for s in range(0, size, win)]
with ThreadPoolExecutor(32) as ex:
    allr = sorted(r for part in ex.map(lambda w: ranges(*w), windows) for r in part)
with open(f"{W}/ranges.v2", "w") as fh:
    for a, b in allr:
        fh.write(f"{a} {b}\n")

# Subtract what is already fetched.
done = []
try:
    for ln in open(f"{W}/done"):
        a, b = map(int, ln.split()[:2])
        done.append((a, b))
except FileNotFoundError:
    pass
done.sort()
merged = []
for a, b in done:
    if merged and a <= merged[-1][1] + 1:
        merged[-1][1] = max(merged[-1][1], b)
    else:
        merged.append([a, b])
CH = 256 << 20
need, j = [], 0
for a, b in allr:
    cur = a
    while j < len(merged) and merged[j][1] < cur:
        j += 1
    k = j
    while cur <= b:
        if k < len(merged) and merged[k][0] <= cur:
            cur = merged[k][1] + 1
            k += 1
            continue
        stop = b if k >= len(merged) else min(b, merged[k][0] - 1)
        need.append((cur, stop))
        cur = stop + 1
with open(f"{W}/chunks.v2", "w") as fh:
    for a, b in need:
        while a <= b:
            x = min(b, a + CH - 1)
            fh.write(f"{a} {x}\n")
            a = x + 1
used = sum(b - a + 1 for a, b in allr)
missing = sum(b - a + 1 for a, b in need)
print(f"ranges={len(allr)} used_bytes={used} missing_bytes={missing} missing_chunks={sum(1 for _ in open(W + '/chunks.v2'))}")
