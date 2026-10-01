#!/usr/bin/env python3
"""Time-boxed disk attribution: where did the space go?

Sibling directories are sized in batches by one `du -sx` each, several batches
at a time. du prints each argument's total as soon as it finishes that
argument, in argument order, so when a batch reaches its deadline every total
already printed is exact, the directory du was still inside is large by
definition, and the arguments after it were never started. The large one is
split into its children, which are batched and sized the same way; the
unstarted ones are queued again. Small directories are settled within seconds,
large ones surface as splits straight away, the walking is du's own native
code, and nothing waits for one walk of everything.

Sizes are allocated blocks (what df counts), each du stays on the argument's
filesystem, and du counts a hardlinked inode once within one invocation. A
file hardlinked into two different batches is counted in both, so compare the
roll-up with df for the exact total.

Output lines on stdout, flushed as they happen:
  SIZE   <bytes>  <path>              finished, at or above --min-size
  SPLIT  <path>  subdirs=<n>          still being walked at the deadline; children queued
  WAIT   io full avg10=<x>            new batches paused on IO pressure
--out FILE also records every finished directory and split, whatever its size
(tab-separated `SIZE <bytes> <path>` / `SPLIT <path> <direct bytes> <subdirs>`),
flushed at least once a second, so partial totals can be summed mid-run. At the
end, a roll-up tree of every node at or above --min-size.

Run it as root to read every directory, and under a supervisor (systemd-run)
when it may outlive a tool call.
"""

from __future__ import annotations

import argparse
import os
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import TextIO


@dataclass
class Node:
    path: str
    size: int | None = None  # exact size when du finished it
    files: int = 0  # direct entries (non-directories, subdirectory inodes), when split
    children: list["Node"] = field(default_factory=list)

    def total(self) -> int:
        if self.size is not None:
            return self.size
        return self.files + sum(c.total() for c in self.children)


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
        """Record a result; the file is flushed at least once a second."""
        if self.record is None:
            return
        with self.lock:
            print(*parts, sep="\t", file=self.record)
            if time.monotonic() - self.flushed >= 1.0:
                self.record.flush()
                self.flushed = time.monotonic()


def du_batch(paths: list[str], timeout: float) -> tuple[dict[str, int], list[str]]:
    """Size paths with one du; return (finished sizes, paths not finished, in order).

    paths must be non-empty: du given no path argument sizes the current directory and
    prints `.`, which is not a node of this walk (KeyError('.') mid-run, 2026-10-01).
    """
    if not paths:
        raise ValueError("du_batch needs at least one path; an empty batch would size the current directory")
    proc = subprocess.Popen(
        ["ionice", "-c2", "-n7", "du", "-sxB1", "--null", "--", *paths],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    buf = b""
    sizes: dict[str, int] = {}
    with selectors.DefaultSelector() as sel:
        sel.register(proc.stdout, selectors.EVENT_READ)
        eof = False
        while not eof:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if not sel.select(left):
                continue
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                eof = True
            buf += chunk
            *records, buf = buf.split(b"\0")
            for rec in records:
                size, _, path = rec.decode("utf-8", "surrogateescape").partition("\t")
                sizes[path] = int(size)
    if proc.poll() is None:
        proc.send_signal(signal.SIGKILL)
    proc.wait()
    proc.stdout.close()
    return sizes, [p for p in paths if p not in sizes]


def chunks(items: list[str], n: int) -> list[list[str]]:
    if not items:
        return []  # a directory with no subdirectories splits into no batches
    n = max(1, min(n, len(items)))
    return [items[i::n] for i in range(n)]


class Attribution:
    def __init__(self, args: argparse.Namespace, out: Output) -> None:
        self.args = args
        self.out = out
        self.min_size = parse_size(args.min_size)
        self.nodes: dict[str, Node] = {}

    def split(self, node: Node) -> list[str]:
        dev = os.lstat(node.path).st_dev
        subdirs: list[str] = []
        with os.scandir(node.path) as it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if st.st_dev != dev:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    # du counts a directory's own inode inside that directory's total.
                    child = Node(entry.path)
                    node.children.append(child)
                    self.nodes[child.path] = child
                    subdirs.append(child.path)
                else:
                    node.files += st.st_blocks * 512
        return sorted(subdirs)

    def run(self, roots: list[Node]) -> None:
        for root in roots:
            self.nodes[root.path] = root
        with ThreadPoolExecutor(max_workers=self.args.jobs) as pool:
            pending: dict[Future[tuple[dict[str, int], list[str]]], list[str]] = {}

            def submit(batch: list[str]) -> None:
                while (pressure := io_full_avg10()) > self.args.max_io_pressure:
                    self.out.show("WAIT", f"io full avg10={pressure:.1f}")
                    time.sleep(2)
                pending[pool.submit(du_batch, batch, self.args.timeout)] = batch

            for batch in chunks([r.path for r in roots], self.args.jobs):
                submit(batch)
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for fut in done:
                    pending.pop(fut)
                    sizes, unfinished = fut.result()
                    for path, size in sizes.items():
                        self.nodes[path].size = size
                        self.out.log("SIZE", size, path)
                        if size >= self.min_size:
                            self.out.show("SIZE", size, path)
                    if not unfinished:
                        continue
                    big, rest = unfinished[0], unfinished[1:]
                    node = self.nodes[big]
                    try:
                        subdirs = self.split(node)
                    except (FileNotFoundError, NotADirectoryError):
                        node.size = 0  # removed while it was being sized
                        subdirs = []
                    else:
                        self.out.log("SPLIT", big, node.files, len(subdirs))
                        self.out.show("SPLIT", big, f"subdirs={len(subdirs)}")
                    for batch in chunks(subdirs, self.args.jobs):
                        submit(batch)
                    if rest:
                        submit(rest)


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
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds per du batch before its current directory is split (default 10)")
    ap.add_argument("--jobs", type=int, default=8, help="du batches at once (default 8)")
    ap.add_argument("--max-io-pressure", type=float, default=20.0,
                    help="pause new batches while /proc/pressure/io full avg10 is above this (default 20)")
    ap.add_argument("--min-size", default="1G", help="smallest node shown on stdout and in the roll-up (default 1G)")
    ap.add_argument("--max-depth", type=int, default=6, help="deepest level of the roll-up (default 6)")
    ap.add_argument("--out", help="also record every finished directory and split, whatever its size, to this TSV file")
    args = ap.parse_args()

    started = time.monotonic()
    roots = [Node(os.path.abspath(r)) for r in args.roots]
    record = open(args.out, "a", encoding="utf-8") if args.out else None
    try:
        Attribution(args, Output(record)).run(roots)
    finally:
        if record is not None:
            record.close()
    print(f"\n== roll-up ({time.monotonic() - started:.0f} s, timeout {args.timeout:g} s, {args.jobs} jobs)")
    for root in roots:
        print_tree(root, parse_size(args.min_size), 0, args.max_depth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
