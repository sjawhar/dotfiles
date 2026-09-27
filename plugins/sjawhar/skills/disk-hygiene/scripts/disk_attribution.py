#!/usr/bin/env python3
"""Time-boxed disk attribution: where did the space go?

Each directory is walked with a deadline, several at a time on threads. A walk
that finishes gives that directory's exact size and it is done. One that runs
out of time is large by definition: it reports the bytes it counted as a lower
bound and is split into its immediate children, which are walked the same way.
Small directories drop out within seconds, the large ones surface as splits
straight away, and nothing waits for one walk of everything.

Sizes are allocated blocks (what df counts), and each walk stays on the root's
filesystem. A file with several hardlinks is counted once: a finished walk
claims the multiply-linked inodes it saw, and an inode another finished walk
already claimed is subtracted from it. So the roll-up total is exact, and a
shared inode is attributed to whichever subtree finished first.

New walks wait while /proc/pressure/io "full avg10" is above --max-io-pressure,
so the thread pool backs off when the box's IO is saturated.

Output lines on stdout, flushed as they happen:
  SIZE   <bytes>  <path>                          finished, at or above --min-size
  SPLIT  <path>  at_least=<GB> subdirs=<n>        ran out of time after counting at_least; children queued
  WAIT   io full avg10=<x>                        new walks paused on IO pressure
--out FILE also records every finished walk and every split, whatever its
size, as tab-separated `SIZE <bytes> <path>` / `SPLIT <path> <at_least bytes>
<subdirs>`, so partial per-directory totals can be summed while the run is
still going. At the end, a roll-up tree of every node at or above --min-size.

Run it as root to read every directory, and under a supervisor (systemd-run)
when it may outlive a tool call.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import TextIO


@dataclass
class Node:
    path: str
    size: int | None = None  # exact size when the walk finished
    files: int = 0  # direct entries (non-directories, subdirectory inodes), when split
    children: list["Node"] = field(default_factory=list)

    def total(self) -> int:
        if self.size is not None:
            return self.size
        return self.files + sum(c.total() for c in self.children)


class Timeout(Exception):
    pass


def io_full_avg10() -> float:
    with open("/proc/pressure/io") as f:
        for line in f:
            if line.startswith("full"):
                return float(line.split()[1].split("=")[1])
    raise RuntimeError("/proc/pressure/io has no 'full' line")


class Output:
    def __init__(self, record: TextIO | None) -> None:
        self.lock = threading.Lock()
        self.record = record
        self.flushed = time.monotonic()

    def show(self, *parts: object) -> None:
        with self.lock:
            print(*parts, sep="\t", flush=True)

    def log(self, *parts: object) -> None:
        """Record a result; the file is flushed at most once a second, so readers see it within a second."""
        if self.record is None:
            return
        with self.lock:
            print(*parts, sep="\t", file=self.record)
            if time.monotonic() - self.flushed >= 1.0:
                self.record.flush()
                self.flushed = time.monotonic()


class Sizer:
    def __init__(self, timeout: float, dev: int) -> None:
        self.timeout = timeout
        self.dev = dev
        self.claimed: set[tuple[int, int]] = set()
        self.lock = threading.Lock()

    def claim(self, links: dict[tuple[int, int], int]) -> int:
        """Claim a finished walk's hardlinked inodes; return bytes already claimed elsewhere."""
        dup = 0
        with self.lock:
            for key, blocks in links.items():
                if key in self.claimed:
                    dup += blocks
                else:
                    self.claimed.add(key)
        return dup

    def walk(self, path: str) -> tuple[int | None, int]:
        """(exact size, bytes counted); size is None when the deadline passed first."""
        deadline = time.monotonic() + self.timeout
        total = 0
        links: dict[tuple[int, int], int] = {}
        stack = [path]
        try:
            while stack:
                if time.monotonic() > deadline:
                    raise Timeout
                d = stack.pop()
                try:
                    it = os.scandir(d)
                except (FileNotFoundError, NotADirectoryError):
                    continue
                with it:
                    for entry in it:
                        try:
                            st = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if st.st_dev != self.dev:
                            continue
                        blocks = st.st_blocks * 512
                        if stat.S_ISDIR(st.st_mode):
                            total += blocks
                            stack.append(entry.path)
                        elif st.st_nlink > 1:
                            links[(st.st_dev, st.st_ino)] = blocks
                        else:
                            total += blocks
        except Timeout:
            return None, total + sum(links.values())
        return total + sum(links.values()) - self.claim(links), total

    def split(self, node: Node) -> None:
        links: dict[tuple[int, int], int] = {}
        with os.scandir(node.path) as it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if st.st_dev != self.dev:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    node.children.append(Node(entry.path))
                    node.files += st.st_blocks * 512  # the subdirectory's own inode
                elif st.st_nlink > 1:
                    links[(st.st_dev, st.st_ino)] = st.st_blocks * 512
                else:
                    node.files += st.st_blocks * 512
        node.files += sum(links.values()) - self.claim(links)


def run(roots: list[Node], args: argparse.Namespace, out: Output) -> None:
    min_size = parse_size(args.min_size)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending: dict[Future[tuple[int | None, int]], tuple[Node, Sizer]] = {}

        def submit(node: Node, sizer: Sizer) -> None:
            while (pressure := io_full_avg10()) > args.max_io_pressure:
                out.show("WAIT", f"io full avg10={pressure:.1f}")
                time.sleep(2)
            pending[pool.submit(sizer.walk, node.path)] = (node, sizer)

        for root in roots:
            submit(root, Sizer(args.timeout, os.lstat(root.path).st_dev))
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                node, sizer = pending.pop(fut)
                node.size, counted = fut.result()
                if node.size is not None:
                    out.log("SIZE", node.size, node.path)
                    if node.size >= min_size:
                        out.show("SIZE", node.size, node.path)
                    continue
                sizer.split(node)
                out.log("SPLIT", node.path, counted, len(node.children))
                out.show("SPLIT", node.path, f"at_least={counted / 1e9:.1f}GB", f"subdirs={len(node.children)}")
                for child in node.children:
                    submit(child, sizer)


def print_tree(node: Node, min_size: int, depth: int, max_depth: int) -> None:
    total = node.total()
    if total < min_size:
        return
    mark = "" if node.size is not None else "  (split)"
    print(f"{total / 1e9:8.1f} GB  {'  ' * depth}{node.path}{mark}")
    if depth >= max_depth:
        return
    for child in sorted(node.children, key=lambda c: c.total(), reverse=True):
        print_tree(child, min_size, depth + 1, max_depth)
    if node.size is None and node.files >= min_size:
        print(f"{node.files / 1e9:8.1f} GB  {'  ' * (depth + 1)}{node.path}/<entries directly in this directory>")


def parse_size(text: str) -> int:
    units = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
    suffix = text[-1].upper()
    return int(float(text[:-1]) * units[suffix]) if suffix in units else int(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="directories to attribute (each stays on its own filesystem)")
    ap.add_argument("--timeout", type=float, default=5.0, help="seconds per walk before a directory is split (default 5)")
    ap.add_argument("--jobs", type=int, default=8, help="walks at once (default 8)")
    ap.add_argument("--max-io-pressure", type=float, default=20.0,
                    help="pause new walks while /proc/pressure/io full avg10 is above this (default 20)")
    ap.add_argument("--min-size", default="1G", help="smallest node shown on stdout and in the roll-up (default 1G)")
    ap.add_argument("--max-depth", type=int, default=6, help="deepest level of the roll-up (default 6)")
    ap.add_argument("--out", help="also record every finished walk and split, whatever its size, to this TSV file")
    args = ap.parse_args()

    started = time.monotonic()
    roots = [Node(os.path.abspath(r)) for r in args.roots]
    record = open(args.out, "a", encoding="utf-8") if args.out else None
    try:
        run(roots, args, Output(record))
    finally:
        if record is not None:
            record.close()
    print(f"\n== roll-up ({time.monotonic() - started:.0f} s, timeout {args.timeout:g} s, {args.jobs} jobs)")
    for root in roots:
        print_tree(root, parse_size(args.min_size), 0, args.max_depth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
